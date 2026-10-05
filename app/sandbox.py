"""The public demo's per-session sandbox: the frozen seed plus this visitor's changes, rebuilt per request."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.agent.graph import build_graph
from app.agent.tool_executor import InProcessTools, tool_specs
from app.config import Settings
from app.session import Session, issue, verify
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.simdb import SimDB, load_db
from mcp_server.tools import WRITE_ACTIONS, ToolInputError, execute, idempotency_key


class SessionLimitError(Exception):
    pass


@dataclass
class TurnResult:
    state: dict[str, Any]
    session: Session
    token: str
    reset: bool
    writes: list[str]


class Sandbox:
    def __init__(self, settings: Settings, store: Any, client: Any) -> None:
        if not settings.session_signing_key:
            raise RuntimeError("sandbox mode needs SESSION_SIGNING_KEY")
        self.settings = settings
        self.store = store
        self.client = client
        self.key = settings.session_signing_key.encode()
        self.seed: SimDB = load_db(settings.sim_seed_path)
        self.specs: list[dict[str, Any]] = []

    async def start(self) -> None:
        self.specs = await tool_specs(self.settings.write_actions)

    def backend(self, mutations: list[dict[str, Any]]) -> SimStoreBackend:
        backend = SimStoreBackend(self.seed.copy_fresh())
        for m in mutations:
            if m.get("action") in WRITE_ACTIONS:
                execute(backend, m["action"], m.get("args") or {})
        return backend

    def tools(self, backend: SimStoreBackend) -> InProcessTools:
        tools = InProcessTools(backend, include_writes=self.settings.write_actions)
        tools.anthropic_tools = self.specs
        return tools

    @staticmethod
    def checked_pending(pending: dict[str, Any] | None, tools: InProcessTools) -> dict[str, Any] | None:
        if not pending or not tools.gated_tool_names:
            return None
        try:
            prepared = tools.prepare(pending["action"], pending["args"])
        except (ToolInputError, KeyError):
            return None
        if not prepared.allowed or idempotency_key(pending["action"], prepared.args) != pending.get("key"):
            return None
        return pending

    @staticmethod
    def new_mutations(state: dict[str, Any]) -> list[dict[str, Any]]:
        mutations = []
        for call in state.get("tool_results", []):
            if call["name"] not in WRITE_ACTIONS:
                continue
            result = json.loads(call["result"])
            if result.get("ok") and not result.get("duplicate"):
                mutations.append({"action": call["name"], "args": call["args"]})
        return mutations

    def open(self, token: str | None, now: datetime) -> tuple[Session, bool]:
        session = verify(token, self.key, now) if token else None
        return session or Session(), bool(token) and session is None

    async def turn(self, history: list[dict[str, Any]], token: str | None) -> TurnResult:
        now = datetime.now(timezone.utc)
        session, reset = self.open(token, now)
        if session.tokens_used >= self.settings.session_token_cap:
            raise SessionLimitError()
        tools = self.tools(self.backend(session.mutations))
        graph = build_graph(self.settings, self.store, tools, client=self.client)
        state = await graph.ainvoke({
            "messages": history,
            "pending_action": self.checked_pending(session.pending_action, tools),
            "executed_actions": session.executed_actions,
        })
        mutations = self.new_mutations(state)
        session.mutations = session.mutations + mutations
        session.pending_action = state.get("pending_action")
        session.executed_actions = list(state.get("executed_actions") or [])
        session.tokens_used += sum(u["input_tokens"] + u["output_tokens"] for u in state.get("usage", []))
        return TurnResult(state, session, issue(session, self.key, now), reset, [m["action"] for m in mutations])
