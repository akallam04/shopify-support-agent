from datetime import timedelta

import pytest

from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import FrozenClock, parse_instant
from mcp_server.simdb import SimDB, db_hash
from mcp_server.tools import ORDER_NOT_FOUND, ToolInputError, execute, idempotency_key, prepare

PLACED_1002 = parse_instant("2026-07-07T00:26:36Z")
MAYA = "maya.thompson@example.com"
CANCEL_1002 = {"order_number": "#1002", "email": MAYA, "reason": "changed_mind"}
NEW_ADDRESS = {
    "order_number": "#1002",
    "email": MAYA,
    "address1": "9 Pine St",
    "city": "Boulder",
    "province_code": "co",
    "zip": "80302",
    "country_code": "us",
}


def store(db: SimDB, after_placed: timedelta = timedelta(hours=1)) -> SimStoreBackend:
    return SimStoreBackend(db, FrozenClock(PLACED_1002 + after_placed))


def test_cancel_inside_the_window_is_applied_and_audited(db: SimDB) -> None:
    backend = store(db)
    result = execute(backend, "cancel_order", CANCEL_1002)
    assert result["ok"] is True
    assert db.orders["#1002"].cancelled_at == "2026-07-07T01:26:36Z"
    assert (db.orders["#1002"].financial_status, db.orders["#1002"].fulfillment_status) == ("REFUNDED", "FULFILLMENT_NOT_REQUIRED")
    assert [a["outcome"] for a in backend.audit] == ["executed"]


def test_replaying_the_same_write_applies_it_once(db: SimDB) -> None:
    backend = store(db)
    first = execute(backend, "cancel_order", CANCEL_1002)
    after_first = db_hash(db)
    second = execute(backend, "cancel_order", CANCEL_1002)
    assert second == {**first, "duplicate": True}
    assert db_hash(db) == after_first
    assert [a["outcome"] for a in backend.audit] == ["executed", "duplicate"]


def test_a_refused_write_changes_nothing(db: SimDB) -> None:
    backend = store(db, timedelta(days=1))
    before = db_hash(db)
    result = execute(backend, "cancel_order", CANCEL_1002)
    assert result["ok"] is False
    assert result["code"] == "change_window_passed"
    assert db_hash(db) == before
    assert backend.executed == {}


def test_writes_with_a_wrong_email_look_like_a_missing_order(db: SimDB) -> None:
    backend = store(db)
    wrong = prepare(backend, "cancel_order", {**CANCEL_1002, "email": "jordan.lee@example.com"})
    missing = prepare(backend, "cancel_order", {**CANCEL_1002, "order_number": "#9999"})
    assert (wrong.code, wrong.reason) == (missing.code, missing.reason) == ("order_not_found", ORDER_NOT_FOUND["message"])
    assert wrong.facts == missing.facts == {}


def test_policy_is_checked_again_at_execution_time(db: SimDB) -> None:
    backend = store(db)
    assert prepare(backend, "cancel_order", CANCEL_1002).allowed
    backend.clock = FrozenClock(PLACED_1002 + timedelta(hours=3))
    assert execute(backend, "cancel_order", CANCEL_1002)["code"] == "change_window_passed"
    assert db.orders["#1002"].cancelled_at is None


def test_address_change_normalizes_codes(db: SimDB) -> None:
    backend = store(db)
    result = execute(backend, "update_shipping_address", NEW_ADDRESS)
    address = db.orders["#1002"].shipping_address
    assert (address.province_code, address.country_code) == ("CO", "US")
    assert "9 Pine St, Boulder, CO, 80302, US" in result["message"]


def test_incomplete_address_is_refused(db: SimDB) -> None:
    result = execute(store(db), "update_shipping_address", {**NEW_ADDRESS, "zip": ""})
    assert result["code"] == "incomplete_address"


def delivered(db: SimDB, days_ago: int) -> SimStoreBackend:
    db.orders["#1001"].fulfillments[0].delivered_at = "2026-07-10T18:00:00Z"
    return SimStoreBackend(db, FrozenClock(parse_instant("2026-07-10T18:00:00Z") + timedelta(days=days_ago)))


def return_args(*items: dict, reason: str = "size_too_small") -> dict:
    return {"order_number": "#1001", "email": MAYA, "reason": reason, "items": list(items)}


def test_return_inside_the_window_is_requested(db: SimDB) -> None:
    backend = delivered(db, 5)
    result = execute(backend, "request_return", return_args({"title": "stormline rain jacket", "quantity": 1}))
    assert result["ok"] is True
    assert "7.50 USD" in result["fee"]
    assert [(r.status, r.line_items[0].line_item_id) for r in db.orders["#1001"].returns] == [("REQUESTED", "l1")]


def test_return_of_an_item_not_on_the_order(db: SimDB) -> None:
    result = execute(delivered(db, 5), "request_return", return_args({"title": "Ember 750 Sleeping Bag"}))
    assert result["code"] == "item_not_found"
    assert "Stormline Rain Jacket" in result["reason"]


