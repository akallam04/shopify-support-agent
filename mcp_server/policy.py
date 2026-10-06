"""Store policy as code: the eligibility rules behind every write, taken from data/policies."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from mcp_server.clock import parse_instant
from mcp_server.simdb import Address, LineItem, Order, Product

ORDER_CHANGE_WINDOW = timedelta(hours=2)
RETURN_WINDOW = timedelta(days=30)
FINAL_SALE_TAG = "final-sale"
SHIP_TO_COUNTRIES = frozenset({"US", "CA"})
RETURN_SHIPPING_FEE = Decimal("7.50")

CANCEL_REASONS = ("changed_mind", "ordered_by_mistake", "found_better_price", "delivery_too_slow", "other")
RETURN_REASONS = (
    "size_too_small",
    "size_too_large",
    "unwanted",
    "not_as_described",
    "wrong_item",
    "defective",
    "style",
    "color",
    "other",
)
RETURN_REASON_LABELS = {
    "size_too_small": "too small",
    "size_too_large": "too large",
    "unwanted": "no longer wanted",
    "not_as_described": "not as described",
    "wrong_item": "wrong item received",
    "defective": "defective",
    "style": "style",
    "color": "color",
    "other": "other",
}
FEE_WAIVED_REASONS = frozenset({"defective", "not_as_described", "wrong_item"})

AFTER_SHIPPING = "Once it arrives, you can return it within 30 days of delivery."


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    reason: str


ALLOWED = Decision(True, "ok", "")


def refuse(code: str, reason: str) -> Decision:
    return Decision(False, code, reason)


def placed_at(order: Order) -> datetime:
    return parse_instant(order.processed_at)


def has_shipped(order: Order) -> bool:
    return order.fulfillment_status != "UNFULFILLED" or bool(order.fulfillments)


def order_change(order: Order, now: datetime, verb: str) -> Decision:
    if order.cancelled_at:
        return refuse("already_cancelled", f"Order {order.name} is already cancelled.")
    if has_shipped(order):
        return refuse(
            "already_shipped",
            f"Order {order.name} has already shipped, so it can no longer be {verb}. {AFTER_SHIPPING}",
        )
    if now - placed_at(order) > ORDER_CHANGE_WINDOW:
        return refuse(
            "change_window_passed",
            f"Order {order.name} was placed more than 2 hours ago, so it has entered processing and "
            f"will ship as placed. Orders can only be {verb} within 2 hours of being placed. {AFTER_SHIPPING}",
        )
    return ALLOWED


def can_cancel(order: Order, reason: str, now: datetime) -> Decision:
    if reason and reason not in CANCEL_REASONS:
        return refuse("invalid_reason", f"The cancellation reason, if given, must be one of: {', '.join(CANCEL_REASONS)}.")
    return order_change(order, now, "cancelled")


def can_change_address(order: Order, address: Address, now: datetime) -> Decision:
    decision = order_change(order, now, "changed")
    if not decision.allowed:
        return decision
    if (address.country_code or "").upper() not in SHIP_TO_COUNTRIES:
        return refuse(
            "unsupported_destination",
            "We currently ship only to the United States and Canada, so the order cannot be sent there.",
        )
    if not all((address.address1, address.city, address.province_code, address.zip)):
        return refuse(
            "incomplete_address",
            "A shipping address needs a street address, city, state or province, postal code, and country.",
        )
    return ALLOWED


def delivered_at(order: Order, line: LineItem) -> datetime | None:
    for fulfillment in order.fulfillments:
        if fulfillment.delivered_at and any(fl.line_item_id == line.line_item_id for fl in fulfillment.line_items):
            return parse_instant(fulfillment.delivered_at)
    return None


def already_requested(order: Order, line: LineItem) -> int:
    return sum(rl.quantity for r in order.returns for rl in r.line_items if rl.line_item_id == line.line_item_id)


def can_return(
    order: Order,
    requested: list[tuple[LineItem, int]],
    products: dict[str, Product],
    reason: str,
    now: datetime,
) -> Decision:
    if reason not in RETURN_REASONS:
        return refuse("invalid_reason", f"The return reason must be one of: {', '.join(RETURN_REASONS)}.")
    if order.cancelled_at:
        return refuse("already_cancelled", f"Order {order.name} was cancelled, so there is nothing to return.")
    if not requested:
        return refuse("no_items", "Tell me which items from the order you would like to return.")
    for line, quantity in requested:
        product = products.get(line.product_id or "")
        if product is not None and product.is_gift_card:
            return refuse("gift_card", "Gift cards cannot be returned or refunded.")
        if product is not None and FINAL_SALE_TAG in {t.lower() for t in product.tags}:
            return refuse(
                "final_sale",
                f"{line.title} was sold as FINAL SALE, and final sale items cannot be returned or exchanged.",
            )
        delivered = delivered_at(order, line)
        if delivered is None:
            return refuse(
                "not_delivered",
                f"{line.title} from order {order.name} has not been delivered yet. Returns open once it arrives.",
            )
        if now - delivered > RETURN_WINDOW:
            return refuse(
                "return_window_passed",
                f"{line.title} was delivered on {delivered.strftime('%B %-d, %Y')}, more than 30 days ago, "
                "so it is outside our 30-day return window.",
            )
        requested_before = already_requested(order, line)
        if quantity >= 1 and requested_before and quantity + requested_before > line.quantity:
            left = line.quantity - requested_before
            remaining = "nothing left to return" if left <= 0 else f"only {left} more can be returned"
            return refuse(
                "quantity_exceeds",
                f"A return was already requested for {line.title} on order {order.name}, so {remaining}.",
            )
        if quantity < 1 or quantity + requested_before > line.quantity:
            return refuse(
                "quantity_exceeds",
                f"Order {order.name} has {line.quantity - already_requested(order, line)} of {line.title} "
                "available to return.",
            )
    return ALLOWED


def return_terms(reason: str, country_code: str | None) -> dict[str, str]:
    fee = (
        "No return shipping fee, because the item arrived damaged, defective, or not as ordered."
        if reason in FEE_WAIVED_REASONS
        else f"A flat {RETURN_SHIPPING_FEE} USD return shipping fee is deducted from the refund."
    )
    label = (
        "Canadian returns ship at the customer's own cost to our Denver warehouse."
        if (country_code or "").upper() == "CA"
        else "We email a prepaid return label within 1 business day."
    )
    return {
        "fee": fee,
        "label": label,
        "refund": "The refund goes to the original payment method within 5 to 7 business days after "
        "we receive and inspect the return.",
        "approval": "The return is requested, and the store confirms it once it is reviewed.",
    }
