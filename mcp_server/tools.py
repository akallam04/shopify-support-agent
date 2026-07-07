"""Support tools over the Shopify client: validated inputs, honest not-found results.

Two failure modes on purpose: bad input raises ToolInputError (the caller sent
garbage and should fix its arguments), while a clean lookup with no match returns
found=False (the honest answer a customer gets). A wrong email on a real order
returns the same not-found shape as a missing order, so order numbers cannot be
probed for existence.
"""

import re
from typing import Any

from mcp_server.shopify_client import ShopifyClient

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ORDER_NOT_FOUND = {
    "found": False,
    "message": "No order found matching that order number and email address.",
}

ORDER_STATUS_QUERY = """
query OrderStatus($query: String!) {
  orders(first: 1, query: $query) {
    nodes {
      name
      createdAt
      cancelledAt
      email
      displayFinancialStatus
      displayFulfillmentStatus
      totalPriceSet { shopMoney { amount currencyCode } }
      customer { displayName email }
      lineItems(first: 20) { nodes { title quantity } }
      fulfillments { status trackingInfo { number company url } }
    }
  }
}
"""

CUSTOMER_ORDERS_QUERY = """
query CustomerOrders($query: String!) {
  orders(first: 10, query: $query, sortKey: PROCESSED_AT, reverse: true) {
    nodes {
      name
      createdAt
      displayFinancialStatus
      displayFulfillmentStatus
      totalPriceSet { shopMoney { amount currencyCode } }
    }
  }
}
"""

INVENTORY_SEARCH_QUERY = """
query InventorySearch($query: String!) {
  products(first: 3, query: $query) {
    nodes {
      title
      handle
      status
      variants(first: 100) {
        nodes { title price inventoryQuantity availableForSale }
      }
    }
  }
}
"""


class ToolInputError(ValueError):
    """Raised when a tool argument fails validation."""


def _normalize_order_number(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits or len(digits) > 10:
        raise ToolInputError(
            "order_number must contain the order's digits, for example #1001"
        )
    return digits


def _validate_email(raw: str) -> str:
    email = (raw or "").strip()
    if not EMAIL_RE.match(email):
        raise ToolInputError("email must be a valid email address")
    return email


def _sanitize_search_text(raw: str) -> str:
    # strip shopify search operators so user text cannot rewrite the query
    text = re.sub(r"[^A-Za-z0-9\s-]", " ", raw or "").strip()
    text = re.sub(r"\s+", " ", text)[:100]
    if not text:
        raise ToolInputError("product_query must contain some searchable text")
    return text


def _money(price_set: dict[str, Any]) -> str:
    money = price_set["shopMoney"]
    return f"{money['amount']} {money['currencyCode']}"


def get_order_status(client: ShopifyClient, order_number: str, email: str) -> dict[str, Any]:
    number = _normalize_order_number(order_number)
    email = _validate_email(email)

    data = client.graphql(ORDER_STATUS_QUERY, {"query": f"name:#{number}"})
    nodes = data["orders"]["nodes"]
    if not nodes:
        return dict(ORDER_NOT_FOUND)

    order = nodes[0]
    emails_on_order = {
        e.lower()
        for e in (order.get("email"), (order.get("customer") or {}).get("email"))
        if e
    }
    if email.lower() not in emails_on_order:
        return dict(ORDER_NOT_FOUND)

    tracking = [
        {"number": t["number"], "carrier": t["company"], "url": t.get("url")}
        for f in order["fulfillments"]
        for t in f["trackingInfo"]
    ]
    return {
        "found": True,
        "order_number": order["name"],
        "placed_at": order["createdAt"],
        "cancelled": order["cancelledAt"] is not None,
        "fulfillment_status": order["displayFulfillmentStatus"],
        "financial_status": order["displayFinancialStatus"],
        "total": _money(order["totalPriceSet"]),
        "items": [
            {"title": li["title"], "quantity": li["quantity"]}
            for li in order["lineItems"]["nodes"]
        ],
        "tracking": tracking,
    }


def list_customer_orders(client: ShopifyClient, email: str) -> dict[str, Any]:
    email = _validate_email(email)

    data = client.graphql(CUSTOMER_ORDERS_QUERY, {"query": f"email:{email}"})
    nodes = data["orders"]["nodes"]
    if not nodes:
        return {"found": False, "message": "No orders found for that email address."}

    return {
        "found": True,
        "count": len(nodes),
        "orders": [
            {
                "order_number": o["name"],
                "placed_at": o["createdAt"],
                "fulfillment_status": o["displayFulfillmentStatus"],
                "financial_status": o["displayFinancialStatus"],
                "total": _money(o["totalPriceSet"]),
            }
            for o in nodes
        ],
    }


def check_inventory(client: ShopifyClient, product_query: str) -> dict[str, Any]:
    text = _sanitize_search_text(product_query)

    data = client.graphql(INVENTORY_SEARCH_QUERY, {"query": f"status:active {text}"})
    nodes = data["products"]["nodes"]
    if not nodes:
        return {"found": False, "message": f"No products found matching '{text}'."}

    products = []
    for p in nodes:
        variants = []
        for v in p["variants"]["nodes"]:
            qty = v.get("inventoryQuantity")
            variants.append(
                {
                    "option": v["title"] if v["title"] != "Default Title" else "Standard",
                    "price": v["price"],
                    "available": bool(v["availableForSale"]),
                    "quantity": qty if isinstance(qty, int) else None,
                }
            )
        products.append({"title": p["title"], "handle": p["handle"], "variants": variants})
    return {"found": True, "products": products}
