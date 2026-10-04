import os
from collections.abc import Iterator

import pytest

from app.config import get_settings
from mcp_server import tools
from mcp_server.backends.shopify import ShopifyAdminBackend
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.shopify_client import ShopifyClient
from mcp_server.simdb import load_db

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("SHOPIFY_LIVE_TESTS") != "1",
        reason="set SHOPIFY_LIVE_TESTS=1 to compare against the development store",
    ),
]

SEED = load_db("data/sim/seed.json")
STRANGER = "nobody@example.com"
CUSTOMER_EMAILS = sorted({c.email for c in SEED.customers.values() if c.email})
SEARCHES = [
    "rain jacket",
    "Stormline",
    "tent",
    "sleeping bag",
    "headlamp",
    "boots",
    "merino",
    "bottle",
    "backpack",
    "gift card",
    "jacket",
    "camping",
    "backpacking",
    "insulated",
    "waterproof",
    "footwear",
    "nonexistent widget",
]


@pytest.fixture(scope="module")
def live() -> Iterator[ShopifyAdminBackend]:
    s = get_settings()
    client = ShopifyClient(s.shopify_store_domain, s.shopify_admin_token, s.shopify_api_version)
    yield ShopifyAdminBackend(client)
    client.close()


@pytest.fixture(scope="module")
def sim() -> SimStoreBackend:
    return SimStoreBackend(SEED.copy_fresh())


def _owner_email(name: str) -> str:
    order = SEED.orders[name]
    return order.email or SEED.customers[order.customer_id].email


@pytest.mark.parametrize("name", sorted(SEED.orders))
def test_order_status_matches(live: ShopifyAdminBackend, sim: SimStoreBackend, name: str) -> None:
    email = _owner_email(name)
    assert tools.get_order_status(live, name, email) == tools.get_order_status(sim, name, email)
    assert tools.get_order_status(live, name, STRANGER) == tools.ORDER_NOT_FOUND
    assert tools.get_order_status(sim, name, STRANGER) == tools.ORDER_NOT_FOUND


def test_missing_order_matches(live: ShopifyAdminBackend, sim: SimStoreBackend) -> None:
    email = CUSTOMER_EMAILS[0]
    assert tools.get_order_status(live, "#9999", email) == tools.get_order_status(sim, "#9999", email)


@pytest.mark.parametrize("email", CUSTOMER_EMAILS + [CUSTOMER_EMAILS[0].upper(), STRANGER])
def test_customer_orders_match(live: ShopifyAdminBackend, sim: SimStoreBackend, email: str) -> None:
    assert tools.list_customer_orders(live, email) == tools.list_customer_orders(sim, email)


@pytest.mark.parametrize("query", SEARCHES)
def test_inventory_matches(live: ShopifyAdminBackend, sim: SimStoreBackend, query: str) -> None:
    assert tools.check_inventory(live, query) == tools.check_inventory(sim, query)
