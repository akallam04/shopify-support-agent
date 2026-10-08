import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.graph import build_graph
from app.agent.nodes.gate import classify_fast, reason_from_customer
from app.agent.prompts import (
    HANDOFF_FOLLOWUP,
    ORDER_CONFIRM_RULE,
    ORDER_GATE_RULE,
    ROUTER_WRITE_RULE,
    CONFIRM_CLASSIFIER_SCHEMA,
    DECLINED_RESPONSE,
    HANDOFF_RESPONSE,
    REASON_ASK,
    REFLECTION_SCHEMA,
    ROUTER_SCHEMA,
)
from app.agent.tool_executor import InProcessTools
from app.config import Settings
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import FrozenClock, parse_instant
from mcp_server.simdb import SimDB, db_hash, load_db
from tests.conftest import make_db

PLACED_1002 = parse_instant("2026-07-07T00:26:36Z")
MAYA = "maya.thompson@example.com"
CANCEL_ARGS = {"order_number": "#1002", "email": MAYA, "reason": "changed_mind", "reason_quote": "I changed my mind"}
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
    assert "is cancelled" in third["response"]
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


def test_a_correction_at_confirmation_sends_the_order_model_back_to_the_tool(db: SimDB) -> None:
    corrected = {**CANCEL_ARGS, "reason": "ordered_by_mistake", "reason_quote": "I ordered it by mistake"}
    script = {
        "route": [ORDER_ROUTE, ORDER_ROUTE],
        "order_tools": [("cancel_order", CANCEL_ARGS), ("cancel_order", corrected)],
        "confirm": ["change"],
    }
    graph, client, _, history, state = first_turn(db, script)
    after = turn(graph, history + [{"role": "user", "content": "Actually the reason is that I ordered it by mistake"}], carry(state))
    first, second = [" ".join(b["text"] for b in kw["system"]) for kind, kw in client.calls if kind == "order_tools"]
    assert "replied with something different" not in first
    assert "replied with something different instead: cancel order #1002" in second
    assert after["pending_action"]["args"]["reason"] == "ordered_by_mistake"
    assert after["response"].startswith("Just to confirm, I will cancel order #1002")
    assert db.orders["#1002"].cancelled_at is None


def test_a_draft_that_asks_for_a_yes_is_sent_back_to_call_the_tool(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": ["Cancel order #1002, is that correct?", ("cancel_order", CANCEL_ARGS)]}
    _, client, _, _, state = first_turn(db, script)
    first, second = [" ".join(b["text"] for b in kw["system"]) for kind, kw in client.calls if kind == "order_tools"]
    assert "Do not ask for confirmation yourself" not in first
    assert "Do not ask for confirmation yourself" in second
    assert {"step": "self_confirm_nudge"} in state["gate_trace"]
    assert state["response"].startswith("Just to confirm, I will cancel order #1002")
    assert db.orders["#1002"].cancelled_at is None


class ThinkingClient(ScriptedClient):
    async def create(self, **kwargs: Any) -> SimpleNamespace:
        if "tools" in kwargs and self.script.get("thinking_first"):
            self.script["thinking_first"] = False
            self.calls.append(("order_tools", {**kwargs, "messages": list(kwargs["messages"])}))
            step = self.script["order_tools"].pop(0)
            return reply(SimpleNamespace(type="thinking", thinking="", signature="sig"), tool_use(*step))
        if "tools" in kwargs:
            kwargs = {**kwargs, "messages": list(kwargs["messages"])}
        return await super().create(**kwargs)


def test_the_nudge_restarts_the_loop_when_thinking_blocks_would_be_sent_back(db: SimDB) -> None:
    status = ("get_order_status", {"order_number": "#1002", "email": MAYA})
    script = {"route": [ORDER_ROUTE], "thinking_first": True, "order_tools": [status, "Cancel order #1002, is that correct?", status, ("cancel_order", CANCEL_ARGS)]}
    backend = SimStoreBackend(db, FrozenClock(PLACED_1002 + timedelta(hours=1)))
    tools = InProcessTools(backend, include_writes=True)
    asyncio.run(tools.start())
    client = ThinkingClient(script)
    graph = build_graph(Settings(_env_file=None, anthropic_api_key="test", write_actions=True), object(), tools, client=client)
    state = turn(graph, [{"role": "user", "content": ASK}])
    calls = [kw for kind, kw in client.calls if kind == "order_tools"]
    assert len(calls) == 4
    assert len(calls[2]["messages"]) == 1
    assert {"step": "self_confirm_nudge"} in state["gate_trace"]
    assert state["response"].startswith("Just to confirm, I will cancel order #1002")


@pytest.mark.parametrize(
    ("draft", "flags", "nudged"),
    [
        ("Which order would you like to cancel?", {}, False),
        ("Cancel order #1002, is that correct?", {"mutation_gate": False}, False),
        ("Cancel order #1002, is that correct?", {"gate_confirmation": False}, False),
    ],
)
def test_the_nudge_fires_only_when_the_gate_will_ask(db: SimDB, draft: str, flags: dict, nudged: bool) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [draft, "(second draft)"]}
    _, client, _, _, state = first_turn(db, script, **flags)
    assert client.count("order_tools") == (2 if nudged else 1)
    assert ({"step": "self_confirm_nudge"} in state.get("gate_trace", [])) is nudged
    assert state["response"] == draft


