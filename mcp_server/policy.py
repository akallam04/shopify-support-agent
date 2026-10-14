"""Store policy as code: the eligibility rules behind every write, taken from data/policies."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from mcp_server.clock import parse_instant, store_date
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
FEE_WAIVED_REASONS = frozenset({"not_as_described", "wrong_item"})
DAMAGE_REASONS = frozenset({"defective"})
USED_CONDITIONS = frozenset({"worn", "washed", "used", "tags_removed"})
OPENED_CONDITIONS = frozenset({"opened"})

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
CA_PROVINCES = frozenset("AB BC MB NB NL NS NT NU ON PE QC SK YT".split())
PROVINCES = {"US": US_STATES, "CA": CA_PROVINCES}
PROVINCE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
    "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon",
}
POSTAL_CODE_RE = {
    "US": re.compile(r"^\d{5}(-\d{4})?$"),
    "CA": re.compile(r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z] ?\d[ABCEGHJ-NPRSTV-Z]\d$", re.IGNORECASE),
}
USED_ITEM_REASON = (
    "Items that have been worn, washed, or used, or that no longer have their tags, cannot be returned. "
    "Store policy accepts only unworn, unused items with original tags and packaging."
)
OPENED_ITEM_REASON = "Ski wax and other consumables cannot be returned once they have been opened."
POSTAL_CODE_HELP = {
    "US": "US ZIP codes are 5 digits, or 5 digits, a hyphen, and 4 more, like 80204 or 80204-1234",
    "CA": "Canadian postal codes look like A1A 1A1",
}

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


def payment_pending(order: Order) -> bool:
    return (order.financial_status or "").upper() == "PENDING"


def order_change(order: Order, now: datetime, verb: str) -> Decision:
    if order.cancelled_at:
        return refuse("already_cancelled", f"Order {order.name} is already cancelled.")
    if has_shipped(order):
        return refuse(
            "already_shipped",
            f"Order {order.name} has already shipped, so it can no longer be {verb}. {AFTER_SHIPPING}",
        )
    if now - placed_at(order) > ORDER_CHANGE_WINDOW:
        if payment_pending(order):
            return refuse(
                "change_window_passed",
                f"Order {order.name} was placed more than 2 hours ago, so it can no longer be {verb} here. "
                f"Orders can only be {verb} within 2 hours of being placed.",
            )
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
    country = address.country_code.upper()
    if address.province_code.upper() not in PROVINCES[country]:
        place = "a US state" if country == "US" else "a Canadian province or territory"
        return refuse("invalid_province", f"{address.province_code} is not {place} code. Please give the two-letter code, like CO or BC.")
    if not POSTAL_CODE_RE[country].match(address.zip.strip()):
        return refuse("invalid_postal_code", f"{address.zip} is not a valid postal code: {POSTAL_CODE_HELP[country]}.")
    return ALLOWED


def delivered_at(order: Order, line: LineItem) -> datetime | None:
    for fulfillment in order.fulfillments:
        if fulfillment.delivered_at and any(fl.line_item_id == line.line_item_id for fl in fulfillment.line_items):
            return parse_instant(fulfillment.delivered_at)
    return None


def condition_refusal(condition: str | None) -> Decision | None:
    if condition in USED_CONDITIONS:
        return refuse("item_used", USED_ITEM_REASON)
    if condition in OPENED_CONDITIONS:
        return refuse("opened_consumable", OPENED_ITEM_REASON)
    return None


def delivered_quantity(order: Order, line: LineItem) -> int:
    return sum(
        fl.quantity
        for fulfillment in order.fulfillments
        if fulfillment.delivered_at
        for fl in fulfillment.line_items
        if fl.line_item_id == line.line_item_id
    )


def already_requested(order: Order, line: LineItem) -> int:
    return sum(rl.quantity for r in order.returns for rl in r.line_items if rl.line_item_id == line.line_item_id)


def can_return(
    order: Order,
    requested: list[tuple[LineItem, int]],
    products: dict[str, Product],
    reason: str,
    now: datetime,
    condition: str = "",
) -> Decision:
    if reason not in RETURN_REASONS:
        return refuse("invalid_reason", f"The return reason must be one of: {', '.join(RETURN_REASONS)}.")
    if order.cancelled_at:
        return refuse("already_cancelled", f"Order {order.name} was cancelled, so there is nothing to return.")
    if not requested:
        return refuse("no_items", "Tell me which items from the order you would like to return.")
    if reason in DAMAGE_REASONS:
        return refuse(
            "damaged_or_defective",
            "Items that arrived damaged or became defective go through a separate process, not a standard "
            "return: email photos of the item and its packaging to support@auroraoutfitters.com within 7 days "
            "of delivery, and the store replaces it or refunds you in full.",
        )
    refusal = condition_refusal(condition)
    if refusal is not None:
        return refusal
    for line, quantity in requested:
        product = products.get(line.product_id or "")
        if product is None or product.tags is None:
            return refuse(
                "product_unverified",
                f"I could not check the product details for {line.title}, so I cannot confirm it can be returned. "
                "Please email support@auroraoutfitters.com and the team will check it.",
            )
        if product.is_gift_card:
            return refuse("gift_card", "Gift cards cannot be returned or refunded.")
        if FINAL_SALE_TAG in {t.lower() for t in product.tags}:
            return refuse(
                "final_sale",
                f"Final sale items cannot be returned or exchanged, and order {order.name} lists the {line.title} as final sale.",
            )
        delivered = delivered_at(order, line)
        if delivered is None:
            return refuse(
                "not_delivered",
                f"Order {order.name} has not been delivered yet, so the {line.title} cannot be returned until it arrives.",
            )
        if now - delivered > RETURN_WINDOW:
            return refuse(
                "return_window_passed",
                f"The 30-day return window for the {line.title} on order {order.name} ended on "
                f"{store_date(delivered + RETURN_WINDOW)}, 30 days after delivery on {store_date(delivered)}.",
            )
        requested_before = already_requested(order, line)
        returnable = delivered_quantity(order, line) - requested_before
        if quantity < 1 or quantity > returnable:
            if requested_before:
                remaining = "nothing left to return" if returnable <= 0 else f"only {returnable} more can be returned"
                return refuse(
                    "quantity_exceeds",
                    f"A return was already requested for {line.title} on order {order.name}, so {remaining}.",
                )
            return refuse(
                "quantity_exceeds",
                f"Order {order.name} has {max(returnable, 0)} of {line.title} delivered and available to return.",
            )
    return ALLOWED


def label_fee_applies(reason: str, country_code: str | None) -> bool:
    return (country_code or "").upper() != "CA" and reason not in FEE_WAIVED_REASONS


def refund_amount(items_total: Decimal, reason: str, country_code: str | None) -> Decimal:
    return items_total - (RETURN_SHIPPING_FEE if label_fee_applies(reason, country_code) else Decimal("0"))


def refund_text(items_total: Decimal, reason: str, country_code: str | None, currency: str = "USD") -> str:
    refund = refund_amount(items_total, reason, country_code)
    if refund == items_total:
        return f"Refund for these items: {items_total:.2f} {currency}."
    return f"Refund for these items: {refund:.2f} {currency} ({items_total:.2f} {currency} minus the {RETURN_SHIPPING_FEE} {currency} label fee)."


def return_terms(reason: str, country_code: str | None, items_total: Decimal | None = None, currency: str = "USD") -> dict[str, str]:
    if (country_code or "").upper() == "CA":
        fee = "No return shipping fee is deducted, because Canadian returns do not use our prepaid label."
        label = "Canadian returns ship at the customer's own cost to our Denver warehouse."
    else:
        fee = (
            "No return shipping fee, because the item did not arrive as ordered or as described."
            if reason in FEE_WAIVED_REASONS
            else f"A flat {RETURN_SHIPPING_FEE} USD fee for the prepaid return label is deducted from the refund."
        )
        label = "We email a prepaid return label within 1 business day."
    terms = {
        "fee": fee,
        "label": label,
        "refund": "The refund goes to the original payment method within 5 to 7 business days after "
        "we receive and inspect the return.",
        "approval": "The store confirms the return once it reviews the request.",
    }
    if items_total is not None:
        terms["amount"] = refund_text(items_total, reason, country_code, currency)
    return terms
