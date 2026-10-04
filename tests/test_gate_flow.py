import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.graph import build_graph
from app.agent.prompts import (
    CONFIRM_CLASSIFIER_SCHEMA,
    DECLINED_RESPONSE,
    HANDOFF_RESPONSE,
    REFLECTION_SCHEMA,
    ROUTER_SCHEMA,
)
from app.agent.tool_executor import InProcessTools
from app.config import Settings
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import FrozenClock, parse_instant
from mcp_server.simdb import SimDB, db_hash

PLACED_1002 = parse_instant("2026-07-07T00:26:36Z")
MAYA = "maya.thompson@example.com"
CANCEL_ARGS = {"order_number": "#1002", "email": MAYA, "reason": "changed_mind"}
ASK = f"Please cancel order #1002, my email is {MAYA}. I changed my mind."


def block_text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def reply(*blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), usage=SimpleNamespace(input_tokens=10, output_tokens=5), stop_reason="end_turn")


def tool_use(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=args, id=f"tu_{name}")


class ScriptedClient:
    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.calls: list[tuple[str, dict]] = []
        self.messages = self

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        schema = (kwargs.get("output_config") or {}).get("format", {}).get("schema")
        if schema is ROUTER_SCHEMA:
            kind, payload = "route", json.dumps(self.script["route"].pop(0))
        elif schema is REFLECTION_SCHEMA:
            kind, payload = "reflect", json.dumps(self.script["reflect"].pop(0))
        elif schema is CONFIRM_CLASSIFIER_SCHEMA:
            kind, payload = "confirm", json.dumps({"label": self.script["confirm"].pop(0)})
        elif "tools" in kwargs:
            self.calls.append(("order_tools", kwargs))
            step = self.script["order_tools"].pop(0)
            return reply(tool_use(*step)) if isinstance(step, tuple) else reply(block_text(step))
        else:
            kind, payload = "respond", self.script.get("respond", ["(free text)"]).pop(0)
        self.calls.append((kind, kwargs))
        return reply(block_text(payload))

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)


ORDER_ROUTE = {"intent": "order", "search_query": "", "order_number": "#1002", "email": MAYA}
PROCEED = {"verdict": "proceed", "issues": [], "question": ""}


def make(db: SimDB, script: dict, after_placed: timedelta = timedelta(hours=1), writes: bool = True, **flags: Any):
    backend = SimStoreBackend(db, FrozenClock(PLACED_1002 + after_placed))
    tools = InProcessTools(backend, include_writes=writes)
    asyncio.run(tools.start())
    settings = Settings(_env_file=None, anthropic_api_key="test", write_actions=writes, **flags)
    client = ScriptedClient(script)
    return build_graph(settings, object(), tools, client=client), client, backend


def turn(graph: Any, messages: list[dict], carry: dict | None = None) -> dict:
    return asyncio.run(graph.ainvoke({"messages": messages, **(carry or {})}))


def carry(state: dict) -> dict:
    return {"pending_action": state.get("pending_action"), "executed_actions": state.get("executed_actions", [])}


def first_turn(db: SimDB, script: dict, **kw: Any):
    graph, client, backend = make(db, script, **kw)
    messages = [{"role": "user", "content": ASK}]
    state = turn(graph, messages)
    return graph, client, backend, messages + [{"role": "assistant", "content": state["response"]}], state


def test_confirmed_cancel_runs_once_after_a_yes(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED]}
    graph, client, backend, history, state = first_turn(db, script)
    before = db_hash(db)
    assert state["pending_action"]["action"] == "cancel_order"
    assert state["response"].startswith("Just to confirm, I will cancel order #1002")
    assert db.orders["#1002"].cancelled_at is None and db_hash(db) == before

    after = turn(graph, history + [{"role": "user", "content": "Yes please"}], carry(state))
    assert db.orders["#1002"].cancelled_at is not None
    assert "is cancelled" in after["response"]
    assert after["pending_action"] is None
    assert client.count("confirm") == 0
    assert [a["outcome"] for a in backend.audit] == ["executed"]


def test_a_no_changes_nothing(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED]}
    graph, _, _, history, state = first_turn(db, script)
    before = db_hash(db)
    after = turn(graph, history + [{"role": "user", "content": "no, keep it"}], carry(state))
    assert after["response"] == DECLINED_RESPONSE
    assert after["pending_action"] is None
    assert db_hash(db) == before


def test_an_unclear_reply_asks_again_and_keeps_the_action_pending(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED], "confirm": ["unclear"]}
    graph, client, _, history, state = first_turn(db, script)
    history += [{"role": "user", "content": "hmm, would I get my money back?"}]
    second = turn(graph, history, carry(state))
    assert second["response"].startswith("Sorry, I want to be sure")
    assert second["pending_action"]["key"] == state["pending_action"]["key"]
    assert db.orders["#1002"].cancelled_at is None
    history += [{"role": "assistant", "content": second["response"]}, {"role": "user", "content": "yes"}]
    third = turn(graph, history, carry(second))
    assert db.orders["#1002"].cancelled_at is not None
    assert client.count("confirm") == 1


