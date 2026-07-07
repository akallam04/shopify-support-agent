"""Unit tests for the support tools, run against a fake client."""

import pytest

from mcp_server.tools import (
    ToolInputError,
    check_inventory,
    get_order_status,
    list_customer_orders,
)

ORDER_PAYLOAD = {
    "orders": {
        "nodes": [
            {
                "name": "#1001",
                "createdAt": "2026-07-07T00:26:32Z",
                "cancelledAt": None,
                "email": "maya.thompson@example.com",
                "displayFinancialStatus": "PAID",
                "displayFulfillmentStatus": "FULFILLED",
                "totalPriceSet": {"shopMoney": {"amount": "219.94", "currencyCode": "USD"}},
                "customer": {"displayName": "Maya Thompson", "email": "maya.thompson@example.com"},
                "lineItems": {"nodes": [{"title": "Stormline Rain Jacket", "quantity": 1}]},
                "fulfillments": [
                    {
                        "status": "SUCCESS",
                        "trackingInfo": [
                            {"number": "1Z999AA10123456784", "company": "UPS", "url": None}
                        ],
                    }
                ],
            }
        ]
    }
}

INVENTORY_PAYLOAD = {
    "products": {
        "nodes": [
            {
                "title": "Stormline Rain Jacket",
                "handle": "stormline-rain-jacket",
                "status": "ACTIVE",
                "variants": {
                    "nodes": [
                        {"title": "M", "price": "179.99", "inventoryQuantity": 14, "availableForSale": True}
                    ]
                },
            }
        ]
    }
}


class FakeClient:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict | None]] = []

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append((query, variables))
        for marker, payload in self.payloads.items():
            if marker in query:
                return payload
        raise AssertionError(f"unexpected query: {query[:60]}")


def test_order_number_normalization() -> None:
    client = FakeClient({"OrderStatus": ORDER_PAYLOAD})
    for raw in ("#1001", "1001", "Order 1001"):
        result = get_order_status(client, raw, "maya.thompson@example.com")
        assert result["found"] is True
    # every call queried the canonical name form
    assert all(v["query"] == "name:#1001" for _, v in client.calls)


def test_order_status_happy_path() -> None:
    client = FakeClient({"OrderStatus": ORDER_PAYLOAD})
    result = get_order_status(client, "#1001", "MAYA.THOMPSON@example.com")
    assert result["fulfillment_status"] == "FULFILLED"
    assert result["tracking"] == [
        {"number": "1Z999AA10123456784", "carrier": "UPS", "url": None}
    ]
    assert result["items"][0]["title"] == "Stormline Rain Jacket"
    assert result["cancelled"] is False


def test_wrong_email_looks_like_missing_order() -> None:
    client = FakeClient({"OrderStatus": ORDER_PAYLOAD})
    wrong = get_order_status(client, "#1001", "jordan.lee@example.com")
    missing = get_order_status(FakeClient({"OrderStatus": {"orders": {"nodes": []}}}), "#9999", "jordan.lee@example.com")
    assert wrong == missing
    assert wrong["found"] is False


def test_invalid_inputs_raise() -> None:
    client = FakeClient({})
    with pytest.raises(ToolInputError):
        get_order_status(client, "no digits here", "maya.thompson@example.com")
    with pytest.raises(ToolInputError):
        get_order_status(client, "#1001", "not-an-email")
    with pytest.raises(ToolInputError):
        list_customer_orders(client, "also not an email")
    with pytest.raises(ToolInputError):
        check_inventory(client, "::((%%))::")
    assert client.calls == []


def test_inventory_search_is_sanitized() -> None:
    client = FakeClient({"InventorySearch": INVENTORY_PAYLOAD})
    result = check_inventory(client, 'rain "jacket" OR status:draft')
    assert result["found"] is True
    _, variables = client.calls[0]
    assert '"' not in variables["query"]
    assert "status:draft" not in variables["query"]
    assert variables["query"].startswith("status:active ")


def test_list_customer_orders() -> None:
    payload = {
        "orders": {
            "nodes": [
                {
                    "name": "#1002",
                    "createdAt": "2026-07-07T00:26:40Z",
                    "displayFinancialStatus": "PAID",
                    "displayFulfillmentStatus": "UNFULFILLED",
                    "totalPriceSet": {"shopMoney": {"amount": "169.98", "currencyCode": "USD"}},
                }
            ]
        }
    }
    client = FakeClient({"CustomerOrders": payload})
    result = list_customer_orders(client, "maya.thompson@example.com")
    assert result["count"] == 1
    assert result["orders"][0]["order_number"] == "#1002"
