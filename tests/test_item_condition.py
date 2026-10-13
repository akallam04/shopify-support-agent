import pytest

from app.agent.reasons import condition_mentions, item_condition, stated_condition
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.simdb import load_db
from tests.test_review_fixes import JORDAN, RETURN_CALL, SEED, graph_for, run

RETURN_ROUTE = {"intent": "order", "search_query": "", "order_number": "#1022", "email": JORDAN}


@pytest.mark.parametrize(
    "said",
    [
        "it's unworn with tags",
        "I've never used it",
        "I haven't worn it outside",
        "tags are still on",
        "I used the size chart and it still didn't fit",
        "I haven’t worn it outside",
        "I havent worn it",
        "It hasn't been washed or worn",
        "Never worn, never washed",
        "I haven't removed the tags",
        "It's un-worn",
        "I have worn a medium for years and this one is too small",
        "The color looks washed out",
        "I only tried it on at home",
    ],
)
def test_negations_and_other_meanings_never_count_as_a_used_item(said: str) -> None:
    assert stated_condition([said]) is None


@pytest.mark.parametrize(
    "said",
    ["Can I return it if I've worn it?", "Do worn items qualify for a return?", "I only wore it to try it on"],
)
def test_a_hypothetical_or_general_question_is_unclear_and_not_refused(said: str) -> None:
    mentions = condition_mentions([said])
    assert [m.stated for m in mentions] == ["unclear"] and item_condition([said]) is None


@pytest.mark.parametrize(
    ("said", "condition"),
    [
        ("I wore it on a hike", "worn"),
        ("I wore it once, can I still return it?", "worn"),
        ("Can I return it even though I wore it?", "worn"),
        ("I didn't wear it but I washed it once", "washed"),
        ("I never wore it and I washed it once", "washed"),
        ("I cut the tags off", "tags_removed"),
        ("The tags came off", "tags_removed"),
        ("It has been used outdoors", "used"),
        ("I opened the wax", "opened"),
    ],
)
def test_a_plainly_stated_condition_is_still_found_with_the_customers_quote(said: str, condition: str) -> None:
    found = item_condition([said])
    assert found is not None and found.condition == condition and found.stated == "yes" and found.quote in said


def test_the_latest_message_about_a_condition_wins() -> None:
    assert stated_condition(["I wore it once.", "Sorry, I misspoke: I never wore it."]) is None
    assert stated_condition(["I never wore it.", "Actually I did wear it, I wore it on a hike."]) == "worn"


@pytest.mark.parametrize(
    "said",
    [
        "I want to return the rain jacket from order #1022. It's unworn with tags, it is just too big. My email is jordan.lee@example.com.",
        "I want to return the rain jacket from order #1022. I used the size chart and it still didn't fit, it is too big. My email is jordan.lee@example.com.",
    ],
)
def test_an_unworn_item_reaches_the_confirmation_card(said: str) -> None:
    backend = SimStoreBackend(load_db(SEED))
    graph, _ = graph_for(backend, {"route": [RETURN_ROUTE], "order_tools": [("request_return", {**RETURN_CALL, "reason_quote": "it is too big"})]})
    state = run(graph, [{"role": "user", "content": said}])
    assert state["pending_action"] is not None
    assert "unworn and unused" in state["pending_action"]["summary"]
    assert not any(g["step"] == "condition_check" and g["stated"] == "yes" for g in state["gate_trace"])


def test_an_unclear_condition_shows_the_card_and_records_why() -> None:
    backend = SimStoreBackend(load_db(SEED))
    said = "Can I return the rain jacket from order #1022 if I've worn it? It is too big. jordan.lee@example.com"
    graph, _ = graph_for(backend, {"route": [RETURN_ROUTE], "order_tools": [("request_return", {**RETURN_CALL, "reason_quote": "It is too big"})]})
    state = run(graph, [{"role": "user", "content": said}])
    assert state["pending_action"] is not None
    check = next(g for g in state["gate_trace"] if g["step"] == "condition_check")
    assert check["stated"] == "unclear" and "@" not in check["quote"]
