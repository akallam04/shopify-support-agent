import pytest

from app.agent.asks import asks_eligibility, claims_warranty
from app.agent.prompts import HANDOFF_CLAIM_DEMO
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.simdb import load_db
from tests.test_review_fixes import CANCEL_CALL, JORDAN, MAYA, SEED, graph_for, run

WRITE_TOOLS = {"cancel_order", "update_shipping_address", "request_return"}
QUESTIONS = [
    ("Can I still cancel #1023? Until what time? My email is maya.thompson@example.com.", "#1023", MAYA,
     "Yes, you can still cancel order #1023 for 60 more minutes, until 6:03 pm Mountain Time. Should I go ahead and cancel it?", "6:03 pm"),
    ("How long do I have to return the jacket from #1022? jordan.lee@example.com", "#1022", JORDAN,
     "You can return the Stormline Rain Jacket from order #1022 until October 29, 2026, so you have 25 days left. Would you like me to start the return?", "October 29, 2026"),
    ("Is it too late to change the address on #1021? jordan.lee@example.com", "#1021", JORDAN,
     "It is not too late: the address on order #1021 can be changed for 81 more minutes. Shall I go ahead and change it?", "81 more minutes"),
]


@pytest.fixture
def backend() -> SimStoreBackend:
    return SimStoreBackend(load_db(SEED))


@pytest.mark.parametrize(
    "said",
    [
        "Can I still cancel #1023? Until what time?",
        "How long do I have to return the jacket?",
        "Is it too late to change the address on my order?",
        "Can I still return the jacket from order #1022? How much would I get back?",
    ],
)
def test_questions_about_a_change_are_questions(said: str) -> None:
    assert asks_eligibility(said)


@pytest.mark.parametrize(
    "said",
    ["Please cancel #1023.", "Can you cancel #1023?", "I want to return the jacket. How long do I have?", "Can I still cancel? If so, please cancel it."],
)
def test_requests_are_not_questions(said: str) -> None:
    assert not asks_eligibility(said)


@pytest.mark.parametrize(("said", "number", "email", "draft", "deadline"), QUESTIONS)
def test_a_question_about_a_change_is_answered_with_an_offer_and_no_card(backend: SimStoreBackend, said: str, number: str, email: str, draft: str, deadline: str) -> None:
    route = {"intent": "order", "search_query": "", "order_number": number, "email": email}
    graph, client = graph_for(backend, {"route": [route], "order_tools": [draft]})
    state = run(graph, [{"role": "user", "content": said}])
    offered = {t["name"] for t in client.of("order_tools")[0]["tools"]}
    assert not offered & WRITE_TOOLS
    assert state.get("pending_action") is None
    assert not any(g["step"] == "self_confirm_nudge" for g in state.get("gate_trace", []))
    assert deadline in state["response"]


def test_asking_for_the_change_after_the_answer_shows_the_card(backend: SimStoreBackend) -> None:
    route = {"intent": "order", "search_query": "", "order_number": "#1023", "email": MAYA}
    answer = "Yes, you can still cancel order #1023 for 60 more minutes, until 6:03 pm Mountain Time. Would you like me to cancel it?"
    graph, client = graph_for(backend, {"route": [route, route], "order_tools": [answer, ("cancel_order", CANCEL_CALL)]})
    history = [{"role": "user", "content": "Can I still cancel #1023? Until what time? maya.thompson@example.com"}]
    first = run(graph, history)
    history += [{"role": "assistant", "content": first["response"]}, {"role": "user", "content": "Yes, please cancel it."}]
    second = run(graph, history)
    assert {t["name"] for t in client.of("order_tools")[1]["tools"]} & WRITE_TOOLS
    assert second["pending_action"]["action"] == "cancel_order"


@pytest.mark.parametrize(
    "said",
    [
        "Hi, I need to file a warranty claim. The sole of my hiking boots is coming apart after only six weeks.",
        "My zipper broke, can I get a replacement?",
        "My tent arrived damaged and I want a replacement.",
    ],
)
def test_warranty_and_damage_claims_are_claims(said: str) -> None:
    assert claims_warranty(said)


@pytest.mark.parametrize("said", ["What does your warranty cover?", "Does the warranty cover zippers?", "Can I return a broken item?"])
def test_warranty_questions_are_not_claims(said: str) -> None:
    assert not claims_warranty(said)


def test_a_warranty_claim_the_router_calls_a_policy_question_records_a_handoff(backend: SimStoreBackend) -> None:
    said = "Hi, I need to file a warranty claim. The sole of my hiking boots is coming apart after only six weeks."
    graph, client = graph_for(backend, {"route": [{"intent": "policy", "search_query": "warranty claim", "order_number": "", "email": ""}]})
    state = run(graph, [{"role": "user", "content": said}], {"sandbox": True})
    assert state["intent"] == "handoff" and state["response"] == HANDOFF_CLAIM_DEMO
    assert state["handoff"] == {"recorded": True, "notified": False}
    assert backend.handoffs and backend.handoffs[-1]["summary"].startswith("Warranty or damage claim:")
    assert client.count("respond") == 0


