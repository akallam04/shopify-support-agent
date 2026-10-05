import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.agent.prompts import CONFIRM_CLASSIFIER_SCHEMA, REFLECTION_SCHEMA, ROUTER_SCHEMA
from app.config import Settings
from evals.sim import compare_runs, orchestrator, user_sim
from evals.sim.config import SimSettings
from evals.sim.env import TaskError, build_db, load_seed, target_db
from evals.sim.grader import CONFIRM_SCHEMA, NL_SCHEMA, communicated, effective_change, grade
from evals.sim.make_regression_task import regression_task
from evals.sim.regrade import rebuild
from evals.sim.run_sim import Budget, load_done, record
from evals.sim.metrics import pass_hat_k, summarize
from evals.sim.orchestrator import AgentConfig, run_conversation
from evals.sim.schema import Task, load_tasks
from evals.sim.user_sim import STOP, SimulatorError, SimulatorQuotaError, UserSimulator, UserTurn
from evals.sim.validate_tasks import check_task, validate
from mcp_server.clock import parse_instant
from mcp_server.simdb import db_hash

MAYA = "maya.thompson@example.com"


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture(scope="module")
def tasks() -> dict[str, Task]:
    return {t.id: t for t in load_tasks()}


def reply(*blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), usage=SimpleNamespace(input_tokens=100, output_tokens=20), stop_reason="end_turn")


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


class FakeAnthropic:
    def __init__(self, script: dict[str, list[Any]]) -> None:
        self.script = script
        self.kinds: list[str] = []
        self.messages = self

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        schema = (kwargs.get("output_config") or {}).get("format", {}).get("schema")
        kind = {id(ROUTER_SCHEMA): "route", id(REFLECTION_SCHEMA): "reflect", id(CONFIRM_CLASSIFIER_SCHEMA): "confirm", id(NL_SCHEMA): "nl", id(CONFIRM_SCHEMA): "judge_confirm"}.get(id(schema))
        if kind is None:
            kind = "order_tools" if "tools" in kwargs else "respond"
        self.kinds.append(kind)
        step = self.script[kind].pop(0)
        if isinstance(step, tuple):
            return reply(SimpleNamespace(type="tool_use", name=step[0], input=step[1], id=f"tu_{len(self.kinds)}"))
        return reply(text(step if isinstance(step, str) else json.dumps(step)))


def sim_settings() -> SimSettings:
    return SimSettings(_env_file=None, sim_user_base_url="http://sim.test/v1", sim_user_api_key="test", sim_user_model="sim-model", max_agent_turns=6)


def scripted_customer(monkeypatch: pytest.MonkeyPatch, turns: list[UserTurn | Exception]) -> None:
    queue = list(turns)

    async def next_message(self: UserSimulator, client: Any, transcript: list[dict[str, str]]) -> UserTurn:
        self.tokens.prompt += 200
        self.tokens.completion += 10
        self.tokens.calls += 1
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(UserSimulator, "next_message", next_message)


def converse(seed, task: Task, client: FakeAnthropic, agent: AgentConfig | None = None, meter: Any = None):
    async def go():
        async with httpx.AsyncClient() as http:
            return await run_conversation(task, 0, seed, agent or AgentConfig("claude-haiku-4-5"), Settings(_env_file=None, anthropic_api_key="test"), object(), client, http, sim_settings(), meter=meter)

    return asyncio.run(go())


CANCEL_1023 = {"order_number": "#1023", "email": MAYA, "reason": "ordered_by_mistake"}
ROUTE_1023 = {"intent": "order", "search_query": "", "order_number": "#1023", "email": MAYA}
PROCEED = {"verdict": "proceed", "issues": [], "question": ""}


def test_the_shipped_tasks_pass_validation(seed, tasks) -> None:
    assert validate(list(tasks.values()), seed) == {}
    assert len(tasks) == 50


def test_the_held_out_tasks_pass_validation_and_stay_separate(seed, tasks) -> None:
    held = load_tasks("evals/sim/heldout_tasks.json")
    assert validate(held, seed) == {}
    assert len(held) == 10 and not ({t.id for t in held} & set(tasks))


