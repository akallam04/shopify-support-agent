"""Vector search against whichever collection the router picked, plus the exceptions that go with a rule."""

import re
from typing import Any

from app.agent.state import AgentState
from app.rag.vectorstore import VectorStore

COMPANIONS = {
    "policy-returns-return-shipping-cost": ("policy-returns-how-to-start-a-return",),
    "policy-returns-how-to-start-a-return": ("policy-returns-return-shipping-cost",),
    "policy-shipping-domestic-shipping-options": ("policy-shipping-international-shipping",),
}
CANADA_RE = re.compile(r"\b(canada|canadian)\b", re.IGNORECASE)
CANADA_DOCS = {
    "policy-returns": ("policy-returns-how-to-start-a-return", "policy-returns-return-shipping-cost"),
    "policy-shipping": ("policy-shipping-international-shipping",),
}


def companion_ids(docs: list[dict[str, Any]], text: str) -> list[str]:
    have = [d["id"] for d in docs]
    wanted = [c for i in have for c in COMPANIONS.get(i, ())]
    if CANADA_RE.search(text):
        wanted += [c for prefix, extra in CANADA_DOCS.items() if any(i.startswith(prefix) for i in have) for c in extra]
    return list(dict.fromkeys(c for c in wanted if c not in have))


def make_retrieve_node(store: VectorStore):
    def retrieve(state: AgentState) -> dict[str, Any]:
        collection = "catalog" if state["intent"] == "product" else "policies"
        query = state.get("search_query") or str(state["messages"][-1]["content"])
        docs = store.query(collection, query, k=4)
        extra = companion_ids(docs, f"{query} {state['messages'][-1]['content']}") if collection == "policies" else []
        if extra:
            docs += store.get(collection, extra)
        return {"retrieved": docs}

    return retrieve
