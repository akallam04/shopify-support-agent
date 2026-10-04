from app.agent.nodes.context import build_digest, make_context_node


def msgs(*pairs: tuple[str, str]) -> list[dict]:
    return [{"role": r, "content": c} for r, c in pairs]


def test_short_conversations_pass_through_untouched() -> None:
    history = msgs(("user", "hi"), ("assistant", "hello"), ("user", "where is #1001?"))
    assert make_context_node(keep=8)({"messages": history}) == {"context_digest": ""}


def test_long_conversations_keep_recent_turns_and_start_on_a_customer_turn() -> None:
    history = msgs(*[("user", f"q{i}") if i % 2 == 0 else ("assistant", f"a{i}") for i in range(11)])
    out = make_context_node(keep=4)({"messages": history})
    assert out["messages"][0]["role"] == "user"
    assert len(out["messages"]) <= 4
    assert out["messages"][-1]["content"] == "q10"


def test_digest_keeps_identifiers_in_the_forms_customers_type() -> None:
    older = msgs(
        ("user", "Hi, order 1002 is late. My email is Maya.Thompson@example.com."),
        ("assistant", "Let me check."),
        ("user", "Also order number #1004 and order no. 1005"),
    )
    digest = build_digest(older, [])
    assert "#1002" in digest and "#1004" in digest and "#1005" in digest
    assert "maya.thompson@example.com" in digest


def test_digest_carries_completed_actions_but_not_refusals() -> None:
    done = [{"ok": True, "message": "Order #1002 is cancelled."}, {"ok": False, "reason": "too late"}]
    digest = build_digest(msgs(("user", "cancel it")), done)
    assert "Already done: Order #1002 is cancelled." in digest
    assert "too late" not in digest
