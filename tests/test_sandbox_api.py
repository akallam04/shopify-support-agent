import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
from fastapi.testclient import TestClient

from app.agent.prompts import CONFIRM_CLASSIFIER_SCHEMA, ROUTER_SCHEMA
from app.config import Settings
from app.main import create_app
from app.sandbox import Sandbox
from app.session import Session, issue, verify
from mcp_server.tools import idempotency_key

MAYA = "maya.thompson@example.com"
KEY = "test-signing-key"
CANCEL = {"order_number": "#1023", "email": MAYA, "reason": "ordered_by_mistake"}
ROUTE = {"intent": "order", "search_query": "", "order_number": "#1023", "email": MAYA}


def reply(block: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=100, output_tokens=20), stop_reason="end_turn")


class Scripted:
    def __init__(self, script: dict[str, list[Any]]) -> None:
        self.script = script
        self.messages = self

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        schema = (kwargs.get("output_config") or {}).get("format", {}).get("schema")
        if schema is ROUTER_SCHEMA:
            return reply(SimpleNamespace(type="text", text=json.dumps(self.script["route"].pop(0))))
        if schema is CONFIRM_CLASSIFIER_SCHEMA:
            return reply(SimpleNamespace(type="text", text=json.dumps({"label": self.script["confirm"].pop(0)})))
        step = self.script["order_tools"].pop(0)
        if isinstance(step, tuple):
            return reply(SimpleNamespace(type="tool_use", name=step[0], input=step[1], id="tu_1"))
        return reply(SimpleNamespace(type="text", text=step))


class Down:
    def __init__(self) -> None:
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        request = httpx.Request("POST", "https://api.anthropic.test")
        raise anthropic.InternalServerError("down", response=httpx.Response(500, request=request), body=None)


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, api_mode="sandbox", session_signing_key=KEY, write_actions=True, anthropic_api_key="test", **overrides)


def client_for(model: Any, **overrides: Any) -> tuple[TestClient, Sandbox]:
    sandbox = Sandbox(settings(**overrides), object(), model)
    asyncio.run(sandbox.start())
    return TestClient(create_app(sandbox=sandbox)), sandbox


def post(client: TestClient, messages: list[dict[str, str]], token: str | None = None) -> httpx.Response:
    return client.post("/chat", json={"messages": messages, "session_state": token}, headers={"Origin": "https://demo.test"})


def test_a_confirmed_cancel_changes_only_this_sessions_sandbox() -> None:
    client, sandbox = client_for(Scripted({"route": [ROUTE], "order_tools": [("cancel_order", CANCEL)]}))
    ask = [{"role": "user", "content": f"Cancel #1023, {MAYA}, ordered by mistake."}]
    with client:
        first = post(client, ask).json()
        assert first["sandbox"] is True and first["response"].startswith("Just to confirm")
        second = post(client, ask + [{"role": "assistant", "content": first["response"]}, {"role": "user", "content": "yes"}], first["session_state"]).json()
    assert "is cancelled" in second["response"]
    session = verify(second["session_state"], KEY.encode(), datetime.now(timezone.utc))
    assert session.mutations == [{"action": "cancel_order", "args": CANCEL}] and session.pending_action is None
    assert sandbox.backend(session.mutations).db.orders["#1023"].cancelled_at is not None
    assert sandbox.backend([]).db.orders["#1023"].cancelled_at is None


def test_a_tampered_token_resets_the_session_and_cannot_confirm() -> None:
    client, _ = client_for(Scripted({"route": [ROUTE, ROUTE], "order_tools": [("cancel_order", CANCEL), "Which order did you mean?"]}))
    ask = [{"role": "user", "content": f"Cancel #1023, {MAYA}, ordered by mistake."}]
    with client:
        first = post(client, ask).json()
        body, sig = first["session_state"].split(".")
        forged = f"{body}x.{sig}"
        second = post(client, ask + [{"role": "assistant", "content": first["response"]}, {"role": "user", "content": "yes"}], forged).json()
    assert second["session_reset"] is True
    assert "is cancelled" not in second["response"]
    assert verify(second["session_state"], KEY.encode(), datetime.now(timezone.utc)).mutations == []


def test_a_pending_action_that_no_longer_matches_is_dropped() -> None:
    _, sandbox = client_for(Scripted({}))
    tools = sandbox.tools(sandbox.backend([]))
    good = {"action": "cancel_order", "args": CANCEL, "key": idempotency_key("cancel_order", tools.prepare("cancel_order", CANCEL).args)}
    assert sandbox.checked_pending(good, tools) == good
    assert sandbox.checked_pending({**good, "key": "swapped"}, tools) is None
    assert sandbox.checked_pending({**good, "args": {**CANCEL, "order_number": "#1001"}}, tools) is None


def test_model_outages_return_a_friendly_503_with_cors() -> None:
    client, _ = client_for(Down())
    with client:
        resp = post(client, [{"role": "user", "content": "Where is my order?"}])
    assert resp.status_code == 503
    assert resp.json()["error"] == "model_unavailable" and "resting" in resp.json()["message"]
    assert resp.headers.get("access-control-allow-origin")


def test_sessions_and_conversations_are_capped() -> None:
    client, _ = client_for(Scripted({}))
    spent = issue(Session(tokens_used=60000), KEY.encode(), datetime.now(timezone.utc))
    with client:
        capped = post(client, [{"role": "user", "content": "hi"}], spent)
        long = post(client, [{"role": "user", "content": "x" * 4000}, {"role": "assistant", "content": "y" * 4000}] * 2 + [{"role": "user", "content": "z" * 4000}])
    assert capped.status_code == 429 and capped.json()["error"] == "session_limit"
    assert long.status_code == 413 and long.json()["error"] == "conversation_too_long"
