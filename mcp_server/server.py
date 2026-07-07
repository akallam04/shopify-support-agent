"""MCP server exposing read-only Shopify support tools over stdio.

Run from the repo root: .venv/bin/python -m mcp_server.server
Any MCP host can consume this: our agent, the check script, or Claude Desktop.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from mcp_server import tools
from mcp_server.shopify_client import ShopifyClient

mcp = FastMCP("aurora-outfitters-support")

_client: ShopifyClient | None = None


def _shopify() -> ShopifyClient:
    # one client for the lifetime of the server process
    global _client
    if _client is None:
        s = get_settings()
        _client = ShopifyClient(
            s.shopify_store_domain, s.shopify_admin_token, s.shopify_api_version
        )
    return _client


@mcp.tool()
def get_order_status(order_number: str, email: str) -> dict[str, Any]:
    """Look up the status of one order, including fulfillment state and tracking.

    Requires both the order number and the email address on the order; they must
    match or the order is reported as not found. Never guesses.

    Args:
        order_number: The customer's order number, for example #1001.
        email: The email address the order was placed with.
    """
    return tools.get_order_status(_shopify(), order_number, email)


@mcp.tool()
def list_customer_orders(email: str) -> dict[str, Any]:
    """List recent orders (up to 10, newest first) for a customer email address.

    Args:
        email: The customer's email address.
    """
    return tools.list_customer_orders(_shopify(), email)


@mcp.tool()
def check_inventory(product_query: str) -> dict[str, Any]:
    """Check live stock and prices for products matching a search phrase.

    Args:
        product_query: Free-text product search, for example "rain jacket".
    """
    return tools.check_inventory(_shopify(), product_query)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