def test_a_change_of_mind_drops_the_pending_action(db: SimDB) -> None:
    script = {
        "route": [ORDER_ROUTE, ORDER_ROUTE],
        "order_tools": [("cancel_order", CANCEL_ARGS), "Sure, what is the new address?"],
        "reflect": [PROCEED],
        "confirm": ["change"],
    }
    graph, _, _, history, state = first_turn(db, script)
    after = turn(graph, history + [{"role": "user", "content": "Actually, change the shipping address instead"}], carry(state))
    assert after["pending_action"] is None
    assert after["response"] == "Sure, what is the new address?"
    assert db.orders["#1002"].cancelled_at is None


def test_policy_refuses_before_reflection_or_confirmation(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED]}
    graph, client, _, _, state = first_turn(db, script, after_placed=timedelta(days=2))
    assert "more than 2 hours ago" in state["response"]
    assert not state.get("pending_action")
    assert client.count("reflect") == 0
    assert [t["code"] for t in state["gate_trace"]] == ["change_window_passed"]


def test_a_wrong_email_gets_the_not_found_answer(db: SimDB) -> None:
    wrong = {**CANCEL_ARGS, "email": "jordan.lee@example.com"}
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", wrong)], "reflect": [PROCEED]}
    _, _, _, _, state = first_turn(db, script)
    assert state["response"].startswith("No order found matching that order number and email address.")
    assert not state.get("pending_action")


def test_reflection_can_stop_a_mismatched_action(db: SimDB) -> None:
    question = "Just to check, did you want to cancel the whole order or only one item?"
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [{"verdict": "ask", "issues": ["scope"], "question": question}]}
    _, _, _, _, state = first_turn(db, script)
    assert state["response"] == question
    assert not state.get("pending_action")
    assert db.orders["#1002"].cancelled_at is None


def test_gate_off_executes_immediately_but_policy_still_holds(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS), "Done, it is cancelled."]}
    _, client, _, _, state = first_turn(db, script, mutation_gate=False)
    assert db.orders["#1002"].cancelled_at is not None
    assert not state.get("pending_action")
    assert client.count("reflect") == 0

    late = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS), "I could not cancel it."]}
    other = db.copy_fresh()
    other.orders["#1002"].cancelled_at = None
    _, _, _, _, refused = first_turn(other, late, after_placed=timedelta(days=2), mutation_gate=False)
    assert other.orders["#1002"].cancelled_at is None
    assert json.loads(refused["tool_results"][-1]["result"])["code"] == "change_window_passed"


def test_confirmation_off_executes_after_reflection(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED]}
    _, client, _, _, state = first_turn(db, script, gate_confirmation=False)
    assert db.orders["#1002"].cancelled_at is not None
    assert client.count("reflect") == 1
    assert "is cancelled" in state["response"]


def test_reflection_off_goes_straight_to_confirmation(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)]}
    _, client, _, _, state = first_turn(db, script, gate_reflection=False)
    assert client.count("reflect") == 0
    assert state["pending_action"]["action"] == "cancel_order"


def test_a_replayed_confirmation_does_not_cancel_twice(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED]}
    graph, _, backend, history, state = first_turn(db, script)
    history += [{"role": "user", "content": "yes"}]
    turn(graph, history, carry(state))
    after_once = db_hash(db)
    replay = turn(graph, history, carry(state))
    assert replay["response"].startswith("That is already done.")
    assert db_hash(db) == after_once
    assert [a["outcome"] for a in backend.audit] == ["executed", "duplicate"]


def test_handoffs_are_recorded_with_the_standard_reply(db: SimDB) -> None:
    script = {"route": [{"intent": "handoff", "search_query": "", "order_number": "", "email": ""}]}
    graph, _, backend = make(db, script)
    state = turn(graph, [{"role": "user", "content": "I want to talk to a real person now."}])
    assert state["response"] == HANDOFF_RESPONSE
    assert "real person" in backend.handoffs[0]["summary"]


def test_read_only_agent_never_holds_or_records_actions(db: SimDB) -> None:
    script = {"route": [{"intent": "handoff", "search_query": "", "order_number": "", "email": ""}]}
    graph, _, backend = make(db, script, writes=False)
    state = turn(graph, [{"role": "user", "content": "Get me a manager."}])
    assert state["response"] == HANDOFF_RESPONSE
    assert backend.handoffs == []


def test_the_mcp_client_cannot_carry_write_tools() -> None:
    mcp_like = SimpleNamespace(gated_tool_names={"cancel_order"}, write_tool_names={"cancel_order"}, tool_names=set())
    with pytest.raises(RuntimeError):
        build_graph(Settings(_env_file=None, anthropic_api_key="test"), object(), mcp_like, client=ScriptedClient({}))


def test_long_conversations_send_a_digest_instead_of_old_turns(db: SimDB) -> None:
    script = {"route": [{"intent": "smalltalk", "search_query": "", "order_number": "", "email": ""}], "respond": ["Glad to help!"]}
    graph, client, _ = make(db, script, context_keep_messages=4)
    history = [
        {"role": "user", "content": f"My order is #1002 and my email is {MAYA}."},
        {"role": "assistant", "content": "Thanks, got it."},
    ] * 4 + [{"role": "user", "content": "thanks!"}]
    turn(graph, history)
    route_call = next(kw for kind, kw in client.calls if kind == "route")
    assert len(route_call["messages"]) <= 4
    assert "#1002" in route_call["system"] and MAYA in route_call["system"]
