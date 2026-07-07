"""Tool-calling loop over the MCP session, capped so a confused model cannot spin."""

import json
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import ORDER_SYSTEM, SAFE_FALLBACK_RESPONSE
from app.agent.state import AgentState
from app.mcp_client import ShopifyTools

MAX_TOOL_ROUNDS = 3


def make_order_tools_node(client: AsyncAnthropic, model: str, tools: ShopifyTools):
    async def order_tools(state: AgentState) -> dict[str, Any]:
        messages: list[Any] = list(state["messages"])
        tool_results: list[dict[str, Any]] = []
        usage = list(state.get("usage", []))
        draft = ""

        for _ in range(MAX_TOOL_ROUNDS):
            response = await client.messages.create(
                model=model,
                max_tokens=1000,
                system=ORDER_SYSTEM,
                messages=messages,
                tools=tools.anthropic_tools,
            )
            usage.append(
                {
                    "node": "order_tools",
                    "model": model,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
            )
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                draft = "".join(b.text for b in response.content if b.type == "text").strip()
                break

            messages.append({"role": "assistant", "content": response.content})
            results_content = []
            for tu in tool_uses:
                # allowlist + shape check before anything reaches the server
                if tu.name not in tools.tool_names or not isinstance(tu.input, dict):
                    result_text = json.dumps({"error": f"unknown tool {tu.name}"})
                else:
                    result_text = await tools.call(tu.name, dict(tu.input))
                tool_results.append(
                    {"name": tu.name, "args": dict(tu.input), "result": result_text}
                )
                results_content.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": result_text}
                )
            messages.append({"role": "user", "content": results_content})

        if not draft:
            draft = SAFE_FALLBACK_RESPONSE
        return {"draft": draft, "tool_results": tool_results, "usage": usage}

    return order_tools
