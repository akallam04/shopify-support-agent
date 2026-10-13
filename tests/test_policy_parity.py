from datetime import timedelta
from typing import Any

import pytest

from app.agent.addresses import ungrounded_parts
from app.agent.nodes.retrieve import make_retrieve_node
from app.agent.prompts import ADDRESS_ASK
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import parse_instant
from mcp_server.policy import can_change_address, can_return, return_terms
from mcp_server.simdb import Address, FulfilledLine, Fulfillment, load_db
from mcp_server.tools import ToolInputError, prepare
from tests.test_review_fixes import ADDRESS_CALL, JORDAN, RETURN_CALL, SEED, graph_for, run

WORN_ASK = "I have already worn my rain jacket outdoors for a week. Order #1022, jordan.lee@example.com. It is too big."
BANANA_ASK = "Please change the shipping address on order #1021 to 1200 Larimer St, Denver, XX banana, US. My email is jordan.lee@example.com."
CANADA_ASK = "I live in Canada. How much does returning an unwanted jacket cost? Do I get a prepaid label?"
RETURN_ROUTE = {"intent": "order", "search_query": "", "order_number": "#1022", "email": JORDAN}
ADDRESS_ROUTE = {"intent": "order", "search_query": "", "order_number": "#1021", "email": JORDAN}


@pytest.fixture
def backend() -> SimStoreBackend:
    return SimStoreBackend(load_db(SEED))


def jacket(backend: SimStoreBackend) -> Any:
    return backend.db.orders["#1022"].line_items[0]


def decide(backend: SimStoreBackend, requested: list[tuple[Any, int]], products: dict[str, Any] | None = None, condition: str = "", reason: str = "size_too_large"):
    order = backend.db.orders["#1022"]
    return can_return(order, requested, backend.db.products if products is None else products, reason, backend.clock.now(), condition)


def test_a_worn_item_is_refused_in_code_even_with_a_valid_reason(backend: SimStoreBackend) -> None:
    graph, _ = graph_for(backend, {"route": [RETURN_ROUTE], "order_tools": [("request_return", {**RETURN_CALL, "reason_quote": "It is too big"})]})
    state = run(graph, [{"role": "user", "content": WORN_ASK}])
    assert state.get("pending_action") is None
    assert "worn, washed, or used" in state["response"]
    check = next(g for g in state["gate_trace"] if g["step"] == "condition_check")
    assert (check["condition"], check["stated"]) == ("worn", "yes") and "worn my rain jacket" in check["quote"]
    assert backend.db.orders["#1022"].returns == []


@pytest.mark.parametrize("condition", ["worn", "washed", "used", "tags_removed"])
def test_every_used_condition_is_refused(backend: SimStoreBackend, condition: str) -> None:
    assert decide(backend, [(jacket(backend), 1)], condition=condition).code == "item_used"


def test_an_opened_consumable_is_refused(backend: SimStoreBackend) -> None:
    assert decide(backend, [(jacket(backend), 1)], condition="opened").code == "opened_consumable"


def test_damaged_or_defective_items_go_to_the_photo_process_not_a_return(backend: SimStoreBackend) -> None:
    decision = decide(backend, [(jacket(backend), 1)], reason="defective")
    assert decision.code == "damaged_or_defective" and "photos" in decision.reason and "7 days" in decision.reason


def test_the_return_card_says_the_item_must_be_unworn_and_unused(backend: SimStoreBackend) -> None:
    prepared = prepare(backend, "request_return", {**RETURN_CALL, "reason_quote": "It is too big"})
    assert prepared.allowed and "must be unworn and unused" in prepared.summary


@pytest.mark.parametrize("case", ["missing product", "missing id", "deleted product", "missing tags"])
def test_returns_fail_closed_when_product_data_is_missing(backend: SimStoreBackend, case: str) -> None:
    line = jacket(backend)
    products = dict(backend.db.products)
    if case == "missing product":
        products.pop(line.product_id)
    elif case == "missing id":
        line = line.model_copy(update={"product_id": None})
    elif case == "deleted product":
        products = {}
    else:
        products[line.product_id] = products[line.product_id].model_copy(update={"tags": None})
    assert decide(backend, [(line, 1)], products).code == "product_unverified"


def test_two_one_of_one_lines_for_a_single_jacket_are_added_up_and_refused(backend: SimStoreBackend) -> None:
    two_lines = {**RETURN_CALL, "items": [{"title": "Stormline Rain Jacket", "quantity": 1}, {"title": "Stormline Rain Jacket", "quantity": 1}]}
    prepared = prepare(backend, "request_return", two_lines)
    assert not prepared.allowed and prepared.code == "quantity_exceeds"


