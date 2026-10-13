"""Where a reply's facts may come from: store records, policy text, or the customer's own words."""

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from app.agent.prompts import HANDOFF_RESPONSE, SAFE_FALLBACK_RESPONSE
from app.agent.state import AgentState

POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"
STORE_TEXT = f"{SAFE_FALLBACK_RESPONSE} {HANDOFF_RESPONSE}"
HIDDEN_CALLS = ("pending_action",)
WHOLE_POLICY_INTENTS = {"order", "smalltalk"}


@dataclass(frozen=True)
class Evidence:
    records: str
    policy: str
    customer: list[str]
    passages: dict[str, str] = field(default_factory=dict)


@cache
def policy_text() -> str:
    return "\n\n".join(p.read_text() for p in sorted(POLICY_DIR.glob("*.md")))


def records_text(state: AgentState) -> str:
    parts = [t["result"] for t in state.get("tool_results", []) if t["name"] not in HIDDEN_CALLS]
    parts += [json.dumps(f["result"]) for f in state.get("verified_facts") or []]
    parts += [json.dumps(a) for a in state.get("executed_actions") or [] if a.get("ok")]
    pending = state.get("pending_action")
    if pending:
        parts += [pending.get("summary", ""), *pending.get("checks", [])]
    return "\n".join(parts)


def passages(state: AgentState) -> dict[str, str]:
    found: dict[str, str] = {}
    for doc in state.get("retrieved", []):
        found[doc["id"]] = doc.get("text", "")
        handle = doc["metadata"].get("handle")
        if handle:
            found[handle] = doc.get("text", "")
    return found


def build_evidence(state: AgentState) -> Evidence:
    customer = [str(m["content"]) for m in state["messages"] if m["role"] == "user"]
    if state.get("context_digest"):
        customer.append(str(state["context_digest"]))
    found = passages(state)
    retrieved = "\n\n".join(dict.fromkeys(found.values()))
    policy = f"{policy_text()}\n\n{STORE_TEXT}" if state.get("intent") in WHOLE_POLICY_INTENTS else f"{retrieved}\n\n{STORE_TEXT}"
    return Evidence(records_text(state), policy, customer, found)