def test_validation_catches_broken_tasks(seed, tasks) -> None:
    idle = tasks["cancel-shipped-refused"].model_copy(deep=True)
    idle.evaluation_criteria.reward_basis = ["DB"]
    assert any("idle agent" in p for p in check_task(idle, seed, ""))

    refused = tasks["cancel-eligible"].model_copy(deep=True)
    refused.evaluation_criteria.actions[-1].arguments["order_number"] = "#1001"
    assert any("refused" in p for p in check_task(refused, seed, ""))

    leaky = tasks["identity-no-email"].model_copy(deep=True)
    leaky.user_scenario.must_not_reveal = ["#1005"]
    assert any("must_not_reveal" in p for p in check_task(leaky, seed, ""))

    ungrounded = tasks["policy-return-fee"].model_copy(deep=True)
    ungrounded.evaluation_criteria.communicate_info = ["9.99"]
    assert any("not in any tool output" in p for p in check_task(ungrounded, seed, ""))


def test_overlays_move_the_clock_relative_dates(seed, tasks) -> None:
    db = build_db(seed, tasks["cancel-pending-payment"].initial_state)
    placed = parse_instant(db.orders["#1014"].processed_at)
    assert (parse_instant(db.meta.frozen_now) - placed).total_seconds() == 40 * 60
    assert db_hash(seed) != db_hash(db)
    delivered = build_db(seed, tasks["return-partial-quantity"].initial_state)
    assert all(f.display_status == "DELIVERED" for f in delivered.orders["#1006"].fulfillments)
    with pytest.raises(TaskError):
        build_db(seed, tasks["cancel-eligible"].initial_state.model_copy(update={"place": {"#9999": "1h"}}))


def test_target_db_applies_only_reference_writes(seed, tasks) -> None:
    assert db_hash(target_db(seed, tasks["cancel-shipped-refused"])) == db_hash(seed)
    target = target_db(seed, tasks["cancel-eligible"])
    assert target.orders["#1023"].cancelled_at is not None


def test_pass_hat_k_matches_the_combinatorial_definition() -> None:
    rewards = {"a": [1.0, 1.0, 0.0, 0.0], "b": [1.0, 1.0, 1.0, 1.0]}
    assert pass_hat_k(rewards, 1) == pytest.approx((0.5 + 1.0) / 2)
    assert pass_hat_k(rewards, 2) == pytest.approx((1 / 6 + 1.0) / 2)
    assert pass_hat_k({"a": [1.0]}, 2) is None


def test_communicate_alternatives_are_case_insensitive() -> None:
    assert communicated(["14 days", "fourteen days"], "within fourteen days of your order")
    assert not communicated("7.50", "the fee is 7 dollars")


def test_confirmed_cancel_conversation_scores_one(monkeypatch, seed, tasks) -> None:
    scripted_customer(monkeypatch, [UserTurn("Please cancel order #1023, email " + MAYA + ". I ordered it by mistake.", None), UserTurn("yes", None), UserTurn("", STOP)])
    client = FakeAnthropic({"route": [ROUTE_1023], "order_tools": [("cancel_order", CANCEL_1023)], "reflect": [PROCEED], "judge_confirm": [{"confirmed": True, "reason": "said yes"}]})
    conv = converse(seed, tasks["cancel-eligible"], client)
    assert conv.stop_reason == "user_stop"
    assert conv.sim.tokens.calls == 3

    graded = asyncio.run(grade(conv, seed, client, "claude-sonnet-5-5"))
    assert graded["reward"] == 1.0
    assert graded["writes"]["executed"] == [("cancel_order", "#1023")]
    assert graded["writes"]["unconfirmed"] == []
    assert graded["agent_cost_usd"] > 0


def test_a_write_without_a_yes_is_flagged_and_forbidden_writes_zero_the_reward(monkeypatch, seed, tasks) -> None:
    scripted_customer(monkeypatch, [UserTurn("I'm thinking about cancelling #1023, email " + MAYA + ".", None), UserTurn("", STOP)])
    client = FakeAnthropic({"route": [ROUTE_1023], "order_tools": [("cancel_order", CANCEL_1023), "Done, it is cancelled."], "judge_confirm": [{"confirmed": False, "reason": "no yes"}], "nl": [{"results": [{"index": 0, "holds": False, "reason": "it did"}]}]})
    conv = converse(seed, tasks["confirm-decline"], client, AgentConfig("claude-haiku-4-5", mutation_gate=False))
    graded = asyncio.run(grade(conv, seed, client, "claude-sonnet-5-5"))
    assert graded["reward"] == 0.0
    assert len(graded["writes"]["unconfirmed"]) == 1
    assert len(graded["writes"]["forbidden"]) == 1
    assert "nl" not in client.kinds