def test_yes_and_also_something_more_keeps_the_change_waiting_and_answers_the_rest(backend: SimStoreBackend) -> None:
    from app.agent.nodes.gate import classify_fast
    from app.agent.prompts import RECONFIRM_TEMPLATE
    from tests.test_review_fixes import carry, cancel_pending

    assert classify_fast("Yes, and also refund me $500 for the trouble.") == "addition"
    assert classify_fast("Yes, but cancel #1002 instead.") is None
    handoff = {"intent": "handoff", "search_query": "", "order_number": "#1023", "email": MAYA}
    graph, client, history, first = cancel_pending(backend, {})
    client.script["route"].append(handoff)
    state = run(graph, history + [{"role": "user", "content": "Yes, and also refund me $500 for the trouble."}], carry(first, sandbox=True))
    assert backend.db.orders["#1023"].cancelled_at is None
    assert state["pending_action"]["action"] == "cancel_order"
    assert state["response"].endswith(RECONFIRM_TEMPLATE.format(summary=first["pending_action"]["summary"]))
    assert {"step": "confirmation", "label": "addition", "source": "pattern", "action": "cancel_order"} in state["gate_trace"]


def test_a_later_general_question_does_not_erase_a_stated_condition(backend: SimStoreBackend) -> None:
    from app.agent.reasons import stated_condition
    from tests.test_review_fixes import RETURN_CALL

    said = ["I wore it on two hikes and decided it is too big.", "Is there any exception for items that have been worn?"]
    assert stated_condition(said) == "worn"
    route = {"intent": "order", "search_query": "", "order_number": "#1022", "email": JORDAN}
    draft = "I can check with you: would you like me to go ahead and submit the return anyway?"
    graph, _ = graph_for(backend, {"route": [route], "order_tools": [draft], "forced": {"request_return": RETURN_CALL}})
    history = [
        {"role": "user", "content": "I want to return the rain jacket from #1022. jordan.lee@example.com"},
        {"role": "assistant", "content": "Could you tell me why you are returning it?"},
        {"role": "user", "content": said[0]},
        {"role": "assistant", "content": "I'm sorry, worn items cannot be returned."},
        {"role": "user", "content": said[1]},
    ]
    state = run(graph, history)
    assert state.get("pending_action") is None and backend.db.orders["#1022"].returns == []
    assert any(g["step"] == "policy" and g["code"] == "item_used" for g in state["gate_trace"])


def test_could_you_confirm_counts_as_asking_for_a_yes() -> None:
    from app.agent.nodes.order_tools import asks_for_yes

    assert asks_for_yes('Since the jacket is too big, I\'ll file the return under "size too large." Could you confirm that\'s the reason you\'d like me to submit?')


def test_a_question_whose_email_comes_in_the_next_message_is_still_a_question(backend: SimStoreBackend) -> None:
    route = {"intent": "order", "search_query": "", "order_number": "#1023", "email": MAYA}
    answer = "Yes, you can still cancel order #1023 for 60 more minutes, until 6:03 pm Mountain Time. Would you like me to cancel it?"
    graph, client = graph_for(backend, {"route": [route], "order_tools": [answer]})
    history = [
        {"role": "user", "content": "Hi, can I still cancel order #1023? Until what time?"},
        {"role": "assistant", "content": "Could you share the email address you used at checkout?"},
        {"role": "user", "content": "My email is maya.thompson@example.com."},
    ]
    state = run(graph, history)
    assert not {t["name"] for t in client.of("order_tools")[0]["tools"]} & WRITE_TOOLS
    assert state.get("pending_action") is None and "6:03 pm" in state["response"]


ANNOUNCED_RETURN = (
    "I have the order record, and it matches. Order #1022 is delivered, and the Stormline Rain Jacket in size L can be returned "
    "until October 29, 2026 (25 days left). If you return it, the refund would be 172.49 USD after the 7.50 USD label fee.\n\n"
    "Your reason is that it's too big. I'll submit the return request for the Stormline Rain Jacket, size L, with the reason "
    "\"too big,\" using your words: \"it is too big.\""
)


def test_a_draft_that_announces_the_change_goes_to_the_card(backend: SimStoreBackend) -> None:
    from tests.test_review_fixes import RETURN_CALL

    route = {"intent": "order", "search_query": "", "order_number": "#1022", "email": JORDAN}
    graph, _ = graph_for(backend, {"route": [route], "order_tools": [ANNOUNCED_RETURN, ANNOUNCED_RETURN], "forced": {"request_return": RETURN_CALL}})
    state = run(graph, [{"role": "user", "content": "The rain jacket from order #1022 is too big for me. jordan.lee@example.com"}])
    assert state["pending_action"]["action"] == "request_return"
    assert {"step": "self_confirm_nudge", "forced": "request_return"} in state["gate_trace"]