JORDAN = "jordan.lee@example.com"
RETURN_ASK = f"I want to return the rain jacket from order #1022. My email is {JORDAN}."
RETURN_ROUTE = {"intent": "order", "search_query": "", "order_number": "#1022", "email": JORDAN}


def seed_turn(script: dict, text: str):
    backend = SimStoreBackend(load_db("data/sim/seed.json"))
    tools = InProcessTools(backend, include_writes=True)
    asyncio.run(tools.start())
    client = ScriptedClient(script)
    graph = build_graph(Settings(_env_file=None, anthropic_api_key="test", write_actions=True), object(), tools, client=client)
    return client, turn(graph, [{"role": "user", "content": text}])


def test_a_guessed_return_reason_goes_back_to_the_model_and_then_the_customer_is_asked() -> None:
    guessed = {"order_number": "#1022", "email": JORDAN, "items": [{"title": "Stormline Rain Jacket"}], "reason": "size_too_large", "reason_quote": "it is too big"}
    client, state = seed_turn({"route": [RETURN_ROUTE], "order_tools": [("request_return", guessed), ("request_return", guessed)]}, RETURN_ASK)
    assert client.count("order_tools") == 2
    retry_system = " ".join(b["text"] for b in [kw for kind, kw in client.calls if kind == "order_tools"][1]["system"])
    assert "not something the customer wrote" in retry_system
    assert state["response"] == REASON_ASK
    assert [t["found"] for t in state["gate_trace"] if t["step"] == "reason_check"] == [False, False]
    assert not state.get("pending_action")


def test_a_quoted_return_reason_reaches_the_confirmation_with_its_check() -> None:
    said = {"order_number": "#1022", "email": JORDAN, "items": [{"title": "Stormline Rain Jacket"}], "reason": "size_too_large", "reason_quote": "It is too big"}
    _, state = seed_turn({"route": [RETURN_ROUTE], "order_tools": [("request_return", said)]}, RETURN_ASK.replace("#1022.", "#1022. It is too big."))
    assert state["pending_action"]["checks"][-1] == "The reason came from the customer"


def test_a_declined_router_call_is_treated_as_out_of_scope(db: SimDB) -> None:
    class Declines(ScriptedClient):
        async def create(self, **kwargs: Any) -> SimpleNamespace:
            response = await super().create(**kwargs)
            return SimpleNamespace(content=[], usage=response.usage, stop_reason="refusal")

    graph_declines = build_graph(Settings(_env_file=None, anthropic_api_key="test", write_actions=True), object(), graph_tools(db), client=Declines({"route": [ORDER_ROUTE]}))
    state = turn(graph_declines, [{"role": "user", "content": ASK}])
    assert state["intent"] == "out_of_scope" and not state.get("pending_action")


def graph_tools(db: SimDB) -> InProcessTools:
    tools = InProcessTools(SimStoreBackend(db, FrozenClock(PLACED_1002 + timedelta(hours=1))), include_writes=True)
    asyncio.run(tools.start())
    return tools