def test_quota_errors_end_the_conversation_unrecorded(monkeypatch, seed, tasks) -> None:
    scripted_customer(monkeypatch, [SimulatorQuotaError("out of quota")])
    conv = converse(seed, tasks["cancel-eligible"], FakeAnthropic({}))
    assert conv.stop_reason in orchestrator.UNRECORDED


def test_agent_infra_errors_are_retryable_not_agent_failures() -> None:
    request = httpx.Request("POST", "https://api.anthropic.test")
    overloaded = __import__("anthropic").OverloadedError("busy", response=httpx.Response(529, request=request), body=None)
    assert orchestrator.classify_agent_error(overloaded) == "infra_error"
    assert orchestrator.classify_agent_error(ValueError("bug")) == "agent_error"


def simulator(handler) -> tuple[UserSimulator, httpx.AsyncClient]:
    task = load_tasks()[0]
    sim = UserSimulator(task.user_scenario, "m", "http://sim.test/v1", "test", retries=1)
    return sim, httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run_sim_call(sim: UserSimulator, client: httpx.AsyncClient, transcript: list[dict[str, str]]):
    async def go():
        async with client:
            return await sim.next_message(client, transcript)

    return asyncio.run(go())


def test_the_simulator_sees_roles_swapped_and_citations_stripped() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": f"Thanks! {STOP}"}}], "usage": {"prompt_tokens": 50, "completion_tokens": 4}})

    sim, client = simulator(handler)
    turn = run_sim_call(sim, client, [{"role": "user", "content": "Where is #1001?"}, {"role": "assistant", "content": "It shipped [policy-shipping]."}])
    assert turn.signal == STOP and turn.text == "Thanks!"
    assert [m["role"] for m in seen["messages"]] == ["system", "user", "assistant", "user"]
    assert seen["messages"][-1]["content"] == "It shipped."
    assert seen["enable_thinking"] is False
    assert sim.tokens.total == 54


def test_quota_and_throttling_responses_are_told_apart(monkeypatch) -> None:
    monkeypatch.setattr(user_sim.asyncio, "sleep", _instant)
    sim, client = simulator(lambda r: httpx.Response(403, json={"code": "AllocationQuota.FreeTierOnly", "message": "free tier quota exhausted"}))
    with pytest.raises(SimulatorQuotaError):
        run_sim_call(sim, client, [])
    sim, client = simulator(lambda r: httpx.Response(429, json={"code": "Throttling.RateQuota", "message": "Requests rate limit exceeded"}))
    with pytest.raises(SimulatorError) as raised:
        run_sim_call(sim, client, [])
    assert not isinstance(raised.value, SimulatorQuotaError)


async def _instant(*_: Any) -> None:
    return None


def test_summaries_count_unsafe_writes_and_skip_excluded_runs() -> None:
    def rec(task: str, reward: float, executed=(), reference=(), unconfirmed=0) -> dict[str, Any]:
        return {
            "task_id": task, "category": "cancel", "trial": 0, "stop_reason": "user_stop", "agent_turns": 2,
            "turn_latency_s": [1.0, 2.0], "sim_tokens": {"total": 1000}, "excluded": None,
            "grade": {"reward": reward, "agent_cost_usd": 0.01, "judge_cost_usd": 0.002,
                      "writes": {"executed": [list(e) for e in executed], "reference": [list(r) for r in reference], "unconfirmed": [{}] * unconfirmed, "forbidden": []}},
        }

    records = [
        rec("a", 1.0, [("cancel_order", "#1")], [("cancel_order", "#1")]),
        rec("b", 0.0, [("cancel_order", "#2")], [], unconfirmed=1),
        {"task_id": "c", "category": "cancel", "trial": 0, "excluded": "simulator_error", "sim_tokens": {"total": 300}},
    ]
    s = summarize(records, 1)
    assert s["conversations"] == 2 and s["excluded"] == 1
    assert s["writes"]["precision"] == 0.5 and s["writes"]["recall"] == 1.0
    assert s["writes"]["unsafe"] == 1
    assert s["simulator_tokens"]["total"] == 2300
    assert s["cost"]["agent_per_resolved_usd"] == 0.02


