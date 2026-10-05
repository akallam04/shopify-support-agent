"""Unit tests for the grounding gate."""

from app.agent.nodes.verify import (
    plain_dashes,
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


def test_em_and_en_dashes_are_replaced_in_replies() -> None:
    assert plain_dashes("It is in stock\u2014we have 12.") == "It is in stock - we have 12."
    assert plain_dashes("Arrives in 3\u20136 business days, or 5 \u2013 7 for Canada.") == "Arrives in 3-6 business days, or 5-7 for Canada."
    assert plain_dashes("Options:\n\u2014 Standard\n\u2014 Expedited") == "Options:\n- Standard\n- Expedited"
    assert plain_dashes("No dashes here - already plain.") == "No dashes here - already plain."


def test_verify_node_cleans_dashes_on_every_passing_path() -> None:
    order = verify_node({"intent": "order", "draft": "It shipped\u2014tracking is below.", "tool_results": [], "messages": []})
    assert order["response"] == "It shipped - tracking is below."
    static = verify_node({"intent": "smalltalk", "draft": "Happy to help\u2014anytime.", "messages": []})
    assert static["response"] == "Happy to help - anytime."


def test_order_facts_from_earlier_verified_replies_stay_grounded() -> None:
    messages = [
        {"role": "user", "content": "I don't have the order number, my email is jordan.lee@example.com"},
        {"role": "assistant", "content": "I found two jacket orders, #1022 and #1017. Which one?"},
        {"role": "user", "content": "The one without a return."},
    ]
    state = {"intent": "order", "draft": "Got it, I will start the return on #1022.", "tool_results": [], "messages": messages}
    assert verify_node(state)["response"] == "Got it, I will start the return on #1022."
    invented = verify_node({**state, "draft": "I will start the return on #1099."})
    assert invented["response"] == "" and "#1099" in invented["verify_feedback"]
