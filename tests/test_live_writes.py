from typing import Any

import pytest

from app.config import Settings
from mcp_server.backends import shopify as live
from mcp_server.backends.shopify import ShopifyAdminBackend
from mcp_server.clock import FrozenClock, parse_instant
from mcp_server.server import build_backend
from mcp_server.tools import STORE_ERROR_REASON, execute
from tests.test_tools import order_node


class ScriptedGraphQL:
    def __init__(self, routes: dict[str, list[dict[str, Any]]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        for marker, replies in self.routes.items():
            if marker in query:
                self.calls.append((marker, variables or {}))
                return replies.pop(0) if len(replies) > 1 else replies[0]
        raise AssertionError(f"unexpected query: {query[:60]}")


def unfulfilled_node(name: str = "#2001") -> dict[str, Any]:
    node = order_node(name)
    node.update({"displayFulfillmentStatus": "UNFULFILLED", "fulfillments": [], "processedAt": "2026-10-04T21:54:00Z"})
    node["returns"] = {"nodes": []}
    return node


def backend(write: ScriptedGraphQL) -> ShopifyAdminBackend:
    b = ShopifyAdminBackend(ScriptedGraphQL({"ProductsById": [{"nodes": []}]}), write)
    b.clock = FrozenClock(parse_instant("2026-10-04T22:30:00Z"))
    return b


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.time, "sleep", lambda _: None)


ARGS = {"order_number": "#2001", "email": "maya.thompson@example.com", "reason": "ordered_by_mistake"}


def test_cancel_sends_the_agreed_options_and_waits_for_shopify() -> None:
    write = ScriptedGraphQL({
        "OrderWithReturns": [{"orders": {"nodes": [unfulfilled_node()]}}],
        "orderCancel(": [{"orderCancel": {"job": {"id": "j1"}, "orderCancelUserErrors": []}}],
        "query Cancelled": [{"order": {"cancelledAt": None}}, {"order": {"cancelledAt": "2026-10-04T22:30:05Z"}}],
    })
    result = execute(backend(write), "cancel_order", ARGS)
    assert result["ok"] is True
    mutation = next(q for q, _ in write.calls if q == "orderCancel(")
    assert mutation
    assert [q for q, _ in write.calls].count("query Cancelled") == 2
    assert "restock: false" in live.ORDER_CANCEL_MUTATION and "originalPaymentMethodsRefund: true" in live.ORDER_CANCEL_MUTATION


def test_a_shopify_error_changes_nothing_and_is_not_cached() -> None:
    write = ScriptedGraphQL({
        "OrderWithReturns": [{"orders": {"nodes": [unfulfilled_node()]}}],
        "orderCancel(": [{"orderCancel": {"job": None, "orderCancelUserErrors": [{"field": None, "message": "Cannot cancel", "code": "X"}]}}],
    })
    b = backend(write)
    result = execute(b, "cancel_order", ARGS)
    assert result == {"ok": False, "action": "cancel_order", "code": "store_error", "reason": STORE_ERROR_REASON}
    assert b.executed == {}
    assert b.audit[-1]["outcome"] == "failed" and "Cannot cancel" in b.audit[-1]["detail"]


def test_address_change_uses_shopify_field_names() -> None:
    write = ScriptedGraphQL({
        "OrderWithReturns": [{"orders": {"nodes": [unfulfilled_node()]}}],
        "orderUpdate(": [{"orderUpdate": {"order": {"id": "o"}, "userErrors": []}}],
    })
    args = {**ARGS, "address1": "9 Pine St", "city": "Boulder", "province_code": "co", "zip": "80302", "country_code": "us"}
    args.pop("reason")
    assert execute(backend(write), "update_shipping_address", args)["ok"] is True
    sent = next(v for q, v in write.calls if q == "orderUpdate(")["input"]["shippingAddress"]
    assert sent["countryCode"] == "US" and sent["provinceCode"] == "CO" and "address2" not in sent


def delivered_node() -> dict[str, Any]:
    node = order_node("#2002", delivered_at="2026-09-29T22:54:00Z")
    node["returns"] = {"nodes": [{"status": "DECLINED", "returnLineItems": {"nodes": [{"quantity": 1, "fulfillmentLineItem": {"lineItem": {"id": "gid://shopify/LineItem/1"}}, "returnReasonDefinition": {"handle": "too-small"}}]}}]}
    return node


def test_returns_map_order_lines_to_fulfilled_lines_and_ignore_declined_returns() -> None:
    write = ScriptedGraphQL({
        "OrderWithReturns": [{"orders": {"nodes": [delivered_node()]}}],
        "returnReasonDefinitions": [{"returnReasonDefinitions": {"nodes": [{"id": "gid://shopify/ReturnReasonDefinition/7", "handle": "too-small"}]}}],
        "returnRequest(": [{"returnRequest": {"return": {"id": "r", "status": "REQUESTED"}, "userErrors": []}}],
    })
    args = {"order_number": "#2002", "email": "maya.thompson@example.com", "reason": "size_too_small", "items": [{"title": "rain jacket"}]}
    b = backend(write)
    b.clock = FrozenClock(parse_instant("2026-10-04T22:54:00Z"))
    assert execute(b, "request_return", args)["ok"] is True
    item = next(v for q, v in write.calls if q == "returnRequest(")["input"]["returnLineItems"][0]
    assert item == {
        "fulfillmentLineItemId": "gid://shopify/FulfillmentLineItem/1",
        "quantity": 1,
        "returnReasonDefinitionId": "gid://shopify/ReturnReasonDefinition/7",
        "customerNote": "Reason: size_too_small",
    }


def test_live_writes_need_the_write_token() -> None:
    settings = Settings(_env_file=None, store_backend="shopify", write_actions=True, shopify_store_domain="x.myshopify.com", shopify_admin_token="read")
    with pytest.raises(SystemExit):
        build_backend(settings)


def test_read_only_backend_reports_no_write_support() -> None:
    assert ShopifyAdminBackend(ScriptedGraphQL({})).supports_writes is False


def test_returns_map_back_to_the_agent_reasons() -> None:
    from mcp_server.backends.shopify import map_returns

    node = {"returns": {"nodes": [{"status": "REQUESTED", "returnLineItems": {"nodes": [
        {"quantity": 1, "fulfillmentLineItem": {"lineItem": {"id": "l1"}}, "returnReasonDefinition": {"handle": "too-small"}}
    ]}}]}}
    [request] = map_returns(node)
    assert (request.status, request.reason, request.line_items[0].line_item_id) == ("REQUESTED", "size_too_small", "l1")
