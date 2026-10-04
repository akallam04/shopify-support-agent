from datetime import timedelta

import pytest

from mcp_server.clock import parse_instant
from mcp_server.policy import can_cancel, can_change_address, can_return, return_terms
from mcp_server.simdb import Address, ReturnLine, ReturnRequest, SimDB

PLACED_1002 = parse_instant("2026-07-07T00:26:36Z")
DELIVERED = "2026-07-10T18:00:00Z"
GOOD_ADDRESS = Address(address1="9 Pine St", city="Boulder", province_code="CO", zip="80302", country_code="US")


def delivered_1001(db: SimDB) -> SimDB:
    db.orders["#1001"].fulfillments[0].delivered_at = DELIVERED
    return db


def test_cancel_inside_the_window(db: SimDB) -> None:
    assert can_cancel(db.orders["#1002"], "changed_mind", PLACED_1002 + timedelta(hours=1)).allowed


def test_cancel_at_exactly_two_hours_is_allowed(db: SimDB) -> None:
    assert can_cancel(db.orders["#1002"], "changed_mind", PLACED_1002 + timedelta(hours=2)).allowed


def test_cancel_after_two_hours_is_refused(db: SimDB) -> None:
    decision = can_cancel(db.orders["#1002"], "changed_mind", PLACED_1002 + timedelta(hours=2, minutes=1))
    assert decision.code == "change_window_passed"
    assert "2 hours" in decision.reason


def test_cancel_refused_once_shipped(db: SimDB) -> None:
    assert can_cancel(db.orders["#1001"], "changed_mind", PLACED_1002).code == "already_shipped"


def test_cancel_refused_when_already_cancelled(db: SimDB) -> None:
    db.orders["#1002"].cancelled_at = "2026-07-07T00:30:00Z"
    assert can_cancel(db.orders["#1002"], "changed_mind", PLACED_1002).code == "already_cancelled"


def test_cancel_needs_a_known_reason(db: SimDB) -> None:
    assert can_cancel(db.orders["#1002"], "because", PLACED_1002).code == "invalid_reason"


def test_shipped_wins_over_the_window(db: SimDB) -> None:
    late = PLACED_1002 + timedelta(days=3)
    assert can_cancel(db.orders["#1001"], "changed_mind", late).code == "already_shipped"


def test_address_change_inside_the_window(db: SimDB) -> None:
    assert can_change_address(db.orders["#1002"], GOOD_ADDRESS, PLACED_1002 + timedelta(minutes=30)).allowed


@pytest.mark.parametrize("country", ["CA", "ca"])
def test_canada_is_a_valid_destination(db: SimDB, country: str) -> None:
    address = GOOD_ADDRESS.model_copy(update={"country_code": country, "province_code": "BC", "zip": "V5K 0A1"})
    assert can_change_address(db.orders["#1002"], address, PLACED_1002).allowed


def test_address_outside_us_and_canada_is_refused(db: SimDB) -> None:
    address = GOOD_ADDRESS.model_copy(update={"country_code": "MX"})
    assert can_change_address(db.orders["#1002"], address, PLACED_1002).code == "unsupported_destination"


def test_incomplete_address_is_refused(db: SimDB) -> None:
    address = GOOD_ADDRESS.model_copy(update={"zip": None})
    assert can_change_address(db.orders["#1002"], address, PLACED_1002).code == "incomplete_address"


def test_address_change_follows_the_same_window(db: SimDB) -> None:
    late = PLACED_1002 + timedelta(hours=3)
    assert can_change_address(db.orders["#1002"], GOOD_ADDRESS, late).code == "change_window_passed"


def lines(db: SimDB, *pairs: tuple[int, int]):
    order = db.orders["#1001"]
    return [(order.line_items[i], qty) for i, qty in pairs]


