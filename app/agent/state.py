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
    # what each verify check found, for the trace
    verify_checks: list[dict[str, Any]]
    # the draft was written by code from store records, not by the model
    system_draft: bool

    # what actually goes back to the customer
    response: str

    # per-call token usage, consumed by the eval harness
    usage: list[dict[str, Any]]

    pending_action: dict[str, Any] | None
    candidate_action: dict[str, Any] | None
    confirmation: str
    gate_trace: list[dict[str, Any]]
    executed_actions: list[dict[str, Any]]
    context_digest: str
    gate_feedback: str | None
    corrected_action: str | None
    gate_retries: int
    timings: list[dict[str, Any]]

    # latest store lookup per order, carried between turns so later answers can rely on it
    verified_facts: list[dict[str, Any]]
    # whether a request for a person was recorded, and whether anyone was notified
    handoff: dict[str, Any] | None
    # the public demo: receipts say nothing is emailed and no money moves
    sandbox: bool
    # text to show before a confirmation card shown again, such as the answer to a question
    lead: str
