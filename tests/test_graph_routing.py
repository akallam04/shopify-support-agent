"""Unit tests for the graph's conditional edges."""

from langgraph.graph import END

from app.agent.graph import (
    _after_confirm,
    _after_context,
    _after_order_tools,
    _after_route,
    _after_sanitize,
    _after_verify,
)


def test_order_without_email_goes_to_ask() -> None:
    assert _after_route({"intent": "order", "order_number": "1002", "email": ""}) == "respond"


def test_order_with_email_goes_to_tools() -> None:
    assert (
        _after_route({"intent": "order", "order_number": "", "email": "a@b.com"})
        == "order_tools"
    )


def test_rag_intents_go_to_retrieve() -> None:
    assert _after_route({"intent": "product"}) == "retrieve"
    assert _after_route({"intent": "policy"}) == "retrieve"


def test_static_intents_go_to_respond() -> None:
    for intent in ("smalltalk", "out_of_scope", "injection"):
        assert _after_route({"intent": intent}) == "respond"


def test_handoff_has_its_own_node() -> None:
    assert _after_route({"intent": "handoff"}) == "handoff"


def test_hard_injection_skips_router() -> None:
    assert _after_sanitize({"hard_injection": True}) == "respond"
    assert _after_sanitize({"hard_injection": False}) == "context"


def test_a_pending_action_sends_the_reply_to_confirmation() -> None:
    assert _after_context({"pending_action": {"action": "cancel_order"}}) == "confirm"
    assert _after_context({"pending_action": None}) == "route"


def test_confirmation_labels_route_to_the_right_node() -> None:
    assert _after_confirm({"confirmation": "confirm"}) == "execute"
    assert _after_confirm({"confirmation": "change"}) == "route"
    assert _after_confirm({"confirmation": "decline"}) == "verify"
    assert _after_confirm({"confirmation": "unclear"}) == "verify"


def test_a_held_back_write_goes_to_the_gate() -> None:
    assert _after_order_tools({"candidate_action": {"name": "cancel_order"}}) == "gate"
    assert _after_order_tools({"candidate_action": None}) == "verify"


def test_verify_gates_on_response() -> None:
    assert _after_verify({"response": "done"}) == END
    assert _after_verify({"response": ""}) == "respond"
