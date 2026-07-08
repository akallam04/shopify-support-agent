"""Unit tests for the graph's conditional edges."""

from langgraph.graph import END

from app.agent.graph import _after_route, _after_sanitize, _after_verify


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
    for intent in ("smalltalk", "handoff", "out_of_scope", "injection"):
        assert _after_route({"intent": intent}) == "respond"


def test_hard_injection_skips_router() -> None:
    assert _after_sanitize({"hard_injection": True}) == "respond"
    assert _after_sanitize({"hard_injection": False}) == "route"


def test_verify_gates_on_response() -> None:
    assert _after_verify({"response": "done"}) == END
    assert _after_verify({"response": ""}) == "respond"
