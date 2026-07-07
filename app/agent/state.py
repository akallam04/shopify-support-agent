"""Shared state that flows through the graph. Every node reads this and returns updates."""

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # chat history in anthropic message format, latest user turn last
    messages: list[dict[str, Any]]

    # product | policy | order | smalltalk | handoff | out_of_scope | injection
    intent: str

    # set by the deterministic pre-filter, skips the router entirely
    hard_injection: bool

    # router extractions, empty string when absent
    search_query: str
    order_number: str
    email: str

    # docs with ids and scores, citations must come from here
    retrieved: list[dict[str, Any]]

    # mcp tool calls made this turn, the only valid source of order facts
    tool_results: list[dict[str, Any]]

    # candidate answer waiting on the verify gate
    draft: str

    # verify feedback for the single retry pass
    verify_feedback: str | None
    retry_count: int

    # what actually goes back to the customer
    response: str

    # per-call token usage, consumed by the eval harness
    usage: list[dict[str, Any]]