def test_infra_errors_after_retries_count_as_failures(monkeypatch, seed, tasks) -> None:
    scripted_customer(monkeypatch, [UserTurn("Where is #1001? Email " + MAYA, None)])

    class Down(FakeAnthropic):
        async def create(self, **kwargs: Any) -> SimpleNamespace:
            request = httpx.Request("POST", "https://api.anthropic.test")
            raise __import__("anthropic").InternalServerError("down", response=httpx.Response(500, request=request), body=None)

    conv = converse(seed, tasks["status-shipped-tracking"], Down({}))
    assert conv.stop_reason == "infra_error"
    assert conv.stop_reason not in orchestrator.RETRYABLE
    assert asyncio.run(grade(conv, seed, Down({}), "claude-sonnet-5-5"))["reward"] == 0.0


def test_a_failing_conversation_becomes_a_scripted_regression_task(seed, tasks) -> None:
    record = {"trial": 1, "transcript": [
        {"role": "user", "content": "Cancel my base layer order."},
        {"role": "assistant", "content": "Which order?"},
        {"role": "user", "content": "The size L one from today."},
    ]}
    new = regression_task(tasks["which-order-base-layer"], record, "20261004-210000")
    assert new.id == "which-order-base-layer--20261004-210000-t1"
    script = new.user_scenario.instructions.task_instructions
    assert "1. Cancel my base layer order." in script and "2. The size L one from today." in script
    assert new.evaluation_criteria == tasks["which-order-base-layer"].evaluation_criteria
    assert check_task(new, seed, "") == []


def test_the_spend_cap_stops_conversations_already_in_flight(monkeypatch, seed, tasks) -> None:
    scripted_customer(monkeypatch, [UserTurn("Where is #1001? Email " + MAYA, None), UserTurn("And the bottle?", None), UserTurn("", STOP)])
    route = {"intent": "order", "search_query": "", "order_number": "#1001", "email": MAYA}
    client = FakeAnthropic({"route": [route, route], "order_tools": ["It shipped.", "It shipped too."]})
    budget = Budget(max_usd=0.0003, per_conversation=0.01)
    conv = converse(seed, tasks["status-shipped-tracking"], client, meter=budget)
    assert conv.stop_reason == "budget_stop" and conv.stop_reason in orchestrator.UNRECORDED
    assert len(conv.turns) == 1
    assert budget.spent > budget.max_usd and budget.stopped == "budget"
    assert not budget.can_start()


def test_new_conversations_are_admitted_on_the_measured_cost_when_it_is_higher() -> None:
    budget = Budget(max_usd=0.10, per_conversation=0.01)
    budget.completed, budget.completed_usd, budget.spent = 1, 0.05, 0.05
    assert budget.expected_per_conversation() == 0.05
    assert budget.can_start()
    budget.in_flight = 1
    assert not budget.can_start()


def test_a_saved_conversation_rebuilds_to_the_same_end_state(monkeypatch, seed, tasks) -> None:
    scripted_customer(monkeypatch, [UserTurn("Please cancel order #1023, email " + MAYA + ". I ordered it by mistake.", None), UserTurn("yes", None), UserTurn("", STOP)])
    client = FakeAnthropic({"route": [ROUTE_1023], "order_tools": [("cancel_order", CANCEL_1023)], "reflect": [PROCEED], "judge_confirm": [{"confirmed": True, "reason": "said yes"}]})
    conv = converse(seed, tasks["cancel-eligible"], client)
    saved = json.loads(json.dumps(record(conv, asyncio.run(grade(conv, seed, client, "claude-sonnet-5-5")), None)))
    rebuilt = rebuild(saved, tasks["cancel-eligible"], seed)
    assert db_hash(rebuilt.backend.db) == db_hash(conv.backend.db)
    assert rebuilt.transcript == conv.transcript


