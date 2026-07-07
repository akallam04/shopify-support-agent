"""Vector search against whichever collection the router picked."""

from typing import Any

from app.agent.state import AgentState
from app.rag.vectorstore import VectorStore


def make_retrieve_node(store: VectorStore):
    def retrieve(state: AgentState) -> dict[str, Any]:
        collection = "catalog" if state["intent"] == "product" else "policies"
        query = state.get("search_query") or str(state["messages"][-1]["content"])
        return {"retrieved": store.query(collection, query, k=4)}

    return retrieve
