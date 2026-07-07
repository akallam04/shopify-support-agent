"""MCP client wrapper: one stdio session to the shopify tool server for the app's lifetime."""

import json
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ShopifyTools:
    """Launches the mcp server as a subprocess and holds the session open."""

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        # tool schemas converted to the anthropic tools parameter shape
        self.anthropic_tools: list[dict[str, Any]] = []

    async def start(self) -> None:
        self._stack = AsyncExitStack()
        params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"])
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        self.anthropic_tools = [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in listed.tools
        ]

    @property
    def tool_names(self) -> set[str]:
        return {t["name"] for t in self.anthropic_tools}

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if self._session is None:
            raise RuntimeError("ShopifyTools.start() was never called")
        result = await self._session.call_tool(name, args)
        text = "\n".join(b.text for b in result.content if b.type == "text")
        if result.isError:
            return json.dumps({"error": text or "tool call failed"})
        return text or "{}"

    async def aclose(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None