@pytest.mark.parametrize("quantity", [1.5, True, 0, -1, "1"])
def test_quantities_must_be_whole_numbers_of_at_least_one(backend: SimStoreBackend, quantity: Any) -> None:
    with pytest.raises(ToolInputError):
        prepare(backend, "request_return", {**RETURN_CALL, "items": [{"title": "Stormline Rain Jacket", "quantity": quantity}]})


def test_returnable_quantity_counts_only_what_was_delivered(backend: SimStoreBackend) -> None:
    order = backend.db.orders["#1022"]
    line = order.line_items[0].model_copy(update={"quantity": 2})
    order.line_items = [line]
    assert decide(backend, [(line, 2)]).code == "quantity_exceeds"
    extra = Fulfillment(fulfillment_id="f2", status="SUCCESS", created_at=order.processed_at, delivered_at=order.fulfillments[0].delivered_at,
                        line_items=[FulfilledLine(fulfillment_line_item_id="fl2", line_item_id=line.line_item_id, quantity=1)])
    order.fulfillments = order.fulfillments + [extra]
    assert decide(backend, [(line, 2)]).allowed


def address(**overrides: str | None) -> Address:
    fields = {"address1": "1200 Larimer St", "city": "Denver", "province_code": "CO", "zip": "80204", "country_code": "US"}
    return Address(**{**fields, **overrides})


def change(backend: SimStoreBackend, new: Address):
    order = backend.db.orders["#1021"]
    return can_change_address(order, new, parse_instant(order.processed_at) + timedelta(minutes=30))


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({}, "ok"),
        ({"zip": "80204-1234"}, "ok"),
        ({"province_code": "XX", "zip": "banana"}, "invalid_province"),
        ({"zip": "banana"}, "invalid_postal_code"),
        ({"zip": "8020"}, "invalid_postal_code"),
        ({"province_code": "BC"}, "invalid_province"),
        ({"province_code": "BC", "zip": "V6B 1A1", "country_code": "CA", "city": "Vancouver"}, "ok"),
        ({"province_code": "BC", "zip": "V6B1A1", "country_code": "CA", "city": "Vancouver"}, "ok"),
        ({"province_code": "BC", "zip": "123456", "country_code": "CA", "city": "Vancouver"}, "invalid_postal_code"),
        ({"city": None}, "incomplete_address"),
    ],
)
def test_addresses_are_validated_in_code(backend: SimStoreBackend, overrides: dict[str, str | None], code: str) -> None:
    assert change(backend, address(**overrides)).code == code


def test_whitespace_only_address_fields_count_as_missing(backend: SimStoreBackend) -> None:
    prepared = prepare(backend, "update_shipping_address", {**ADDRESS_CALL, "city": "   "})
    assert not prepared.allowed and prepared.code == "incomplete_address"


def test_the_banana_address_fails_in_code(backend: SimStoreBackend) -> None:
    banana = {**ADDRESS_CALL, "province_code": "XX", "zip": "banana"}
    graph, _ = graph_for(backend, {"route": [ADDRESS_ROUTE], "order_tools": [("update_shipping_address", banana)]})
    state = run(graph, [{"role": "user", "content": BANANA_ASK}])
    assert state.get("pending_action") is None
    assert "XX is not a US state code" in state["response"]


def test_address_parts_the_customer_never_gave_are_not_filled_in(backend: SimStoreBackend) -> None:
    graph, _ = graph_for(backend, {"route": [ADDRESS_ROUTE], "order_tools": [("update_shipping_address", {**ADDRESS_CALL, "zip": "80202"})]})
    state = run(graph, [{"role": "user", "content": "Please change the shipping address on order #1021 to 1200 Larimer St, Denver. My email is jordan.lee@example.com."}])
    assert state["response"] == ADDRESS_ASK and state.get("pending_action") is None


def test_state_names_and_street_abbreviations_still_count_as_the_customers_words() -> None:
    said = ["Ship it to 1200 Larimer Street, Denver, Colorado 80204 please."]
    assert ungrounded_parts({**ADDRESS_CALL, "address1": "1200 Larimer St"}, said) == []


