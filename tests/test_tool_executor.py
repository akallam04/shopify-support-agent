import asyncio
import json

import pytest

from app.agent.tool_executor import InProcessTools, tool_specs
from mcp_server import policy, server, tools
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.clock import FrozenClock, parse_instant
from mcp_server.simdb import SimDB

READ = {"get_order_status", "list_customer_orders", "check_inventory"}
WRITE = {"cancel_order", "update_shipping_address", "request_return", "transfer_to_human"}


def started(db: SimDB, include_writes: bool = True) -> InProcessTools:
    backend = SimStoreBackend(db, FrozenClock(parse_instant("2026-07-07T01:00:00Z")))
    executor = InProcessTools(backend, include_writes)
    asyncio.run(executor.start())
    return executor


def specs_by_name(executor: InProcessTools) -> dict:
    return {t["name"]: t for t in executor.anthropic_tools}


def test_read_only_executor_exposes_only_read_tools(db: SimDB) -> None:
    executor = started(db, include_writes=False)
    assert executor.tool_names == READ
    assert executor.write_tool_names == executor.gated_tool_names == set()


def test_write_executor_gates_mutations_but_not_handoffs(db: SimDB) -> None:
    executor = started(db)
    assert executor.tool_names == READ | WRITE
    assert executor.gated_tool_names == {"cancel_order", "update_shipping_address", "request_return"}


def test_read_schemas_match_what_the_mcp_server_serves() -> None:
    served = {t.name: t.inputSchema for t in asyncio.run(server.mcp.list_tools())}
    built = {t["name"]: t["input_schema"] for t in asyncio.run(tool_specs(include_writes=False))}
    assert built == served


def test_reason_enums_come_from_the_policy(db: SimDB) -> None:
    specs = specs_by_name(started(db))
    assert tuple(specs["cancel_order"]["input_schema"]["properties"]["reason"]["enum"]) == policy.CANCEL_REASONS
    schema = specs["request_return"]["input_schema"]
    assert tuple(schema["properties"]["reason"]["enum"]) == policy.RETURN_REASONS
    assert "items" in schema["required"]


def test_read_calls_match_the_shared_tools(db: SimDB) -> None:
    executor = started(db)
    args = {"order_number": "#1001", "email": "maya.thompson@example.com"}
    out = json.loads(asyncio.run(executor.call("get_order_status", args)))
    assert out == tools.get_order_status(executor.backend, **args)


def test_write_calls_execute_once(db: SimDB) -> None:
    executor = started(db)
    args = {"order_number": "#1002", "email": "maya.thompson@example.com", "reason": "changed_mind"}
    first = json.loads(asyncio.run(executor.call("cancel_order", args)))
    second = json.loads(asyncio.run(executor.call("cancel_order", args)))
    assert first["ok"] is True and second["duplicate"] is True


def test_unknown_tools_and_bad_arguments_return_errors(db: SimDB) -> None:
    executor = started(db)
    assert "unknown tool" in json.loads(asyncio.run(executor.call("refund_everything", {})))["error"]
    assert "missing arguments" in json.loads(asyncio.run(executor.call("get_order_status", {"order_number": "#1001"})))["error"]
    bad = json.loads(asyncio.run(executor.call("get_order_status", {"order_number": "abc", "email": "x@y.com"})))
    assert "order_number" in bad["error"]


def test_writes_need_a_backend_that_accepts_them(db: SimDB) -> None:
    backend = SimStoreBackend(db)
    backend.supports_writes = False
    with pytest.raises(ValueError):
        InProcessTools(backend, include_writes=True)
