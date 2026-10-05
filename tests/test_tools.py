from typing import Any

import pytest

from mcp_server.backends.base import FoundOrder
from mcp_server.backends.shopify import ShopifyAdminBackend, TruncatedConnectionError, map_order, map_product
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.simdb import Order, Product, SimDB, load_db
from mcp_server.tools import (
    ORDER_NOT_FOUND,
    ToolInputError,
    check_inventory,
    get_order_status,
    list_customer_orders,
)


class RecordingBackend:
    name = "recording"
    reads_returns = True

    def __init__(self, inner: SimStoreBackend) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def find_order(self, order_name: str) -> FoundOrder | None:
        self.calls.append(f"find_order {order_name}")
        return self.inner.find_order(order_name)

    def orders_for_email(self, email: str, limit: int) -> list[Order]:
        self.calls.append(f"orders_for_email {email}")
        return self.inner.orders_for_email(email, limit)

    def search_products(self, text: str, limit: int) -> list[Product]:
        self.calls.append(f"search_products {text}")
        return self.inner.search_products(text, limit)


@pytest.fixture
def sim(db: SimDB) -> RecordingBackend:
    return RecordingBackend(SimStoreBackend(db))


def test_order_number_forms_resolve_to_the_same_order(sim: RecordingBackend) -> None:
    for raw in ("#1001", "1001", "Order 1001"):
        assert get_order_status(sim, raw, "maya.thompson@example.com")["found"] is True
    assert sim.calls == ["find_order #1001"] * 3


def test_order_status_view(sim: RecordingBackend) -> None:
    result = get_order_status(sim, "#1001", "MAYA.THOMPSON@example.com")
    assert result == {
        "found": True,
        "order_number": "#1001",
        "placed_at": "2026-07-07T00:26:32Z",
        "cancelled": False,
        "fulfillment_status": "FULFILLED",
        "shipping": "shipped, not delivered yet",
        "financial_status": "PAID",
        "total": "219.94 USD",
        "items": [
            {"title": "Stormline Rain Jacket", "variant": "M", "quantity": 1},
            {"title": "Wander Insulated Bottle", "variant": None, "quantity": 1},
        ],
        "tracking": [{"number": "1Z999AA10123456784", "carrier": "UPS", "url": None}],
        "delivered_on": None,
        "returns": [],
    }


def test_customer_email_authorizes_when_the_order_has_none(sim: RecordingBackend) -> None:
    assert get_order_status(sim, "#1004", "jordan.lee@example.com")["found"] is True


def test_wrong_email_is_indistinguishable_from_a_missing_order(sim: RecordingBackend) -> None:
    wrong = get_order_status(sim, "#1001", "jordan.lee@example.com")
    missing = get_order_status(sim, "#9999", "jordan.lee@example.com")
    assert wrong == missing == ORDER_NOT_FOUND


def test_invalid_inputs_raise_before_touching_the_store(sim: RecordingBackend) -> None:
    with pytest.raises(ToolInputError):
        get_order_status(sim, "no digits here", "maya.thompson@example.com")
    with pytest.raises(ToolInputError):
        get_order_status(sim, "#1001", "not-an-email")
    with pytest.raises(ToolInputError):
        list_customer_orders(sim, "also not an email")
    with pytest.raises(ToolInputError):
        check_inventory(sim, "::((%%))::")
    assert sim.calls == []


def test_list_customer_orders(sim: RecordingBackend) -> None:
    result = list_customer_orders(sim, "maya.thompson@example.com")
    assert result["count"] == 2
    assert [o["order_number"] for o in result["orders"]] == ["#1002", "#1001"]
    assert list_customer_orders(sim, "nobody@example.com")["found"] is False


def test_inventory_view_and_default_variant_label(sim: RecordingBackend) -> None:
    result = check_inventory(sim, "insulated bottle")
    assert result == {
        "found": True,
        "products": [
            {
                "title": "Wander Insulated Bottle",
                "handle": "wander-insulated-bottle",
                "variants": [{"option": "Standard", "price": "39.95", "available": True, "quantity": None}],
            }
        ],
    }


def test_title_matches_outrank_older_description_matches(db: SimDB) -> None:
    db.products["p1"].description = "Fits a water bottle in the chest pocket."
    titles = [p["title"] for p in check_inventory(SimStoreBackend(db), "bottle")["products"]]
    assert titles == ["Wander Insulated Bottle", "Stormline Rain Jacket"]


def test_inventory_never_returns_draft_products(sim: RecordingBackend) -> None:
    assert check_inventory(sim, "prototype shell")["found"] is False


class FakeClient:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((query, variables or {}))
        for marker, payload in self.payloads.items():
            if marker in query:
                return payload
        raise AssertionError(f"unexpected query: {query[:60]}")