def canadian(backend: SimStoreBackend) -> SimStoreBackend:
    order = backend.db.orders["#1022"]
    order.shipping_address = order.shipping_address.model_copy(update={"country_code": "CA", "province_code": "BC", "city": "Vancouver", "zip": "V6B 1A1"})
    return backend


def test_canadian_returns_pay_no_label_fee_and_ship_at_their_own_cost(backend: SimStoreBackend) -> None:
    terms = return_terms("unwanted", "CA")
    assert "No return shipping fee" in terms["fee"] and "own cost" in terms["label"]
    prepared = prepare(canadian(backend), "request_return", {**RETURN_CALL, "reason_quote": "It is too big"})
    assert "7.50" not in prepared.summary and "own cost" in prepared.summary and "prepaid" not in prepared.summary.replace("do not use our prepaid label", "")


class ChunkStore:
    def __init__(self, hits: list[str]) -> None:
        self.hits = hits
        self.fetched: list[str] = []

    def query(self, collection: str, text: str, k: int = 4) -> list[dict[str, Any]]:
        return [{"id": i, "text": i, "metadata": {}} for i in self.hits]

    def get(self, collection: str, ids: list[str]) -> list[dict[str, Any]]:
        self.fetched += ids
        return [{"id": i, "text": i, "metadata": {}} for i in ids]


def retrieved(hits: list[str], text: str) -> list[str]:
    node = make_retrieve_node(ChunkStore(hits))
    return [d["id"] for d in node({"intent": "policy", "search_query": "", "messages": [{"role": "user", "content": text}]})["retrieved"]]


def test_the_fee_rule_brings_its_canada_exception_along() -> None:
    ids = retrieved(["policy-returns-return-shipping-cost"], "How much is return shipping?")
    assert "policy-returns-how-to-start-a-return" in ids


def test_a_canada_question_gets_the_canada_return_rules() -> None:
    ids = retrieved(["policy-returns-return-window", "policy-returns-exchanges"], CANADA_ASK)
    assert {"policy-returns-how-to-start-a-return", "policy-returns-return-shipping-cost"} <= set(ids)


def test_a_question_without_canada_gets_no_extra_canada_chunks() -> None:
    assert retrieved(["policy-returns-return-window"], "How long do I have to return a jacket?") == ["policy-returns-return-window"]


def test_every_test_named_in_the_parity_doc_exists() -> None:
    import re
    from pathlib import Path

    named = set(re.findall(r"`(test_[a-z0-9_]+)`", Path("docs/policy-parity.md").read_text()))
    defined = {m for p in Path("tests").glob("test_*.py") for m in re.findall(r"^def (test_[a-z0-9_]+)", p.read_text(), re.M)}
    assert named and named <= defined, sorted(named - defined)


def test_a_stated_condition_reaches_the_order_context_even_without_a_tool_call(backend: SimStoreBackend) -> None:
    from tests.test_review_fixes import system_text

    graph, client = graph_for(backend, {"route": [RETURN_ROUTE], "order_tools": ["That jacket cannot be returned because it has been worn."]})
    run(graph, [{"role": "user", "content": WORN_ASK}])
    system = system_text(client.of("order_tools")[0])
    assert "The customer said the item was worn" in system and "cannot be returned" in system



@pytest.mark.parametrize(
    "said",
    [
        "The reason is that the wrong size arrived; I ordered a medium but received a large.",
        "Hi, I received a large Sierra Sun Hoody on order #1009 instead of the medium I ordered.",
    ],
)
def test_a_wrong_size_that_arrived_is_a_wrong_item_return(said: str) -> None:
    from app.agent.reasons import stated_reason

    found = stated_reason([said])
    assert found is not None and found.codes == ("wrong_item",)


def test_an_ambiguous_fit_phrase_keeps_the_models_wrong_item_code() -> None:
    from app.agent.nodes.gate import customer_reason

    args = {"reason": "wrong_item", "reason_quote": "it is the wrong size"}
    state = {"messages": [{"role": "user", "content": "It is the wrong size."}]}
    assert customer_reason(args, state)["reason"] == "wrong_item"



def test_a_specific_reason_in_an_earlier_message_beats_a_vague_one_later() -> None:
    from app.agent.reasons import stated_reason

    said = [
        "Hi, I received the wrong size for my Sierra Sun Hoody. Order #1009",
        "ethan.brooks@example.com",
        "I ordered a medium, but a large arrived. I just want to return the hoody because of the wrong size.",
    ]
    assert stated_reason(said).codes == ("wrong_item",)
    assert stated_reason(said[2:]).codes == ("wrong_item",)
