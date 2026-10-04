"""In-process tool execution against one store backend, with the same schemas the MCP server serves."""

import inspect
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server import server, tools
from mcp_server.backends.base import StoreBackend

READ_FUNCTIONS = {
    "get_order_status": tools.get_order_status,
    "list_customer_orders": tools.list_customer_orders,
    "check_inventory": tools.check_inventory,
}


async def tool_specs(include_writes: bool) -> list[dict[str, Any]]:
    registry = FastMCP("aurora-outfitters-support-specs")
    for fn in server.READ_TOOLS + (server.WRITE_TOOLS if include_writes else ()):
        registry.add_tool(fn)
    listed = await registry.list_tools()
    return [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema} for t in listed]


class InProcessTools:
    def __init__(self, backend: StoreBackend, include_writes: bool) -> None:
        if include_writes and not backend.supports_writes:
            raise ValueError(f"the {backend.name} backend does not accept writes")
        self.backend = backend
        self.include_writes = include_writes
        self.anthropic_tools: list[dict[str, Any]] = []

    async def start(self) -> None:
        self.anthropic_tools = await tool_specs(self.include_writes)

    @property
    def tool_names(self) -> set[str]:
        return {t["name"] for t in self.anthropic_tools}

    @property
    def write_tool_names(self) -> set[str]:
        return set(tools.WRITE_ACTIONS) | {tools.HANDOFF_ACTION} if self.include_writes else set()

    @property
    def gated_tool_names(self) -> set[str]:
        return set(tools.WRITE_ACTIONS) if self.include_writes else set()

    def prepare(self, name: str, args: dict[str, Any]) -> tools.Prepared:
        return tools.prepare(self.backend, name, args)

    def execute(self, name: str, args: dict[str, Any], key: str | None = None) -> dict[str, Any]:
        return tools.execute(self.backend, name, args, key)

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if name not in self.tool_names:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            if name in self.write_tool_names:
                return json.dumps(self.execute(name, args))
            fn = READ_FUNCTIONS[name]
            params = list(inspect.signature(fn).parameters)[1:]
            missing = [p for p in params if p not in args]
            if missing:
                return json.dumps({"error": f"missing arguments: {', '.join(missing)}"})
            return json.dumps(fn(self.backend, **{p: args[p] for p in params}))
        except tools.ToolInputError as e:
            return json.dumps({"error": str(e)})

    async def aclose(self) -> None:
        return None