def test_return_of_a_final_sale_item(db: SimDB) -> None:
    db.products["p1"].tags.append("final-sale")
    result = execute(delivered(db, 5), "request_return", return_args({"title": "Stormline Rain Jacket"}))
    assert result["code"] == "final_sale"


def test_return_needs_the_variant_when_a_title_repeats(db: SimDB) -> None:
    order = db.orders["#1001"]
    order.line_items.append(order.line_items[0].model_copy(update={"line_item_id": "l9", "variant_title": "L"}))
    shipped = order.fulfillments[0].line_items
    shipped.append(shipped[0].model_copy(update={"fulfillment_line_item_id": "fl9", "line_item_id": "l9"}))
    backend = delivered(db, 5)
    assert execute(backend, "request_return", return_args({"title": "Stormline Rain Jacket"}))["code"] == "ambiguous_item"
    chosen = execute(backend, "request_return", return_args({"title": "Stormline Rain Jacket", "variant": "L"}))
    assert chosen["ok"] is True


def test_malformed_items_raise_before_touching_the_store(db: SimDB) -> None:
    with pytest.raises(ToolInputError):
        prepare(delivered(db, 5), "request_return", return_args({"quantity": 1}))


def test_writes_in_any_order_reach_the_same_hash(db: SimDB) -> None:
    other = db.copy_fresh()
    a, b = store(db), store(other)
    cancel_1004 = {"order_number": "#1004", "email": "jordan.lee@example.com", "reason": "ordered_by_mistake"}
    a.clock = b.clock = FrozenClock(PLACED_1002 + timedelta(minutes=30))
    execute(a, "update_shipping_address", NEW_ADDRESS)
    execute(a, "cancel_order", cancel_1004)
    execute(b, "cancel_order", cancel_1004)
    execute(b, "update_shipping_address", NEW_ADDRESS)
    assert db_hash(db) == db_hash(other)


def test_handoffs_are_recorded_outside_the_hashed_state(db: SimDB) -> None:
    backend = store(db)
    before = db_hash(db)
    result = execute(backend, "transfer_to_human", {"summary": "Wants a warranty claim on a parka."})
    assert result["ok"] is True
    assert backend.handoffs[0]["summary"] == "Wants a warranty claim on a parka."
    assert db_hash(db) == before


def test_confirmation_summary_names_the_order_items_and_total(db: SimDB) -> None:
    prepared = prepare(store(db), "cancel_order", CANCEL_1002)
    assert "#1002" in prepared.summary and "169.98 USD" in prepared.summary and "Stormline Rain Jacket" in prepared.summary


def test_idempotency_key_depends_only_on_the_normalized_action() -> None:
    assert idempotency_key("cancel_order", {"a": 1, "b": 2}) == idempotency_key("cancel_order", {"b": 2, "a": 1})
    assert idempotency_key("cancel_order", {"a": 1}) != idempotency_key("request_return", {"a": 1})


def test_backends_without_write_support_reject_writes(db: SimDB) -> None:
    backend = store(db)
    backend.supports_writes = False
    with pytest.raises(ToolInputError):
        execute(backend, "cancel_order", CANCEL_1002)


def test_items_can_be_named_the_way_customers_say_them(db: SimDB) -> None:
    db.products["p1"].tags.append("final-sale")
    result = execute(delivered(db, 5), "request_return", return_args({"title": "rain jacket", "quantity": 1}))
    assert result["code"] == "final_sale"


def test_a_partial_name_that_fits_two_items_is_ambiguous(db: SimDB) -> None:
    order = db.orders["#1001"]
    order.line_items.append(order.line_items[0].model_copy(update={"line_item_id": "l9", "title": "Northwind Fleece Jacket", "variant_title": "L"}))
    result = execute(delivered(db, 5), "request_return", return_args({"title": "jacket"}))
    assert result["code"] == "ambiguous_item"


def test_a_new_address_takes_the_customer_name_when_none_is_on_file(db: SimDB) -> None:
    execute(store(db), "update_shipping_address", NEW_ADDRESS)
    address = db.orders["#1002"].shipping_address
    assert (address.first_name, address.last_name) == ("Maya", "Thompson")


@pytest.mark.parametrize("variant", ["M", "m", "size M", "Size m"])
def test_variants_match_the_way_customers_write_them(db: SimDB, variant: str) -> None:
    result = execute(delivered(db, 5), "request_return", return_args({"title": "Stormline Rain Jacket", "variant": variant, "quantity": 1}))
    assert result["ok"] is True


def test_a_variant_not_on_the_order_is_not_found(db: SimDB) -> None:
    result = execute(delivered(db, 5), "request_return", return_args({"title": "Stormline Rain Jacket", "variant": "size L"}))
    assert result["code"] == "item_not_found"
    assert "Stormline Rain Jacket (M)" in result["reason"]