def order_node(
    name: str = "#1001",
    with_customer: bool = True,
    with_variant: bool = True,
    delivered_at: str | None = None,
) -> dict[str, Any]:
    email = "maya.thompson@example.com"
    return {
        "id": f"gid://shopify/Order/{name[1:]}",
        "name": name,
        "email": email,
        "createdAt": "2026-07-07T00:26:32Z",
        "processedAt": "2026-07-07T00:26:32Z",
        "cancelledAt": None,
        "cancelReason": None,
        "tags": [],
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "FULFILLED",
        "totalPriceSet": {"shopMoney": {"amount": "219.94", "currencyCode": "USD"}},
        "customer": {
            "id": "gid://shopify/Customer/1",
            "firstName": "Maya",
            "lastName": "Thompson",
            "defaultEmailAddress": {"emailAddress": email},
            "defaultAddress": None,
        }
        if with_customer
        else None,
        "shippingAddress": None,
        "lineItems": {
            "nodes": [
                {
                    "id": "gid://shopify/LineItem/1",
                    "title": "Stormline Rain Jacket",
                    "variantTitle": "M",
                    "quantity": 1,
                    "sku": "",
                    "variant": {"id": "gid://shopify/ProductVariant/11"} if with_variant else None,
                    "product": {"id": "gid://shopify/Product/1"} if with_variant else None,
                    "originalUnitPriceSet": {"shopMoney": {"amount": "179.99", "currencyCode": "USD"}},
                }
            ]
        },
        "fulfillments": [
            {
                "id": "gid://shopify/Fulfillment/1",
                "status": "SUCCESS",
                "createdAt": "2026-07-07T00:30:00Z",
                "deliveredAt": delivered_at,
                "displayStatus": "DELIVERED" if delivered_at else "FULFILLED",
                "trackingInfo": [{"number": "1Z999AA10123456784", "company": "UPS", "url": None}],
                "fulfillmentLineItems": {
                    "nodes": [
                        {"id": "gid://shopify/FulfillmentLineItem/1", "lineItem": {"id": "gid://shopify/LineItem/1"}, "quantity": 1}
                    ]
                },
            }
        ],
    }


def test_live_lookup_queries_by_name_and_needs_an_exact_match() -> None:
    client = FakeClient({"OrderByName": {"orders": {"nodes": [order_node("#10010"), order_node("#1001")]}}})
    found = ShopifyAdminBackend(client).find_order("#1001")
    assert found is not None and found.order.name == "#1001"
    assert client.calls[0][1] == {"query": "name:#1001"}

    near_miss = FakeClient({"OrderByName": {"orders": {"nodes": [order_node("#10010")]}}})
    assert ShopifyAdminBackend(near_miss).find_order("#1001") is None


def test_live_search_is_sanitized_and_scoped_to_active_products() -> None:
    client = FakeClient({"ProductSearch": {"products": {"nodes": []}}})
    check_inventory(ShopifyAdminBackend(client), 'rain "jacket" OR status:draft')
    variables = client.calls[0][1]
    assert variables["query"].startswith("status:active ")
    assert '"' not in variables["query"]
    assert "status:draft" not in variables["query"]
    assert variables["limit"] == 10


def test_live_customer_orders_filter_by_email() -> None:
    client = FakeClient({"OrdersByEmail": {"orders": {"nodes": [order_node()]}}})
    orders = ShopifyAdminBackend(client).orders_for_email("maya.thompson@example.com", 10)
    assert [o.name for o in orders] == ["#1001"]
    assert client.calls[0][1] == {"query": "email:maya.thompson@example.com", "limit": 10}


def test_mapping_handles_guest_checkout_and_custom_items() -> None:
    found = map_order(order_node(with_customer=False, with_variant=False))
    assert found.customer is None
    assert found.order.customer_id is None
    assert found.order.line_items[0].variant_id is None
    assert found.order.line_items[0].sku is None


def test_mapping_keeps_delivery_tracking_and_fulfilled_lines() -> None:
    order = map_order(order_node(delivered_at="2026-07-10T18:00:00Z")).order
    fulfillment = order.fulfillments[0]
    assert fulfillment.delivered_at == "2026-07-10T18:00:00Z"
    assert fulfillment.tracking[0].company == "UPS"
    assert fulfillment.line_items[0].line_item_id == "gid://shopify/LineItem/1"
    assert fulfillment.line_items[0].fulfillment_line_item_id == "gid://shopify/FulfillmentLineItem/1"


def test_a_full_nested_page_fails_loudly_instead_of_truncating() -> None:
    variant = {"id": "v", "title": "x", "sku": "", "price": "1.00", "inventoryQuantity": 1, "availableForSale": True}
    node = {
        "id": "p",
        "handle": "many",
        "title": "Many",
        "status": "ACTIVE",
        "variants": {"nodes": [dict(variant, id=f"v{i}") for i in range(100)]},
    }
    with pytest.raises(TruncatedConnectionError):
        map_product(node)


def test_order_status_shows_delivery_and_returns_only_when_the_backend_reads_them() -> None:
    seed = load_db("data/sim/seed.json")
    view = get_order_status(SimStoreBackend(seed), "#1017", "jordan.lee@example.com")
    assert view["delivered_on"] == "2026-09-29"
    assert view["returns"] == [{"title": "Stormline Rain Jacket", "variant": "L", "quantity": 1, "status": "REQUESTED"}]
    in_transit = get_order_status(SimStoreBackend(seed), "#1019", "maya.thompson@example.com")
    assert in_transit["delivered_on"] is None and in_transit["returns"] == []

    class ReadOnly(RecordingBackend):
        reads_returns = False

    assert "returns" not in get_order_status(ReadOnly(SimStoreBackend(seed)), "#1017", "jordan.lee@example.com")


@pytest.mark.parametrize(
    ("order", "email", "expected"),
    [
        ("#1016", "maya.thompson@example.com", "delivered on 2026-08-20"),
        ("#1019", "maya.thompson@example.com", "shipped, not delivered yet"),
        ("#1023", "maya.thompson@example.com", "not shipped yet"),
        ("#1020", "maya.thompson@example.com", "cancelled"),
    ],
)
def test_shipping_status_never_calls_a_fulfilled_order_delivered(order: str, email: str, expected: str) -> None:
    backend = SimStoreBackend(load_db("data/sim/seed.json"))
    assert get_order_status(backend, order, email)["shipping"] == expected
    listed = {o["order_number"]: o["shipping"] for o in list_customer_orders(backend, email)["orders"]}
    assert listed[order] == expected