def test_a_cancel_needs_no_reason_and_never_asks_for_one(db: SimDB) -> None:
    unsaid = {"order_number": "#1002", "email": MAYA}
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", unsaid)]}
    graph, client, _ = make(db, script)
    state = turn(graph, [{"role": "user", "content": f"Please cancel order #1002, my email is {MAYA}."}])
    assert client.count("order_tools") == 1
    assert state["pending_action"]["action"] == "cancel_order"
    assert "The reason came from the customer" not in state["pending_action"]["checks"]
    assert not [t for t in state["gate_trace"] if t["step"] == "reason_check"]


def test_a_guessed_cancel_reason_is_dropped_instead_of_asked(db: SimDB) -> None:
    guessed = {**CANCEL_ARGS, "reason_quote": "it arrived too late"}
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", guessed)]}
    _, client, _, _, state = first_turn(db, script)
    assert client.count("order_tools") == 1
    assert state["pending_action"]["args"]["reason"] == "" and state["pending_action"]["args"]["reason_quote"] == ""
    assert [(t["found"], t.get("optional")) for t in state["gate_trace"] if t["step"] == "reason_check"] == [(False, True)]
    assert db.orders["#1002"].cancelled_at is None


@pytest.mark.parametrize(
    ("quote", "said", "digest", "found"),
    [
        ("I changed my mind!", "Cancel #1002. I changed my mind.", "", True),
        ("changed  my MIND", "cancel it, changed my mind", "", True),
        ("too small", "please cancel #1002", "Customer said: the jacket is too small", True),
        ("it is too big", "I want to return the jacket", "", False),
        ("ok", "ok, cancel it", "", False),
    ],
)
def test_the_reason_must_be_words_the_customer_wrote(quote: str, said: str, digest: str, found: bool) -> None:
    state = {"messages": [{"role": "user", "content": said}, {"role": "assistant", "content": "it is too big?"}], "context_digest": digest}
    assert reason_from_customer(quote, state) is found


def test_policy_refuses_before_reflection_or_confirmation(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED]}
    graph, client, _, _, state = first_turn(db, script, after_placed=timedelta(days=2))
    assert "more than 2 hours ago" in state["response"]
    assert not state.get("pending_action")
    assert client.count("reflect") == 0
    assert [t["step"] for t in state["gate_trace"]] == ["reason_check", "policy"]
    assert state["gate_trace"][0]["found"] and state["gate_trace"][1]["code"] == "change_window_passed"


def test_a_wrong_email_gets_the_not_found_answer(db: SimDB) -> None:
    wrong = {**CANCEL_ARGS, "email": "jordan.lee@example.com"}
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", wrong)], "reflect": [PROCEED]}
    _, _, _, _, state = first_turn(db, script)
    assert state["response"].startswith("No order found matching that order number and email address.")
    assert not state.get("pending_action")


def test_reflection_can_stop_a_mismatched_action(db: SimDB) -> None:
    question = "Just to check, did you want to cancel the whole order or only one item?"
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [{"verdict": "ask", "issues": ["scope"], "question": question}]}
    _, _, _, _, state = first_turn(db, script, gate_reflection=True)
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
    other = make_db()
    _, _, _, _, refused = first_turn(other, late, after_placed=timedelta(days=2), mutation_gate=False)
    assert other.orders["#1002"].cancelled_at is None
    assert json.loads(refused["tool_results"][-1]["result"])["code"] == "change_window_passed"


@pytest.mark.parametrize(
    ("flags", "prompt_confirms"),
    [({}, False), ({"mutation_gate": False}, True), ({"gate_confirmation": False}, True)],
)
def test_the_prompt_asks_for_a_yes_only_when_the_gate_does_not(db: SimDB, flags: dict, prompt_confirms: bool) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": ["Which order did you mean?"]}
    _, client, _, _, _ = first_turn(db, script, **flags)
    system = " ".join(b["text"] for b in next(kw["system"] for kind, kw in client.calls if kind == "order_tools"))
    assert (ORDER_CONFIRM_RULE in system) is prompt_confirms
    assert (ORDER_GATE_RULE in system) is not prompt_confirms


def test_confirmation_off_executes_after_reflection(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED]}
    _, client, _, _, state = first_turn(db, script, gate_confirmation=False, gate_reflection=True)
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
    route_system = " ".join(b["text"] for b in route_call["system"])
    assert "#1002" in route_system and MAYA in route_system


