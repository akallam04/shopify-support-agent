"""Context cleaning: recent turns stay verbatim, older ones fold into a short deterministic digest."""

import re
from typing import Any

from app.agent.state import AgentState

ORDER_MENTION_RE = re.compile(r"(?:#|\border\s*(?:number|no\.?)?\s*#?)\s*(\d{3,10})", re.IGNORECASE)
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
EARLIER_REQUESTS_KEPT = 4


def build_digest(older: list[dict[str, Any]], executed: list[dict[str, Any]]) -> str:
    user_texts = [" ".join(str(m["content"]).split()) for m in older if m["role"] == "user"]
    joined = " ".join(user_texts)
    lines = []
    orders = sorted({f"#{n}" for n in ORDER_MENTION_RE.findall(joined)})
    if orders:
        lines.append(f"Order numbers the customer gave: {', '.join(orders)}.")
    emails = sorted({e.lower().rstrip(".,") for e in EMAIL_RE.findall(joined)})
    if emails:
        lines.append(f"Email addresses the customer gave: {', '.join(emails)}.")
    lines += [f"Customer said: {text[:160]}" for text in user_texts[-EARLIER_REQUESTS_KEPT:]]
    done = [a["message"] for a in executed if a.get("ok") and a.get("message")]
    if done:
        lines.append("Already done: " + " ".join(done))
    return "\n".join(lines)


def make_context_node(keep: int):
    def context(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        if len(messages) <= keep:
            return {"context_digest": ""}
        cut = len(messages) - keep
        while messages[cut]["role"] != "user":
            cut += 1
        return {
            "messages": messages[cut:],
            "context_digest": build_digest(messages[:cut], state.get("executed_actions", [])),
        }

    return context
