import json
import stat
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from mcp_server.simdb import ReturnLine, ReturnRequest, SimDB, db_hash, load_db, save_db

SEED_PATH = "data/sim/seed.json"
SEED_HASH = "19c5e7635c7f28713076076c07f633407fb5f1c1955227199e2ec1381e4c9ae9"


def test_hash_ignores_key_and_list_order(db: SimDB) -> None:
    before = db_hash(db)
    shuffled = db.copy_fresh()
    shuffled.orders = dict(reversed(list(shuffled.orders.items())))
    shuffled.products = dict(reversed(list(shuffled.products.items())))
    for order in shuffled.orders.values():
        order.line_items.reverse()
        for f in order.fulfillments:
            f.line_items.reverse()
    shuffled.products["p1"].tags.reverse()
    assert db_hash(shuffled) == before


MUTATIONS: dict[str, Callable[[SimDB], None]] = {
    "inventory": lambda d: setattr(d.products["p1"].variants["v2"], "inventory_quantity", 13),
    "price": lambda d: setattr(d.products["p2"].variants["v3"], "price", "29.95"),
    "fulfillment status": lambda d: setattr(d.orders["#1002"], "fulfillment_status", "FULFILLED"),
    "cancellation": lambda d: setattr(d.orders["#1002"], "cancelled_at", "2026-08-12T16:00:00Z"),
    "customer email": lambda d: setattr(d.customers["c1"], "email", "someone@example.com"),
    "line quantity": lambda d: setattr(d.orders["#1001"].line_items[0], "quantity", 2),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_hash_changes_with_any_business_value(db: SimDB, name: str) -> None:
    before = db_hash(db)
    MUTATIONS[name](db)
    assert db_hash(db) != before


def test_metadata_is_outside_the_hash(db: SimDB) -> None:
    before = db_hash(db)
    db.meta.frozen_now = "2030-01-01T00:00:00Z"
    db.meta.notes.append("re-exported")
    assert db_hash(db) == before


def test_returns_recorded_in_any_order_hash_the_same(db: SimDB) -> None:
    a = ReturnRequest(status="REQUESTED", reason="SIZE_TOO_SMALL", line_items=[ReturnLine(line_item_id="l1", quantity=1)])
    b = ReturnRequest(status="REQUESTED", reason="DEFECTIVE", line_items=[ReturnLine(line_item_id="l2", quantity=1)])
    first, second = db.copy_fresh(), db.copy_fresh()
    first.orders["#1001"].returns = [a, b]
    second.orders["#1001"].returns = [b, a]
    assert db_hash(first) == db_hash(second) != db_hash(db)


def test_fresh_copies_are_isolated(db: SimDB) -> None:
    before = db_hash(db)
    copy = db.copy_fresh()
    copy.orders["#1001"].fulfillment_status = "RESTOCKED"
    assert db_hash(db) == before


def test_round_trip_keeps_hash_and_is_world_readable(db: SimDB, tmp_path) -> None:
    path = tmp_path / "seed.json"
    save_db(db, path)
    assert db_hash(load_db(path)) == db_hash(db)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_unknown_fields_are_rejected(db: SimDB, tmp_path) -> None:
    raw = db.model_dump(mode="json")
    raw["orders"]["#1001"]["loyalty_points"] = 50
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        load_db(path)


def test_committed_seed_is_pinned_and_holds_only_test_customers() -> None:
    seed = load_db(SEED_PATH)
    assert db_hash(seed) == SEED_HASH, "the seed changed, re-export deliberately and update SEED_HASH"
    assert all((c.email or "").endswith("@example.com") for c in seed.customers.values())
