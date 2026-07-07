"""Deterministic input guard, layer one of the injection defense. No LLM, no cost."""

import re
from typing import Any

from app.agent.state import AgentState

MAX_INPUT_CHARS = 2000

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+|your\s+|the\s+|previous\s+|prior\s+)*(instructions|rules|guidelines)",
        r"disregard\s+(all\s+|your\s+|the\s+|previous\s+|prior\s+)*(instructions|rules|guidelines)",
        r"system\s+prompt",
        r"you\s+are\s+now\s+",
        r"developer\s+mode",
        r"jailbreak",
        r"pretend\s+(to\s+be|you\s+are)",
    )
]

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(raw: str) -> str:
    return _CONTROL_CHARS.sub("", raw)[:MAX_INPUT_CHARS].strip()


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_PATTERNS)


def sanitize_node(state: AgentState) -> dict[str, Any]:
    messages = list(state["messages"])
    text = clean_text(str(messages[-1]["content"]))
    messages[-1] = {"role": "user", "content": text}
    return {"messages": messages, "hard_injection": looks_like_injection(text)}
