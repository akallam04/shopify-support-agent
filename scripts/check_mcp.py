"""Drives the MCP server over stdio exactly like a real host would. Live store, read-only.

Run from the repo root: .venv/bin/python -m scripts.check_mcp
"""

import asyncio
import json
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = StdioServerParameters(command=".venv/bin/python", args=["-m", "mcp_server.server"])


def show(label: str, result: Any) -> None:
    print(f"\n--- {label} ---")
    for block in result.content:
        if block.type == "text":
            try:
                print(json.dumps(json.loads(block.text), indent=2))
            except json.JSONDecodeError:
                print(block.text)


async def main() -> None:
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = await session.list_tools()
            print("tools exposed by the server:")
            for tool in listed.tools:
                first_line = (tool.description or "").strip().splitlines()[0]
                print(f"  - {tool.name}: {first_line}")

            show(
                "order lookup, correct email",
                await session.call_tool(
                    "get_order_status",
                    {"order_number": "#1001", "email": "maya.thompson@example.com"},
                ),
            )
            show(
                "order lookup, wrong email (must look identical to not found)",
                await session.call_tool(
                    "get_order_status",
                    {"order_number": "#1001", "email": "jordan.lee@example.com"},
                ),
            )
            show(
                "customer order history",
                await session.call_tool(
                    "list_customer_orders", {"email": "maya.thompson@example.com"}
                ),
            )
            show(
                "live inventory search",
                await session.call_tool("check_inventory", {"product_query": "rain jacket"}),
            )


if __name__ == "__main__":
    asyncio.run(main())
