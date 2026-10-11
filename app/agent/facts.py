"""Verified facts and receipts carried between turns, so later answers rest on store data, not on earlier wording."""

import json
from typing import Any

from app.agent.prompts import FACTS_HEADER, HANDOFF_STATUS, RECEIPTS_HEADER
from app.agent.state import AgentState

LOOKUP = "get_order_status"
MAX_FACTS = 3


def merge_facts(old: list[dict[str, Any]], tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = {f["order_number"]: f for f in old}
    for call in tool_results:
        if call["name"] != LOOKUP:
            continue
        try:
            result = json.loads(call["result"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(result, dict) or not result.get("found"):
            continue
        number = result["order_number"]
        facts.pop(number, None)
        facts[number] = {"tool": LOOKUP, "order_number": number, "result": result}
    return list(facts.values())[-MAX_FACTS:]


def receipts(state: AgentState) -> list[str]:
    return [a["message"] for a in state.get("executed_actions") or [] if a.get("ok") and a.get("message")]


def facts_block(state: AgentState, fresh: list[dict[str, Any]], records: bool = True) -> str | None:
    parts = []
    facts = merge_facts(state.get("verified_facts") or [], fresh) if records else []
    if facts:
        parts.append(FACTS_HEADER + "".join(f"\n{f['order_number']}: {json.dumps(f['result'])}" for f in facts))
    done = receipts(state)
    if done:
        parts.append(RECEIPTS_HEADER + "".join(f"\n- {m}" for m in done))
    handoff = state.get("handoff") or {}
    if handoff.get("recorded"):
        parts.append(HANDOFF_STATUS[bool(state.get("sandbox"))])
    return "\n\n".join(parts) or None


def grounding_text(state: AgentState) -> str:
    facts = " ".join(json.dumps(f["result"]) for f in state.get("verified_facts") or [])
    return f"{facts} {' '.join(receipts(state))}"
