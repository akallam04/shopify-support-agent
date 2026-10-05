"""Runs one simulated conversation between the user simulator and the agent, in process."""

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx

from app.agent.graph import build_graph
from app.agent.tool_executor import InProcessTools
from app.config import Settings
from app.costs import usage_cost
from evals.sim.config import SimSettings
from evals.sim.env import build_db
from evals.sim.schema import Task
from evals.sim.user_sim import OUT_OF_SCOPE, STOP, TRANSFER, SimTokens, SimulatorError, SimulatorQuotaError, UserSimulator
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.simdb import SimDB

STOP_REASONS = {STOP: "user_stop", TRANSFER: "transfer", OUT_OF_SCOPE: "out_of_scope"}
INFRA_ERRORS = (anthropic.APIConnectionError, anthropic.InternalServerError, anthropic.RateLimitError, anthropic.OverloadedError)
BILLING_MARKERS = ("credit balance", "usage limit")


def billing_text(text: str) -> bool:
    return any(m in text.lower() for m in BILLING_MARKERS)


UNRECORDED = frozenset({"simulator_quota", "billing_error", "budget_stop"})
RETRYABLE = frozenset({"simulator_error"})
FAILED = frozenset({"agent_error", "infra_error"})


class Meter(Protocol):
    def charge(self, usd: float) -> None: ...

    def exhausted(self) -> bool: ...


def is_billing_error(e: Exception) -> bool:
    return isinstance(e, anthropic.BadRequestError) and billing_text(str(e))


def classify_agent_error(e: Exception) -> str:
    if isinstance(e, INFRA_ERRORS):
        return "infra_error"
    if is_billing_error(e):
        return "billing_error"
    return "agent_error"


@dataclass(frozen=True)
class AgentConfig:
    model: str
    mutation_gate: bool = True
    gate_reflection: bool = False
    gate_confirmation: bool = True

    def settings(self, base: Settings) -> Settings:
        return base.model_copy(
            update={
                "write_actions": True,
                "router_model": self.model,
                "answer_model": self.model,
                "mutation_gate": self.mutation_gate,
                "gate_reflection": self.gate_reflection,
                "gate_confirmation": self.gate_confirmation,
            }
        )


@dataclass
class Conversation:
    task: Task
    trial: int
    backend: SimStoreBackend
    sim: UserSimulator | None
    transcript: list[dict[str, str]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "max_turns"
    error: str | None = None
    wall_s: float = 0.0


async def run_conversation(
    task: Task,
    trial: int,
    seed: SimDB,
    agent: AgentConfig,
    base_settings: Settings,
    store: Any,
    anthropic_client: Any,
    http: httpx.AsyncClient,
    sim_settings: SimSettings,
    tokens: SimTokens | None = None,
    meter: Meter | None = None,
) -> Conversation:
    backend = SimStoreBackend(build_db(seed, task.initial_state))
    tools = InProcessTools(backend, include_writes=True)
    await tools.start()
    graph = build_graph(agent.settings(base_settings), store, tools, client=anthropic_client)
    sim = UserSimulator(
        task.user_scenario,
        sim_settings.sim_user_model,
        sim_settings.sim_user_base_url,
        sim_settings.sim_user_api_key,
        sim_settings.sim_user_temperature,
        tokens or SimTokens(),
    )
    conv = Conversation(task=task, trial=trial, backend=backend, sim=sim)
    carry: dict[str, Any] = {"pending_action": None, "executed_actions": []}
    started = time.perf_counter()
    for _ in range(sim_settings.max_agent_turns):
        if meter is not None and meter.exhausted():
            conv.stop_reason = "budget_stop"
            break
        try:
            user = await sim.next_message(http, conv.transcript)
        except SimulatorQuotaError as e:
            conv.stop_reason, conv.error = "simulator_quota", str(e)[:300]
            break
        except SimulatorError as e:
            conv.stop_reason, conv.error = "simulator_error", str(e)[:300]
            break
        if user.signal:
            conv.stop_reason = STOP_REASONS[user.signal]
            if user.text:
                conv.transcript.append({"role": "user", "content": user.text})
            break
        conv.transcript.append({"role": "user", "content": user.text})
        turn_started = time.perf_counter()
        try:
            state = await graph.ainvoke({"messages": list(conv.transcript), **carry})
        except Exception as e:
            conv.stop_reason, conv.error = classify_agent_error(e), f"{type(e).__name__}: {e}"[:500]
            break
        if meter is not None:
            meter.charge(usage_cost(state.get("usage", [])))
        reply = state.get("response") or ""
        conv.transcript.append({"role": "assistant", "content": reply})
        carry = {"pending_action": state.get("pending_action"), "executed_actions": state.get("executed_actions", [])}
        conv.turns.append(
            {
                "user": user.text,
                "agent": reply,
                "intent": "injection" if state.get("hard_injection") else state.get("intent", ""),
                "gate_trace": state.get("gate_trace", []),
                "tool_calls": [{"name": t["name"], "args": t["args"], "result": t["result"]} for t in state.get("tool_results", [])],
                "pending_after": bool(state.get("pending_action")),
                "latency_s": round(time.perf_counter() - turn_started, 3),
                "usage": state.get("usage", []),
            }
        )
    conv.wall_s = round(time.perf_counter() - started, 3)
    return conv
