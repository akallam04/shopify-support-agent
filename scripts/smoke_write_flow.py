"""Real-model smoke test of the write flow on the simulated store: a few fixed conversations.

Each scenario runs on its own fresh copy of the seed with its own frozen clock, staged in
memory, so nothing on disk or in the live store changes. Prints a cost estimate first and
stops before the next turn once --max-usd is reached.

Run from the repo root: .venv/bin/python -m scripts.smoke_write_flow [--max-usd 0.10]
"""

import argparse
import asyncio
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from app.agent.graph import build_graph
from app.agent.tool_executor import InProcessTools
from app.config import get_settings
from app.costs import usage_cost
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import FrozenClock, format_instant, parse_instant
from mcp_server.simdb import SimDB, db_hash, load_db

MAYA = "maya.thompson@example.com"
EST_COST_PER_TURN = 0.006


def stage_cancel(db: SimDB) -> FrozenClock:
    return FrozenClock(parse_instant(db.orders["#1002"].processed_at) + timedelta(hours=1))


def stage_final_sale(db: SimDB) -> FrozenClock:
    now = parse_instant("2026-07-15T16:00:00Z")
    db.orders["#1001"].fulfillments[0].delivered_at = format_instant(now - timedelta(days=5))
    jacket = db.orders["#1001"].line_items[0].product_id
    db.products[jacket].tags.append("final-sale")
    return FrozenClock(now)


SCENARIOS: list[tuple[str, Callable[[SimDB], FrozenClock], list[str]]] = [
    (
        "confirmed cancel",
        stage_cancel,
        [f"Hi, please cancel order #1002. My email is {MAYA}. I ordered it by mistake.", "Yes, go ahead."],
    ),
    (
        "final-sale return",
        stage_final_sale,
        [f"I'd like to return the rain jacket from order #1001, my email is {MAYA}. It's too small."],
    ),
    (
        "change of mind",
        stage_cancel,
        [
            f"Please cancel order #1002, email {MAYA}. I changed my mind about it.",
            "Actually, wait. Can you change the shipping address instead to 9 Pine St, Boulder, CO 80302, US?",
        ],
    ),
]


async def run(max_usd: float) -> None:
    settings = get_settings().model_copy(update={"write_actions": True})
    seed = load_db(settings.sim_seed_path)
    turns = sum(len(t) for _, _, t in SCENARIOS)
    print(f"estimate: {turns} turns at about ${EST_COST_PER_TURN} each, about ${turns * EST_COST_PER_TURN:.3f}; cap ${max_usd}")
    spent = 0.0
    for name, stage, user_turns in SCENARIOS:
        db = seed.copy_fresh()
        backend = SimStoreBackend(db, stage(db))
        tools = InProcessTools(backend, include_writes=True)
        await tools.start()
        graph = build_graph(settings, object(), tools)
        before = db_hash(db)
        messages: list[dict[str, Any]] = []
        carry: dict[str, Any] = {}
        print(f"\n=== {name} ===")
        for text in user_turns:
            if spent >= max_usd:
                print(f"stopping: spent ${spent:.4f} of ${max_usd}")
                return
            messages.append({"role": "user", "content": text})
            state = await graph.ainvoke({"messages": messages, **carry})
            spent += usage_cost(state.get("usage", []))
            messages.append({"role": "assistant", "content": state["response"]})
            carry = {"pending_action": state.get("pending_action"), "executed_actions": state.get("executed_actions", [])}
            steps = [t.get("step") + (f":{t['label']}" if "label" in t else "") for t in state.get("gate_trace", [])]
            print(f"customer: {text}")
            print(f"agent:    {state['response']}")
            print(f"          gate {steps} pending={bool(carry['pending_action'])}")
        print(f"state changed: {db_hash(db) != before}; audit: {json.dumps([a['outcome'] + ' ' + a['action'] for a in backend.audit])}")
    print(f"\nspent ${spent:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-usd", type=float, default=0.10)
    asyncio.run(run(parser.parse_args().max_usd))


if __name__ == "__main__":
    main()
