"""MCP server exposing read-only store support tools over stdio, on the live or simulated backend.

Run from the repo root: .venv/bin/python -m mcp_server.server
STORE_BACKEND=sim serves the simulated store from SIM_SEED_PATH instead of the live API.
Any MCP host can consume this: our agent, the check script, or Claude Desktop.
"""

import sys
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from app.config import Settings, get_settings
from mcp_server import policy, tools
from mcp_server.backends.base import StoreBackend
from mcp_server.backends.shopify import ShopifyAdminBackend
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import FrozenClock, parse_instant
from mcp_server.shopify_client import ShopifyClient
from mcp_server.simdb import load_db

mcp = FastMCP("aurora-outfitters-support")

CancelReason = Literal[policy.CANCEL_REASONS]
ReturnReason = Literal[policy.RETURN_REASONS]


class ReturnItem(BaseModel):
    title: str
    variant: str = ""
    quantity: int = 1

_backend: StoreBackend | None = None


def build_backend(settings: Settings) -> StoreBackend:
    if settings.store_backend == "sim":
        clock = FrozenClock(parse_instant(settings.sim_now)) if settings.sim_now else None
        return SimStoreBackend(load_db(settings.sim_seed_path), clock)
    client = ShopifyClient(
        settings.shopify_store_domain, settings.shopify_admin_token, settings.shopify_api_version
    )
    if not settings.write_actions:
        return ShopifyAdminBackend(client)
    return ShopifyAdminBackend(client, live_write_client(settings, client))


def live_write_client(settings: Settings, read_client: ShopifyClient) -> ShopifyClient:
    if not settings.shopify_write_token:
        raise SystemExit("live writes need SHOPIFY_WRITE_TOKEN, see docs/live-write-testing.md")
    if not read_client.shop_info()["plan"]["partnerDevelopment"]:
        raise SystemExit("refusing live writes: this is not a development store")
    return ShopifyClient(settings.shopify_store_domain, settings.shopify_write_token, settings.shopify_api_version)


def _store() -> StoreBackend:
    global _backend
    if _backend is None:
        _backend = build_backend(get_settings())
    return _backend


@mcp.tool()
def get_order_status(order_number: str, email: str) -> dict[str, Any]:
    """Look up the status of one order, including fulfillment state and tracking.

    Requires both the order number and the email address on the order; they must
    match or the order is reported as not found. Never guesses.

    Args:
        order_number: The customer's order number, for example #1001.
        email: The email address the order was placed with.
    """
    return tools.get_order_status(_store(), order_number, email)


@mcp.tool()
def list_customer_orders(email: str) -> dict[str, Any]:
    """List recent orders (up to 10, newest first) for a customer email address.

    Args:
        email: The customer's email address.
    """
    return tools.list_customer_orders(_store(), email)


@mcp.tool()
def check_inventory(product_query: str) -> dict[str, Any]:
    """Check live stock and prices for products matching a search phrase.

    Args:
        product_query: Free-text product search, for example "rain jacket".
    """
    return tools.check_inventory(_store(), product_query)


def cancel_order(order_number: str, email: str, reason: CancelReason) -> dict[str, Any]:
    """Cancel an order that has not shipped yet.

    Store policy allows cancelling only within 2 hours of the order being placed and before it
    ships; the tool refuses with a reason otherwise. Needs the order number, the email on the
    order, and the customer's reason.

    Args:
        order_number: The customer's order number, for example #1001.
        email: The email address the order was placed with.
        reason: Why the customer is cancelling.
    """
    return tools.execute(_store(), "cancel_order", {"order_number": order_number, "email": email, "reason": reason})


def update_shipping_address(
    order_number: str,
    email: str,
    address1: str,
    city: str,
    province_code: str,
    zip: str,
    country_code: str,
    address2: str = "",
    first_name: str = "",
    last_name: str = "",
) -> dict[str, Any]:
    """Change the shipping address on an order that has not shipped yet.

    Store policy allows this only within 2 hours of the order being placed and before it ships,
    to the United States or Canada; the tool refuses with a reason otherwise.

    Args:
        order_number: The customer's order number, for example #1001.
        email: The email address the order was placed with.
        address1: Street address.
        city: City.
        province_code: Two letter state or province code, for example CO or BC.
        zip: ZIP or postal code.
        country_code: Two letter country code, US or CA.
        address2: Apartment, suite, or unit, if any.
        first_name: Recipient first name, if it changes.
        last_name: Recipient last name, if it changes.
    """
    args = {
        "order_number": order_number,
        "email": email,
        "address1": address1,
        "address2": address2,
        "city": city,
        "province_code": province_code,
        "zip": zip,
        "country_code": country_code,
        "first_name": first_name,
        "last_name": last_name,
    }
    return tools.execute(_store(), "update_shipping_address", args)


def request_return(order_number: str, email: str, items: list[ReturnItem], reason: ReturnReason) -> dict[str, Any]:
    """Request a return for delivered items, for the store to approve.

    Store policy allows returns within 30 days of delivery, never for final sale items or gift
    cards; the tool refuses with a reason otherwise. Name each item as it appears on the order,
    with its variant when the order has more than one of that item.

    Args:
        order_number: The customer's order number, for example #1001.
        email: The email address the order was placed with.
        items: The items to return, each with its title, variant if needed, and quantity.
        reason: Why the customer is returning the items.
    """
    args = {
        "order_number": order_number,
        "email": email,
        "items": [i.model_dump() if isinstance(i, BaseModel) else i for i in items],
        "reason": reason,
    }
    return tools.execute(_store(), "request_return", args)


def transfer_to_human(summary: str, order_number: str = "") -> dict[str, Any]:
    """Hand the conversation to the human support team.

    Use only when the request cannot be handled with the other tools, such as warranty claims,
    damaged items, or exceptions to store policy.

    Args:
        summary: A short summary of what the customer needs, for the support team.
        order_number: The order this is about, if any.
    """
    return tools.execute(_store(), "transfer_to_human", {"summary": summary, "order_number": order_number})


READ_TOOLS = (get_order_status, list_customer_orders, check_inventory)
WRITE_TOOLS = (cancel_order, update_shipping_address, request_return, transfer_to_human)


def main() -> None:
    settings = get_settings()
    backend = _store()
    if settings.write_actions:
        if not backend.supports_writes:
            raise SystemExit(f"WRITE_ACTIONS is on but the {backend.name} backend does not accept writes")
        for fn in WRITE_TOOLS:
            mcp.add_tool(fn)
    print(f"aurora support mcp server using the {backend.name} backend", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
