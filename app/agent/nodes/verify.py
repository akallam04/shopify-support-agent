"""Deterministic grounding gate: citations must exist, order facts must come from tools."""

import re
from typing import Any

from app.agent.facts import grounding_text
from app.agent.prompts import SAFE_FALLBACK_RESPONSE
from app.agent.state import AgentState

CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9-]*)\]")
BRACKET_RE = re.compile(r"\[([^\[\]\n]{1,80})\]")
ORDER_NUMBER_RE = re.compile(r"#\d{3,}")
# at least one digit, so status words like FULFILLED never match
TRACKING_LIKE_RE = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b")

UNCHECKED_INTENTS = {"injection", "out_of_scope", "handoff", "smalltalk"}

RETRACTION_RE = re.compile(
    r"\b(correct(ing)? my (last|earlier|previous) (message|reply|answer)|"
    r"(never|did not|didn't) (actually )?(look|looked) (that |it |this )?up|"
    r"(should not|shouldn't) have (described|stated|said|mentioned|given)|"
    r"(do not|don't) (actually )?have (order\b|its details|any record of (order|that order|this order|it)\b))",
    re.IGNORECASE,
)
RETRACTION_FEEDBACK = "do not retract or correct the confirmation and receipt messages: the store system built them from store records"
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
SYSTEM_START_RE = re.compile(
    r"^(Just to confirm, I will|Sorry, I want to be sure before I change anything|"
    r"I cannot make this change depend on a condition|That is already done\.|Order #\d+ is cancelled\.|"
    r"The shipping address on order #\d+ is now|A return is requested on order #\d+)"
)
RECONFIRM_MARK = "Your change is still waiting: I will"
DISTINCT_NUMBER_RE = re.compile(r"(?<![#\d.])(\d[\d,]*\.\d+|\d{3,})")


def system_part(message: str) -> str:
    text = message.strip()
    if SYSTEM_START_RE.match(text):
        return text
    at = text.find(RECONFIRM_MARK)
    return text[at:] if at >= 0 else ""


def retracts_system_message(sentence: str, messages: list[dict[str, Any]]) -> bool:
    if not RETRACTION_RE.search(sentence):
        return False
    said = [str(m["content"]).strip() for m in messages if m["role"] == "assistant"]
    built = [p for p in (system_part(m) for m in said) if p]
    if not built:
        return False
    numbers = set(DISTINCT_NUMBER_RE.findall(sentence))
    if numbers and any(numbers & set(DISTINCT_NUMBER_RE.findall(p)) for p in built):
        return True
    return system_part(said[-1]) == said[-1]


def system_retractions(text: str, messages: list[dict[str, Any]]) -> list[str]:
    return [s for p in text.split("\n\n") for s in SENTENCE_RE.split(p.strip()) if s and retracts_system_message(s, messages)]


def without_retractions(text: str, messages: list[dict[str, Any]]) -> str:
    kept = []
    for paragraph in text.split("\n\n"):
        sentences = [s for s in SENTENCE_RE.split(paragraph.strip()) if s and not retracts_system_message(s, messages)]
        if sentences:
            kept.append(" ".join(sentences))
    return "\n\n".join(kept)

LINE_START_DASH_RE = re.compile(r"(?m)^[ \t]*[\u2013\u2014][ \t]*")
RANGE_DASH_RE = re.compile(r"(?<=\d)[ \t]*[\u2013\u2014][ \t]*(?=\d)")
DASH_RE = re.compile(r"[ \t]*[\u2013\u2014][ \t]*")
EMOJI_RE = re.compile(r"[ \t]*[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")


def plain_text(text: str) -> str:
    text = EMOJI_RE.sub("", text)
    text = LINE_START_DASH_RE.sub("- ", text)
    text = RANGE_DASH_RE.sub("-", text)
    return DASH_RE.sub(" - ", text).strip()


def check_citations(draft: str, allowed_ids: set[str]) -> tuple[bool, str | None]:
    unknown = sorted(set(BRACKET_RE.findall(draft)) - allowed_ids)
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
        return {"response": plain_text(draft), "verify_feedback": None}

    if system_retractions(draft, state["messages"]):
        if state.get("retry_count", 0) >= 1:
            return {"response": plain_text(without_retractions(draft, state["messages"])), "verify_feedback": None}
        return {"response": "", "verify_feedback": RETRACTION_FEEDBACK, "retry_count": state.get("retry_count", 0) + 1}

    if intent == "order":
        grounding = " ".join(t["result"] for t in state.get("tool_results", []))
        grounding += " " + " ".join(str(m["content"]) for m in state["messages"])
        grounding += " " + (state.get("context_digest") or "") + " " + grounding_text(state)
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
        return {"response": plain_text(draft), "verify_feedback": None}
    if state.get("retry_count", 0) >= 1:
        return {"response": SAFE_FALLBACK_RESPONSE, "verify_feedback": None}
    return {
        "response": "",
        "verify_feedback": feedback,
        "retry_count": state.get("retry_count", 0) + 1,
    }