def test_return_inside_the_window(db: SimDB) -> None:
    db = delivered_1001(db)
    now = parse_instant(DELIVERED) + timedelta(days=5)
    assert can_return(db.orders["#1001"], lines(db, (0, 1)), db.products, "size_too_small", now).allowed


def test_return_at_exactly_thirty_days_is_allowed(db: SimDB) -> None:
    db = delivered_1001(db)
    now = parse_instant(DELIVERED) + timedelta(days=30)
    assert can_return(db.orders["#1001"], lines(db, (0, 1)), db.products, "unwanted", now).allowed


def test_return_after_thirty_days_is_refused(db: SimDB) -> None:
    db = delivered_1001(db)
    now = parse_instant(DELIVERED) + timedelta(days=30, minutes=1)
    decision = can_return(db.orders["#1001"], lines(db, (0, 1)), db.products, "unwanted", now)
    assert decision.code == "return_window_passed"
    assert "July 10, 2026" in decision.reason


def test_return_before_delivery_is_refused(db: SimDB) -> None:
    now = parse_instant(DELIVERED)
    assert can_return(db.orders["#1001"], lines(db, (0, 1)), db.products, "unwanted", now).code == "not_delivered"


@pytest.mark.parametrize("tag", ["final-sale", "Final-Sale", "FINAL-SALE"])
def test_final_sale_items_cannot_be_returned(db: SimDB, tag: str) -> None:
    db = delivered_1001(db)
    db.products["p1"].tags.append(tag)
    now = parse_instant(DELIVERED) + timedelta(days=1)
    decision = can_return(db.orders["#1001"], lines(db, (0, 1)), db.products, "unwanted", now)
    assert decision.code == "final_sale"
    assert "FINAL SALE" in decision.reason


def test_gift_cards_cannot_be_returned(db: SimDB) -> None:
    db = delivered_1001(db)
    db.products["p2"].is_gift_card = True
    now = parse_instant(DELIVERED) + timedelta(days=1)
    assert can_return(db.orders["#1001"], lines(db, (1, 1)), db.products, "unwanted", now).code == "gift_card"


def test_return_quantity_counts_items_already_requested(db: SimDB) -> None:
    db = delivered_1001(db)
    db.orders["#1001"].returns = [
        ReturnRequest(status="REQUESTED", reason="unwanted", line_items=[ReturnLine(line_item_id="l1", quantity=1)])
    ]
    now = parse_instant(DELIVERED) + timedelta(days=1)
    assert can_return(db.orders["#1001"], lines(db, (0, 1)), db.products, "unwanted", now).code == "quantity_exceeds"


def test_return_of_zero_items_is_refused(db: SimDB) -> None:
    db = delivered_1001(db)
    now = parse_instant(DELIVERED) + timedelta(days=1)
    assert can_return(db.orders["#1001"], lines(db, (0, 0)), db.products, "unwanted", now).code == "quantity_exceeds"
    assert can_return(db.orders["#1001"], [], db.products, "unwanted", now).code == "no_items"


def test_return_needs_a_known_reason(db: SimDB) -> None:
    db = delivered_1001(db)
    now = parse_instant(DELIVERED) + timedelta(days=1)
    assert can_return(db.orders["#1001"], lines(db, (0, 1)), db.products, "meh", now).code == "invalid_reason"


def test_cancelled_orders_have_nothing_to_return(db: SimDB) -> None:
    db = delivered_1001(db)
    db.orders["#1001"].cancelled_at = DELIVERED
    now = parse_instant(DELIVERED) + timedelta(days=1)
    assert can_return(db.orders["#1001"], lines(db, (0, 1)), db.products, "unwanted", now).code == "already_cancelled"


def test_return_terms_follow_the_policy() -> None:
    assert "No return shipping fee" in return_terms("defective", "US")["fee"]
    assert "7.50 USD" in return_terms("size_too_small", "US")["fee"]
    assert "own cost" in return_terms("unwanted", "CA")["label"]
    assert "prepaid" in return_terms("unwanted", "US")["label"]
