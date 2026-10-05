from datetime import datetime, timedelta, timezone

from app.session import SESSION_TTL, Session, issue, verify

KEY = b"test-key"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def test_a_token_round_trips_its_state() -> None:
    session = Session(mutations=[{"action": "cancel_order", "args": {"order_number": "#1023"}}], pending_action={"action": "x", "key": "k"}, tokens_used=1200)
    back = verify(issue(session, KEY, NOW), KEY, NOW + timedelta(minutes=5))
    assert back == session


def test_tampered_foreign_and_expired_tokens_are_rejected() -> None:
    token = issue(Session(), KEY, NOW)
    body, sig = token.split(".")
    forged_body = body[:-2] + ("AA" if body[-2:] != "AA" else "BB")
    assert verify(f"{forged_body}.{sig}", KEY, NOW) is None
    assert verify(token, b"other-key", NOW) is None
    assert verify(token, KEY, NOW + SESSION_TTL + timedelta(seconds=1)) is None
    assert verify("garbage", KEY, NOW) is None
    assert verify("", KEY, NOW) is None


def test_each_issue_gets_a_fresh_nonce() -> None:
    session = Session()
    assert issue(session, KEY, NOW) != issue(session, KEY, NOW)
