import json

from app.logs import log_event
from app.privacy import mask_email, masked
from app.trace import tool_view


def test_emails_keep_only_their_first_letter_and_domain() -> None:
    assert mask_email("maya.thompson@example.com") == "m•••@example.com"
    assert mask_email("write to j@x.io or k.l@shop.example.co.uk") == "write to j•••@x.io or k•••@shop.example.co.uk"
    assert mask_email("order #1023, no email here") == "order #1023, no email here"


def test_nested_values_are_masked() -> None:
    value = {"args": {"email": "a.b@c.com", "n": 3}, "list": ["x@y.org", 4]}
    assert masked(value) == {"args": {"email": "a•••@c.com", "n": 3}, "list": ["x•••@y.org", 4]}


def test_tool_calls_in_the_trace_never_show_a_full_email() -> None:
    call = {"name": "get_order_status", "args": {"order_number": "#1023", "email": "maya.thompson@example.com"}, "result": json.dumps({"found": True, "note": "for maya.thompson@example.com"})}
    view = tool_view(call)
    assert view["args"]["email"] == "m•••@example.com"
    assert "maya.thompson" not in json.dumps(view)


def test_log_lines_never_carry_a_full_email(capsys) -> None:
    log_event(event="chat", error="lookup failed for maya.thompson@example.com")
    line = capsys.readouterr().out
    assert "maya.thompson" not in line and "m•••@example.com" in json.loads(line)["error"]
