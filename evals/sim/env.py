"""Builds each conversation's store: the seed, the task's overlays, and the frozen clock."""

from evals.sim.schema import InitialState, Task
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import format_instant, parse_ago, parse_instant
from mcp_server.simdb import SimDB, load_db
from mcp_server.tools import execute

SEED_PATH = "data/sim/seed.json"


class TaskError(ValueError):
    pass


def load_seed(path: str = SEED_PATH) -> SimDB:
    return load_db(path)


def build_db(seed: SimDB, state: InitialState) -> SimDB:
    db = seed.copy_fresh()
    now = parse_instant(db.meta.frozen_now)
    for name, ago in state.place.items():
        if name not in db.orders:
            raise TaskError(f"overlay places unknown order {name}")
        stamp = format_instant(now - parse_ago(ago))
        db.orders[name].processed_at = stamp
        db.orders[name].created_at = stamp
    for name, ago in state.deliver.items():
        order = db.orders.get(name)
        if order is None or not order.fulfillments:
            raise TaskError(f"overlay delivers {name}, which has no fulfillment")
        for fulfillment in order.fulfillments:
            fulfillment.delivered_at = format_instant(now - parse_ago(ago))
            fulfillment.display_status = "DELIVERED"
    for handle, tag in state.tag_products.items():
        product = next((p for p in db.products.values() if p.handle == handle), None)
        if product is None:
            raise TaskError(f"overlay tags unknown product {handle}")
        product.tags.append(tag)
    return db


def target_db(seed: SimDB, task: Task) -> SimDB:
    db = build_db(seed, task.initial_state)
    backend = SimStoreBackend(db)
    for action in task.evaluation_criteria.write_actions():
        result = execute(backend, action.name, action.arguments)
        if not result.get("ok"):
            raise TaskError(f"{task.id}: reference {action.name} is refused: {result.get('code')}")
    return db
