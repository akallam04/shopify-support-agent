"""Shared state that flows through the graph. Every node reads this and returns updates."""

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # full chat history in anthropic message format
    messages: list[dict[str, Any]]

    # product | policy | order | smalltalk | out_of_scope | injection | handoff
    intent: str

    # order number + email pulled out by the router, None until an order intent shows up
    order_query: dict[str, str] | None

    # docs with ids and scores, citations must come from here
    retrieved: list[dict[str, Any]]

    # raw MCP tool outputs, the only valid source of order facts
    tool_results: list[dict[str, Any]]

    # candidate answer waiting on the verify gate
    draft: str

    citations: list[str]

    # verify allows one retry before falling back to a safe reply
    retry_count: int
