"""Support tools over any store backend: validated inputs, shared authorization, honest not-found.

Two failure modes on purpose: bad input raises ToolInputError (the caller sent
garbage and should fix its arguments), while a clean lookup with no match returns
found=False (the honest answer a customer gets). A wrong email on a real order
returns the same not-found shape as a missing order, so order numbers cannot be
probed for existence. Authorization lives here, above the backend, so the live
and simulated stores enforce it identically.
"""

import re
from typing import Any

from mcp_server.backends.base import FoundOrder, StoreBackend
from mcp_server.search import WORD_RE, title_score
from mcp_server.simdb import Order, Product

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
        "items": [{"title": li.title, "quantity": li.quantity} for li in order.line_items],
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