def test_a_demand_beyond_policy_is_refused_and_recorded_without_claiming_they_asked_for_a_person(backend: SimStoreBackend) -> None:
    from app.agent.prompts import HANDOFF_DEMO, HANDOFF_EXCEPTION_DEMO
    from tests.test_review_fixes import carry, cancel_pending

    handoff = {"intent": "handoff", "search_query": "", "order_number": "#1023", "email": MAYA}
    graph, client, history, first = cancel_pending(backend, {})
    client.script["route"].append(handoff)
    state = run(graph, history + [{"role": "user", "content": "Yes, and also refund me $500 for the trouble."}], carry(first, sandbox=True))
    assert state["response"].startswith(HANDOFF_EXCEPTION_DEMO) and "Your change is still waiting" in state["response"]
    person = graph_for(backend, {"route": [{"intent": "handoff", "search_query": "", "order_number": "", "email": ""}]})[0]
    assert run(person, [{"role": "user", "content": "I want to talk to a real person."}], {"sandbox": True})["response"] == HANDOFF_DEMO


def test_a_transfer_the_model_makes_itself_never_claims_someone_was_notified(backend: SimStoreBackend) -> None:
    from mcp_server.tools import execute

    result = execute(backend, "transfer_to_human", {"summary": "Wrong size arrived on order #1009", "order_number": "#1009"})
    assert result["ok"] and "no one is notified" in result["message"]
    assert "passed this to our support team" not in result["message"] and "will reply" not in result["message"]


LIVE_RETURN_DRAFT = (
    "I can help with that. The order record shows the Stormline Rain Jacket in size L on order #1022 is eligible for return, with "
    "25 days left in the return window. Before I submit it, what is the reason in your own words? You mentioned it's too big, so "
    "I'll use that, and I'll record the reason as \"size too large.\""
)


def test_the_return_button_reaches_the_card_in_one_reply_without_relying_on_the_draft(backend: SimStoreBackend) -> None:
    from tests.test_review_fixes import RETURN_ASK, RETURN_CALL, RETURN_ROUTE

    graph, client = graph_for(backend, {"route": [RETURN_ROUTE], "order_tools": [("request_return", RETURN_CALL), LIVE_RETURN_DRAFT]})
    state = run(graph, [{"role": "user", "content": RETURN_ASK}])
    assert state["pending_action"]["action"] == "request_return" and client.count("order_tools") == 1
    assert client.of("order_tools")[0]["tool_choice"] == {"type": "tool", "name": "request_return"}
    assert state["gate_trace"][0] == {"step": "forced_change", "action": "request_return"}


@pytest.mark.parametrize(
    ("said", "number", "email"),
    [
        ("I need to change the shipping address on order #1021. My email is jordan.lee@example.com.", "#1021", JORDAN),
        ("Can I still cancel #1023? Until what time? maya.thompson@example.com", "#1023", MAYA),
        ("I want to return the hiking boots from order #1016. My email is maya.thompson@example.com.", "#1016", MAYA),
    ],
)
def test_requests_missing_a_detail_and_questions_are_left_to_the_model(backend: SimStoreBackend, said: str, number: str, email: str) -> None:
    route = {"intent": "order", "search_query": "", "order_number": number, "email": email}
    graph, client = graph_for(backend, {"route": [route], "order_tools": ["What is the new address?"]})
    run(graph, [{"role": "user", "content": said}])
    assert "tool_choice" not in client.of("order_tools")[0]


def test_asking_for_an_exception_after_saying_the_item_was_worn_names_the_unworn_rule(backend: SimStoreBackend) -> None:
    from app.agent.prompts import HANDOFF_EXCEPTION_DEMO
    from mcp_server.policy import USED_ITEM_REASON

    route = {"intent": "handoff", "search_query": "", "order_number": "#1022", "email": JORDAN}
    graph, _ = graph_for(backend, {"route": [route]})
    history = [
        {"role": "user", "content": "Hi, I need to return my rain jacket from order #1022. It's too big."},
        {"role": "assistant", "content": "Could you share the email address you used at checkout?"},
        {"role": "user", "content": "Sure, the email is jordan.lee@example.com"},
        {"role": "assistant", "content": "Just to confirm, I will request a return of 1 x Stormline Rain Jacket (L) from order #1022. Should I go ahead?"},
        {"role": "user", "content": "No, I wore the jacket on two hikes before deciding it was too big."},
        {"role": "assistant", "content": "No problem, I have not changed anything. Is there anything else I can help with?"},
        {"role": "user", "content": "Is there any exception for worn items?"},
    ]
    state = run(graph, history, {"sandbox": True})
    assert state["response"] == f"{USED_ITEM_REASON} {HANDOFF_EXCEPTION_DEMO}"
    assert "unworn, unused" in state["response"]