def test_the_thinking_switch_reaches_every_model_call(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "confirm": ["confirm"]}
    graph, client, _, messages, state = first_turn(db, script, router_model="claude-haiku-5-5", answer_model="claude-haiku-5-5", model_thinking=False)
    turn(graph, messages + [{"role": "user", "content": "hmm I guess that works for me"}], carry(state))
    assert client.count("route") == 1 and client.count("order_tools") == 1 and client.count("confirm") == 1
    assert all(kw["thinking"] == {"type": "disabled"} for _, kw in client.calls)


def test_a_correctable_input_mistake_goes_back_to_the_model_once(db: SimDB) -> None:
    bad = {**CANCEL_ARGS, "reason": "because"}
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", bad), ("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED]}
    _, client, _, _, state = first_turn(db, script)
    assert client.count("order_tools") == 2
    retry_system = " ".join(b["text"] for b in [kw for kind, kw in client.calls if kind == "order_tools"][1]["system"])
    assert "was not run" in retry_system
    assert state["pending_action"]["action"] == "cancel_order"
    assert db.orders["#1002"].cancelled_at is None


def test_a_repeated_input_mistake_reaches_the_customer_after_one_retry(db: SimDB) -> None:
    bad = {**CANCEL_ARGS, "reason": "because"}
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", bad), ("cancel_order", bad)]}
    _, client, _, _, state = first_turn(db, script)
    assert client.count("order_tools") == 2
    assert "must be one of" in state["response"]
    assert not state.get("pending_action")


@pytest.mark.parametrize("writes", [True, False])
def test_the_router_sends_questions_about_changing_an_order_to_the_tools_only_when_it_can_act(db: SimDB, writes: bool) -> None:
    script = {"route": [{"intent": "smalltalk", "search_query": "", "order_number": "", "email": ""}]}
    graph, client, _ = make(db, script, writes=writes)
    turn(graph, [{"role": "user", "content": "Can I return the second sleeping bag?"}])
    route_system = " ".join(b["text"] for b in next(kw for kind, kw in client.calls if kind == "route")["system"])
    assert (ROUTER_WRITE_RULE.strip() in route_system) is writes


@pytest.mark.parametrize(
    ("text", "label"),
    [("no", "decline"), ("No thanks", "decline"), ("no, don't cancel it", "decline"),
     ("No, I don't want to cancel it anymore. Can you change the shipping address instead?", None),
     ("yes please", "confirm")],
)
def test_only_short_replies_take_the_fast_path(text: str, label: str | None) -> None:
    assert classify_fast(text) == label


def test_asking_again_for_a_person_gets_a_follow_up_without_a_second_handoff(db: SimDB) -> None:
    route = {"intent": "handoff", "search_query": "", "order_number": "", "email": ""}
    graph, _, backend = make(db, {"route": [route, route]})
    first = turn(graph, [{"role": "user", "content": "I want to talk to a real person."}])
    history = [{"role": "user", "content": "I want to talk to a real person."}, {"role": "assistant", "content": first["response"]},
               {"role": "user", "content": "No, connect me to a live agent now."}]
    second = turn(graph, history)
    assert first["response"] == HANDOFF_RESPONSE and second["response"] == HANDOFF_FOLLOWUP
    assert len(backend.handoffs) == 1


def test_reflection_is_off_by_default_and_the_switch_turns_it_on(db: SimDB) -> None:
    assert Settings(_env_file=None).gate_reflection is False
    def script() -> dict:
        return {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)], "reflect": [PROCEED]}

    _, client, _, _, state = first_turn(db, script())
    assert client.count("reflect") == 0 and state["pending_action"]["action"] == "cancel_order"
    _, switched, _, _, _ = first_turn(make_db(), script(), gate_reflection=True)
    assert switched.count("reflect") == 1


def test_every_turn_records_how_long_each_node_took(db: SimDB) -> None:
    script = {"route": [ORDER_ROUTE], "order_tools": [("cancel_order", CANCEL_ARGS)]}
    _, _, _, _, state = first_turn(db, script)
    nodes = [t["node"] for t in state["timings"]]
    assert nodes == ["sanitize", "context", "route", "order_tools", "gate", "verify"]
    assert all(t["ms"] >= 0 for t in state["timings"])
