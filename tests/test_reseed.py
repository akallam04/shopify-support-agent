from datetime import UTC, datetime, timedelta

import pytest

from scripts.reseed_test_orders import ExistingOrder, is_usable, plan

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
UNFULFILLED = {"key": "cancel-eligible", "state": "unfulfilled"}
SHIPPED = {"key": "shipped-not-delivered", "state": "fulfilled"}
IN_WINDOW = {"key": "return-in-window", "state": "delivered", "delivered_days_ago": 5}
OUT_OF_WINDOW = {"key": "return-out-of-window", "state": "delivered", "delivered_days_ago": 45}


def existing(status: str = "UNFULFILLED", cancelled: bool = False, delivered_days_ago: int | None = None) -> ExistingOrder:
    delivered = NOW - timedelta(days=delivered_days_ago) if delivered_days_ago is not None else None
    return ExistingOrder(name="#2001", cancelled=cancelled, fulfillment_status=status, delivered_at=delivered)


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
