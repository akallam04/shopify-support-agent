from datetime import UTC, datetime

import pytest

from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import FrozenClock, parse_instant
from mcp_server.simdb import SimDB


def test_clock_defaults_to_the_seed_frozen_time(db: SimDB) -> None:
    assert SimStoreBackend(db).clock.now() == datetime(2026, 8, 12, 16, 0, tzinfo=UTC)


def test_explicit_clock_overrides_the_seed(db: SimDB) -> None:
    clock = FrozenClock(datetime(2027, 1, 1, tzinfo=UTC))
    assert SimStoreBackend(db, clock).clock.now().year == 2027


def test_parse_instant_normalizes_to_utc() -> None:
    assert parse_instant("2026-08-12T16:00:00Z") == datetime(2026, 8, 12, 16, 0, tzinfo=UTC)
    assert parse_instant("2026-08-12T12:00:00-04:00") == datetime(2026, 8, 12, 16, 0, tzinfo=UTC)


def test_parse_instant_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError):
        parse_instant("2026-08-12T16:00:00")


def test_customer_orders_newest_first_and_case_insensitive(db: SimDB) -> None:
    names = [o.name for o in SimStoreBackend(db).orders_for_email("MAYA.Thompson@example.com", 10)]
    assert names == ["#1002", "#1001"]


def test_customer_orders_respect_the_limit(db: SimDB) -> None:
    assert len(SimStoreBackend(db).orders_for_email("maya.thompson@example.com", 1)) == 1


def test_search_needs_every_word_and_matches_prefixes(db: SimDB) -> None:
    backend = SimStoreBackend(db)
    assert [p.title for p in backend.search_products("storm jack", 3)] == ["Stormline Rain Jacket"]
    assert backend.search_products("rain bottle", 3) == []


def test_search_skips_products_that_are_not_active(db: SimDB) -> None:
    assert SimStoreBackend(db).search_products("prototype", 3) == []


def test_search_reads_descriptions_and_keeps_store_order(db: SimDB) -> None:
    db.products["p1"].description = "Fits a water bottle in the chest pocket."
    titles = [p.title for p in SimStoreBackend(db).search_products("bottle", 10)]
    assert titles == ["Stormline Rain Jacket", "Wander Insulated Bottle"]
