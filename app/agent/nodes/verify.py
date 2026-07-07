"""Deterministic grounding gate: citations must exist, order facts must come from tools."""

import re
from typing import Any

from app.agent.prompts import SAFE_FALLBACK_RESPONSE
from app.agent.state import AgentState

CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9-]*)\]")
ORDER_NUMBER_RE = re.compile(r"#\d{3,}")
# at least one digit, so status words like FULFILLED never match
TRACKING_LIKE_RE = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b")

UNCHECKED_INTENTS = {"injection", "out_of_scope", "handoff", "smalltalk"}


def check_citations(draft: str, allowed_ids: set[str]) -> tuple[bool, str | None]:
    unknown = sorted(set(CITATION_RE.findall(draft)) - allowed_ids)
    if unknown:
        return False, f"citations not present in the provided context: {unknown}"
    return True, None


def check_order_facts(draft: str, grounding_text: str) -> tuple[bool, str | None]:
    problems = [t for t in sorted(set(ORDER_NUMBER_RE.findall(draft))) if t not in grounding_text]
    problems += [t for t in sorted(set(TRACKING_LIKE_RE.findall(draft))) if t not in grounding_text]
    if problems:
        return False, (
            "these identifiers do not appear in the tool results or the customer's "
            f"own messages: {problems}"
        )
    return True, None


def verify_node(state: AgentState) -> dict[str, Any]:
    draft = state.get("draft", "")
    intent = state.get("intent", "")

    if state.get("hard_injection") or intent in UNCHECKED_INTENTS:
        return {"response": draft, "verify_feedback": None}

    if intent == "order":
        grounding = " ".join(t["result"] for t in state.get("tool_results", []))
        grounding += " " + " ".join(
            str(m["content"]) for m in state["messages"] if m["role"] == "user"
        )
        ok, feedback = check_order_facts(draft, grounding)
    else:
        allowed: set[str] = set()
        for d in state.get("retrieved", []):
            allowed.add(d["id"])
            handle = d["metadata"].get("handle")
            if handle:
                allowed.add(handle)
        ok, feedback = check_citations(draft, allowed)

    if ok:
        return {"response": draft, "verify_feedback": None}
    if state.get("retry_count", 0) >= 1:
        return {"response": SAFE_FALLBACK_RESPONSE, "verify_feedback": None}
    return {
        "response": "",
        "verify_feedback": feedback,
        "retry_count": state.get("retry_count", 0) + 1,
    }
