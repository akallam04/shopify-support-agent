"""MCP server exposing read-only store support tools over stdio, on the live or simulated backend.

Run from the repo root: .venv/bin/python -m mcp_server.server
STORE_BACKEND=sim serves the simulated store from SIM_SEED_PATH instead of the live API.
Any MCP host can consume this: our agent, the check script, or Claude Desktop.
"""

import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.config import Settings, get_settings
from mcp_server import tools
from mcp_server.backends.base import StoreBackend
from mcp_server.backends.shopify import ShopifyAdminBackend
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import FrozenClock, parse_instant
from mcp_server.shopify_client import ShopifyClient
from mcp_server.simdb import load_db

mcp = FastMCP("aurora-outfitters-support")

_backend: StoreBackend | None = None


def build_backend(settings: Settings) -> StoreBackend:
    if settings.store_backend == "sim":
        clock = FrozenClock(parse_instant(settings.sim_now)) if settings.sim_now else None
        return SimStoreBackend(load_db(settings.sim_seed_path), clock)
    client = ShopifyClient(
        settings.shopify_store_domain, settings.shopify_admin_token, settings.shopify_api_version
    )
    return ShopifyAdminBackend(client)


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


def main() -> None:
    backend = _store()
    print(f"aurora support mcp server using the {backend.name} backend", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
