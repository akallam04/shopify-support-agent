"""What one turn did inside the agent, compact enough to send back with the reply."""

import json
from typing import Any

from app.agent.nodes.verify import UNCHECKED_INTENTS
from app.agent.prompts import SAFE_FALLBACK_RESPONSE
from app.logs import request_cost, token_counts

MAX_RESULT_CHARS = 2500
HIDDEN_CALLS = ("pending_action",)


def tool_view(call: dict[str, Any]) -> dict[str, Any]:
    raw = call.get("result", "")
    try:
        result: Any = json.loads(raw) if len(raw) <= MAX_RESULT_CHARS else raw[:MAX_RESULT_CHARS]
    except json.JSONDecodeError:
        result = raw[:MAX_RESULT_CHARS]
    name = call["name"]
    kind = "policy_check" if name.startswith("prepare:") else "tool"
    return {"kind": kind, "name": name.removeprefix("prepare:"), "args": call.get("args", {}), "result": result}


def verify_outcome(state: dict[str, Any]) -> str:
    if state.get("hard_injection") or state.get("intent") in UNCHECKED_INTENTS:
        return "not needed"
    if state.get("response") == SAFE_FALLBACK_RESPONSE:
        return "fell back to a safe answer"
    return "passed after one rewrite" if state.get("retry_count") else "passed"


def turn_trace(state: dict[str, Any]) -> dict[str, Any]:
    usage = state.get("usage", [])
    return {
        "path": [t["node"] for t in state.get("timings", [])],
        "nodes": state.get("timings", []),
        "tools": [tool_view(c) for c in state.get("tool_results", []) if c["name"] not in HIDDEN_CALLS],
        "gate": state.get("gate_trace", []),
        "verify": verify_outcome(state),
        "tokens": token_counts(usage),
        "cost_usd": request_cost(usage),
    }


def pending_view(state: dict[str, Any]) -> dict[str, str] | None:
    pending = state.get("pending_action")
    if not pending:
        return None
    return {"action": pending["action"], "summary": pending["summary"]}
