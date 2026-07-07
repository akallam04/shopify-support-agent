"""Unit tests for the grounding gate."""

from app.agent.nodes.verify import (
    check_citations,
    check_order_facts,
    verify_node,
)
from app.agent.prompts import SAFE_FALLBACK_RESPONSE


def test_citations_must_come_from_context() -> None:
    ok, _ = check_citations(
        "The Stormline is waterproof [stormline-rain-jacket].",
        {"stormline-rain-jacket", "policy-returns-return-window"},
    )
    assert ok

    ok, feedback = check_citations("It is waterproof [made-up-product].", {"stormline-rain-jacket"})
    assert not ok
    assert "made-up-product" in feedback


def test_order_facts_must_come_from_grounding() -> None:
    grounding = '{"order_number": "#1001", "tracking": "1Z999AA10123456784"} where is #1001'
    ok, _ = check_order_facts("Order #1001 shipped, tracking 1Z999AA10123456784.", grounding)
    assert ok

    ok, feedback = check_order_facts("Your order #4242 has shipped!", grounding)
    assert not ok
    assert "#4242" in feedback


def test_status_words_do_not_trip_the_tracking_regex() -> None:
    ok, _ = check_order_facts("Your order is UNFULFILLED right now.", "no identifiers here")
    assert ok


def test_verify_node_retry_then_fallback() -> None:
    base = {
        "intent": "product",
        "draft": "Great jacket [nonexistent-handle].",
        "retrieved": [{"id": "product-1", "metadata": {"handle": "stormline-rain-jacket"}}],
        "messages": [{"role": "user", "content": "jacket?"}],
    }
    first = verify_node({**base, "retry_count": 0})
    assert first["response"] == ""
    assert first["retry_count"] == 1
    assert "nonexistent-handle" in first["verify_feedback"]

    second = verify_node({**base, "retry_count": 1})
    assert second["response"] == SAFE_FALLBACK_RESPONSE


def test_verify_node_passes_clean_answer_through() -> None:
    state = {
        "intent": "policy",
        "draft": "You have 30 days [policy-returns-return-window].",
        "retrieved": [{"id": "policy-returns-return-window", "metadata": {}}],
        "messages": [{"role": "user", "content": "return window?"}],
        "retry_count": 0,
    }
    result = verify_node(state)
    assert result["response"] == state["draft"]


def test_verify_node_skips_static_paths() -> None:
    result = verify_node(
        {"intent": "handoff", "draft": "connecting you", "messages": [], "retry_count": 0}
    )
    assert result["response"] == "connecting you"
