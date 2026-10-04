"""Support tools over any store backend: validated inputs, shared authorization, honest not-found.

Two failure modes on purpose: bad input raises ToolInputError (the caller sent
garbage and should fix its arguments), while a clean lookup with no match returns
found=False (the honest answer a customer gets). A wrong email on a real order
returns the same not-found shape as a missing order, so order numbers cannot be
probed for existence. Authorization lives here, above the backend, so the live
and simulated stores enforce it identically.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from mcp_server import policy
from mcp_server.backends.base import FoundOrder, StoreBackend, WritableStore
from mcp_server.clock import format_instant
from mcp_server.search import WORD_RE, prefix_hit, title_score, words
from mcp_server.simdb import Address, LineItem, Order, Product

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ORDER_NOT_FOUND = {
    "found": False,
    "message": "No order found matching that order number and email address.",
}

CUSTOMER_ORDER_LIMIT = 10
PRODUCT_CANDIDATE_LIMIT = 10
PRODUCT_RESULT_LIMIT = 3


class ToolInputError(ValueError):
    """Raised when a tool argument fails validation."""


def normalize_order_name(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits or len(digits) > 10:
        raise ToolInputError(
            "order_number must contain the order's digits, for example #1001"
        )
    return f"#{digits}"


def validate_email(raw: str) -> str:
    email = (raw or "").strip()
    if not EMAIL_RE.match(email):
        raise ToolInputError("email must be a valid email address")
    return email


def sanitize_search_text(raw: str) -> str:
    text = re.sub(r"[^A-Za-z0-9\s-]", " ", raw or "").strip()
    text = re.sub(r"\s+", " ", text)[:100]
    if not text:
        raise ToolInputError("product_query must contain some searchable text")
    return text


def owns_order(found: FoundOrder, email: str) -> bool:
    on_file = {
        e.lower()
        for e in (found.order.email, found.customer.email if found.customer else None)
        if e
    }
    return email.lower() in on_file


def _order_status_view(order: Order) -> dict[str, Any]:
    return {
        "found": True,
        "order_number": order.name,
        "placed_at": order.processed_at,
        "cancelled": order.cancelled_at is not None,
        "fulfillment_status": order.fulfillment_status,
        "financial_status": order.financial_status,
        "total": f"{order.total} {order.currency}",
        "items": [
            {"title": li.title, "variant": li.variant_title, "quantity": li.quantity} for li in order.line_items
        ],
        "tracking": [
            {"number": t.number, "carrier": t.company, "url": t.url}
            for f in order.fulfillments
            for t in f.tracking
        ],
    }


def _product_view(product: Product) -> dict[str, Any]:
    return {
        "title": product.title,
        "handle": product.handle,
        "variants": [
            {
                "option": v.title if v.title != "Default Title" else "Standard",
                "price": v.price,
                "available": v.available_for_sale,
                "quantity": v.inventory_quantity,
            }
            for v in product.variants.values()
        ],
    }


def get_order_status(backend: StoreBackend, order_number: str, email: str) -> dict[str, Any]:
    name = normalize_order_name(order_number)
    email = validate_email(email)
    found = backend.find_order(name)
    if found is None or not owns_order(found, email):
        return dict(ORDER_NOT_FOUND)
    return _order_status_view(found.order)


def list_customer_orders(backend: StoreBackend, email: str) -> dict[str, Any]:
    email = validate_email(email)
    orders = backend.orders_for_email(email, CUSTOMER_ORDER_LIMIT)
    if not orders:
        return {"found": False, "message": "No orders found for that email address."}
    return {
        "found": True,
        "count": len(orders),
        "orders": [
            {
                "order_number": o.name,
                "placed_at": o.processed_at,
                "fulfillment_status": o.fulfillment_status,
                "financial_status": o.financial_status,
                "total": f"{o.total} {o.currency}",
            }
            for o in orders
        ],
    }


def check_inventory(backend: StoreBackend, product_query: str) -> dict[str, Any]:
    text = sanitize_search_text(product_query)
    candidates = backend.search_products(text, PRODUCT_CANDIDATE_LIMIT)
    tokens = WORD_RE.findall(text.lower())
    ranked = sorted(candidates, key=lambda p: -title_score(tokens, p.title))
    products = ranked[:PRODUCT_RESULT_LIMIT]
    if not products:
        return {"found": False, "message": f"No products found matching '{text}'."}
    return {"found": True, "products": [_product_view(p) for p in products]}


WRITE_ACTIONS = ("cancel_order", "update_shipping_address", "request_return")
HANDOFF_ACTION = "transfer_to_human"
SUPPORT_HOURS = "Monday to Friday, 8 am to 5 pm Mountain Time, replying within 1 business day"


@dataclass(frozen=True)
class Prepared:
    action: str
    allowed: bool
    code: str
    reason: str
    args: dict[str, Any] = field(default_factory=dict)
    order_name: str | None = None
    summary: str = ""
    facts: dict[str, Any] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "order_number": self.order_name,
            "summary": self.summary,
            "facts": self.facts,
        }


def idempotency_key(action: str, args: dict[str, Any]) -> str:
    payload = json.dumps({"action": action, "args": args}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def format_address(address: Address | None) -> str:
    if address is None or not address.address1:
        return "no address on file"
    parts = [address.address1, address.address2, address.city, address.province_code, address.zip, address.country_code]
    return ", ".join(p for p in parts if p)


def _items_text(pairs: list[tuple[LineItem, int]]) -> str:
    return ", ".join(
        f"{qty} x {li.title}" + (f" ({li.variant_title})" if li.variant_title and li.variant_title != "Default Title" else "")
        for li, qty in pairs
    )


def _order_facts(order: Order) -> dict[str, Any]:
    return {
        "order_number": order.name,
        "placed_at": order.processed_at,
        "cancelled": order.cancelled_at is not None,
        "fulfillment_status": order.fulfillment_status,
        "financial_status": order.financial_status,
        "total": f"{order.total} {order.currency}",
        "shipping_address": format_address(order.shipping_address),
        "items": [
            {"title": li.title, "variant": li.variant_title, "quantity": li.quantity} for li in order.line_items
        ],
        "deliveries": [f.delivered_at for f in order.fulfillments if f.delivered_at],
    }


def _not_found(action: str) -> Prepared:
    return Prepared(action, False, "order_not_found", ORDER_NOT_FOUND["message"])


def _authorized_order(backend: StoreBackend, action: str, args: dict[str, Any]) -> tuple[FoundOrder | None, str, str]:
    name = normalize_order_name(str(args.get("order_number", "")))
    email = validate_email(str(args.get("email", "")))
    found = backend.find_order(name)
    if found is None or not owns_order(found, email):
        return None, name, email
    return found, name, email


def _resolve_items(order: Order, raw_items: Any) -> tuple[list[tuple[LineItem, int]], policy.Decision]:
    if not isinstance(raw_items, list) or not raw_items:
        return [], policy.ALLOWED
    resolved: list[tuple[LineItem, int]] = []
    for raw in raw_items:
        if not isinstance(raw, dict) or not str(raw.get("title", "")).strip():
            raise ToolInputError("each item needs a title, for example {\"title\": \"Stormline Rain Jacket\", \"quantity\": 1}")
        title = str(raw["title"]).strip().lower()
        variant = str(raw.get("variant") or "").strip().lower()
        try:
            quantity = int(raw.get("quantity", 1))
        except (TypeError, ValueError) as e:
            raise ToolInputError("item quantity must be a whole number") from e
        matches = [li for li in order.line_items if li.title.lower() == title]
        if not matches:
            wanted = words(title)
            matches = [li for li in order.line_items if wanted and all(prefix_hit(w, words(li.title)) for w in wanted)]
        if variant:
            matches = [li for li in matches if (li.variant_title or "").lower() == variant]
        if not matches:
            on_order = "; ".join(_items_text([(li, li.quantity)]) for li in order.line_items)
            return [], policy.refuse("item_not_found", f"Order {order.name} does not include that item. It has: {on_order}.")
        if len(matches) > 1:
            variants = ", ".join(li.variant_title or "standard" for li in matches)
            return [], policy.refuse("ambiguous_item", f"Order {order.name} has more than one {matches[0].title}: {variants}. Which one?")
        resolved.append((matches[0], quantity))
    return resolved, policy.ALLOWED


def _address_from(args: dict[str, Any], found: FoundOrder) -> Address:
    def text(key: str) -> str | None:
        value = str(args.get(key) or "").strip()
        return value or None

    existing = found.order.shipping_address
    customer = found.customer
    return Address(
        first_name=text("first_name")
        or (existing.first_name if existing and existing.first_name else None)
        or (customer.first_name if customer else None),
        last_name=text("last_name")
        or (existing.last_name if existing and existing.last_name else None)
        or (customer.last_name if customer else None),
        address1=text("address1"),
        address2=text("address2"),
        city=text("city"),
        province_code=(text("province_code") or "").upper() or None,
        zip=text("zip"),
        country_code=(text("country_code") or "").upper() or None,
    )


def prepare(backend: StoreBackend, action: str, args: dict[str, Any]) -> Prepared:
    if action == HANDOFF_ACTION:
        summary = str(args.get("summary") or "").strip()[:500]
        if not summary:
            raise ToolInputError("summary must describe what the customer needs")
        return Prepared(action, True, "ok", "", {"summary": summary, "order_number": args.get("order_number") or None})
    if action not in WRITE_ACTIONS:
        raise ToolInputError(f"unknown action {action}")

    found, name, email = _authorized_order(backend, action, args)
    if found is None:
        return _not_found(action)
    order = found.order
    now = backend.clock.now()
    facts = _order_facts(order)

    if action == "cancel_order":
        reason = str(args.get("reason") or "").strip()
        decision = policy.can_cancel(order, reason, now)
        normalized = {"order_number": name, "email": email.lower(), "reason": reason}
        summary = (
            f"cancel order {name} ({_items_text([(li, li.quantity) for li in order.line_items])}, "
            f"total {order.total} {order.currency}), with any amount paid going back to the original payment method"
        )
    elif action == "update_shipping_address":
        address = _address_from(args, found)
        decision = policy.can_change_address(order, address, now)
        normalized = {"order_number": name, "email": email.lower(), "address": address.model_dump()}
        summary = (
            f"change the shipping address on order {name} from {format_address(order.shipping_address)} "
            f"to {format_address(address)}"
        )
    else:
        reason = str(args.get("reason") or "").strip()
        pairs, decision = _resolve_items(order, args.get("items"))
        if decision.allowed:
            products = backend.products_by_id({li.product_id for li, _ in pairs if li.product_id})
            decision = policy.can_return(order, pairs, products, reason, now)
        terms = policy.return_terms(reason, order.shipping_address.country_code if order.shipping_address else None)
        normalized = {
            "order_number": name,
            "email": email.lower(),
            "reason": reason,
            "items": sorted([[li.line_item_id, qty] for li, qty in pairs]),
        }
        summary = f"request a return of {_items_text(pairs)} from order {name}. {terms['fee']} {terms['label']}"
        facts = {**facts, "return_terms": terms}

    if not decision.allowed:
        return Prepared(action, False, decision.code, decision.reason, normalized, name, "", facts)
    return Prepared(action, True, "ok", "", normalized, name, summary, facts)


def _audit(backend: WritableStore, action: str, prepared: Prepared, outcome: str, key: str) -> None:
    backend.audit.append(
        {
            "at": format_instant(backend.clock.now()),
            "action": action,
            "order_number": prepared.order_name,
            "outcome": outcome,
            "code": prepared.code,
            "key": key,
        }
    )


def execute(backend: StoreBackend, action: str, args: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    if not backend.supports_writes:
        raise ToolInputError("this store backend does not accept write actions")
    store: WritableStore = backend  # type: ignore[assignment]
    prepared = prepare(store, action, args)
    key = key or idempotency_key(action, prepared.args)
    if key in store.executed:
        _audit(store, action, prepared, "duplicate", key)
        return {**store.executed[key], "duplicate": True}
    if not prepared.allowed:
        _audit(store, action, prepared, "refused", key)
        return {"ok": False, "action": action, "code": prepared.code, "reason": prepared.reason}

    name = prepared.order_name
    if action == "cancel_order":
        store.cancel_order(name)
        result = {"message": f"Order {name} is cancelled. Any amount paid goes back to the original payment method."}
    elif action == "update_shipping_address":
        address = Address(**prepared.args["address"])
        store.update_shipping_address(name, address)
        result = {"message": f"The shipping address on order {name} is now {format_address(address)}."}
    elif action == "request_return":
        lines = [(lid, qty) for lid, qty in prepared.args["items"]]
        store.request_return(name, lines, prepared.args["reason"])
        terms = prepared.facts["return_terms"]
        result = {"message": f"A return is requested on order {name}.", **terms}
    else:
        store.record_handoff({"at": format_instant(store.clock.now()), **prepared.args})
        result = {"message": f"I have passed this to our support team, who work {SUPPORT_HOURS}."}

    outcome = {"ok": True, "action": action, "order_number": name, **result}
    store.executed[key] = outcome
    _audit(store, action, prepared, "executed", key)
    return outcome
