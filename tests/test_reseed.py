from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scripts.reseed_test_orders import ExistingOrder, Line, creation_order, is_usable, order_input, plan

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
UNFULFILLED = {"key": "cancel-eligible", "state": "unfulfilled"}
SHIPPED = {"key": "shipped-not-delivered", "state": "fulfilled"}
IN_WINDOW = {"key": "return-in-window", "state": "delivered", "delivered_days_ago": 5}
OUT_OF_WINDOW = {"key": "return-out-of-window", "state": "delivered", "delivered_days_ago": 45}


def existing(
    status: str = "UNFULFILLED", cancelled: bool = False, delivered_days_ago: int | None = None, returned: bool = False
) -> ExistingOrder:
    delivered = NOW - timedelta(days=delivered_days_ago) if delivered_days_ago is not None else None
    return ExistingOrder(
        name="#2001", cancelled=cancelled, fulfillment_status=status, delivered_at=delivered, returned=returned
    )


@pytest.mark.parametrize(
    "spec, order, usable",
    [
        (UNFULFILLED, existing(), True),
        (UNFULFILLED, existing(cancelled=True), False),
        (UNFULFILLED, existing(status="FULFILLED"), False),
        (SHIPPED, existing(status="FULFILLED"), True),
        (SHIPPED, existing(status="FULFILLED", delivered_days_ago=1), False),
        (IN_WINDOW, existing(status="FULFILLED", delivered_days_ago=5), True),
        (IN_WINDOW, existing(status="FULFILLED", delivered_days_ago=31), False),
        (IN_WINDOW, existing(status="FULFILLED"), False),
        (OUT_OF_WINDOW, existing(status="FULFILLED", delivered_days_ago=45), True),
        (OUT_OF_WINDOW, existing(status="FULFILLED", delivered_days_ago=10), False),
        (IN_WINDOW, existing(status="FULFILLED", delivered_days_ago=5, returned=True), False),
    ],
)
def test_fixture_usability(spec: dict, order: ExistingOrder, usable: bool) -> None:
    assert is_usable(spec, order, NOW) is usable


def test_plan_keeps_a_usable_order_and_recreates_spent_ones() -> None:
    decisions = plan(
        [UNFULFILLED, IN_WINDOW],
        {
            "cancel-eligible": [existing(cancelled=True), existing()],
            "return-in-window": [existing(status="FULFILLED", delivered_days_ago=40)],
        },
        NOW,
    )
    assert [(d.key, d.action) for d in decisions] == [
        ("cancel-eligible", "keep"),
        ("return-in-window", "create"),
    ]


CUSTOMER = {"id": "gid://shopify/Customer/1", "firstName": "Maya", "lastName": "Thompson"}
LINES = [
    Line(variant_id="gid://shopify/ProductVariant/1", price=Decimal("179.99"), quantity=1),
    Line(variant_id="gid://shopify/ProductVariant/2", price=Decimal("39.95"), quantity=2),
]


def test_order_input_backdates_the_order_and_pays_the_full_total() -> None:
    spec = {"key": "return-in-window", "customer_email": "maya.thompson@example.com", "state": "unfulfilled", "placed_days_ago": 9}
    order = order_input(spec, LINES, CUSTOMER, "gid://shopify/Location/1", NOW)
    assert order["processedAt"] == "2026-09-25T12:00:00Z"
    assert order["financialStatus"] == "PAID"
    assert order["transactions"][0]["amountSet"]["shopMoney"] == {"amount": "259.89", "currencyCode": "USD"}
    assert order["tags"] == ["v2-live-test", "fixture-return-in-window"]
    assert order["shippingAddress"]["firstName"] == "Maya"
    assert "fulfillment" not in order


def test_shipped_fixtures_get_an_in_transit_fulfillment() -> None:
    spec = {
        "key": "shipped-not-delivered",
        "customer_email": "maya.thompson@example.com",
        "state": "fulfilled",
        "placed_days_ago": 4,
        "tracking": {"number": "1Z1", "company": "UPS"},
    }
    order = order_input(spec, LINES, CUSTOMER, "gid://shopify/Location/1", NOW)
    assert order["fulfillmentStatus"] == "FULFILLED"
    assert order["fulfillment"]["shipmentStatus"] == "IN_TRANSIT"
    assert order["fulfillment"]["trackingNumber"] == "1Z1"


def test_the_most_backdated_delivery_is_created_first_as_the_probe() -> None:
    keys = [s["key"] for s in creation_order([UNFULFILLED, IN_WINDOW, OUT_OF_WINDOW, SHIPPED])]
    assert keys[0] == "return-out-of-window"
    assert keys[1] == "return-in-window"