def test_the_yes_check_judges_the_change_in_store_terms_not_raw_arguments(seed, tasks) -> None:
    args = {"order_number": "#1009", "email": "ethan.brooks@example.com", "items": [{"title": "Sierra Sun Hoody", "variant": "L", "quantity": 1}], "reason": "wrong_item"}
    summary = effective_change(seed, tasks["return-wrong-item"], "request_return", args)
    assert "1 x Sierra Sun Hoody (M)" in summary and "No return shipping fee" in summary
    refused = effective_change(seed, tasks["cancel-shipped-refused"], "cancel_order", {"order_number": "#1001", "email": MAYA, "reason": "changed_mind"})
    assert refused.startswith("cancel_order with")


def test_comparisons_pair_tasks_and_prefer_regraded_files(tmp_path) -> None:
    def write_run(name: str, rewards: dict[str, list[float]], regraded: dict[str, list[float]] | None = None):
        run = tmp_path / name
        run.mkdir()

        def lines(rs: dict[str, list[float]]) -> str:
            out = []
            for task, values in rs.items():
                for trial, reward in enumerate(values):
                    out.append(json.dumps({
                        "task_id": task, "category": "cancel", "trial": trial, "stop_reason": "user_stop", "agent_turns": 2,
                        "turn_latency_s": [1.0], "sim_tokens": {"total": 10}, "excluded": None,
                        "grade": {"reward": reward, "agent_cost_usd": 0.01, "judge_cost_usd": 0.0,
                                  "writes": {"executed": [], "reference": [], "unconfirmed": [], "forbidden": []}},
                    }))
            return "\n".join(out) + "\n"

        (run / "trajectories.jsonl").write_text(lines(rewards))
        if regraded:
            (run / "regraded-20990101-000000.jsonl").write_text(lines(regraded))
        return run

    a = write_run("a", {"t1": [1.0, 0.0], "t2": [0.0, 0.0]}, regraded={"t1": [1.0, 1.0], "t2": [0.0, 0.0]})
    b = write_run("b", {"t1": [1.0, 1.0], "t2": [1.0, 1.0]})
    result = compare_runs.compare(a, b, None, None)
    assert result["a"].endswith("regraded-20990101-000000.jsonl")
    assert result["summary_a"]["resolved"] == 2 and result["summary_b"]["resolved"] == 4
    assert result["pass1_difference_b_minus_a"]["mean"] == pytest.approx(0.5)
    low, high = result["pass1_difference_b_minus_a"]["ci95"]
    assert low <= 0.5 <= high
    assert compare_runs.compare(a, b, None, 1)["k"] == 1


USAGE_LIMIT = "You have reached your specified API usage limits. You will regain access on 2026-11-01 at 00:00 UTC."


@pytest.mark.parametrize("message", [USAGE_LIMIT, "Your credit balance is too low to access the Anthropic API."])
def test_billing_refusals_stop_runs_instead_of_failing_the_agent(message: str) -> None:
    request = httpx.Request("POST", "https://api.anthropic.test")
    error = __import__("anthropic").BadRequestError(message, response=httpx.Response(400, request=request), body=None)
    assert orchestrator.classify_agent_error(error) == "billing_error"
    assert "billing_error" in orchestrator.UNRECORDED


def test_conversations_cut_off_by_billing_are_left_out_of_results_and_rerun(tmp_path) -> None:
    base = {"category": "cancel", "agent_turns": 1, "turn_latency_s": [1.0], "sim_tokens": {"total": 10}, "excluded": None,
            "grade": {"reward": 0.0, "agent_cost_usd": 0.01, "judge_cost_usd": 0.0, "writes": {"executed": [], "reference": [], "unconfirmed": [], "forbidden": []}}}
    cut = {**base, "task_id": "a", "trial": 0, "stop_reason": "agent_error", "error": f"BadRequestError: Error code: 400 - {USAGE_LIMIT}"}
    real = {**base, "task_id": "b", "trial": 0, "stop_reason": "agent_error", "error": "ValueError: bug"}
    path = tmp_path / "trajectories.jsonl"
    path.write_text(json.dumps(cut) + "\n" + json.dumps(real) + "\n")
    assert load_done(path) == {("b", 0)}
    s = summarize([cut, real], 1)
    assert s["conversations"] == 1 and s["agent_errors"] == 1
