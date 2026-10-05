"""Tool-calling loop over the store tools, capped so a confused model cannot spin."""

import json
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import (
    GATE_FEEDBACK_TEMPLATE,
    ORDER_CONFIRM_RULE,
    ORDER_GATE_RULE,
    ORDER_SYSTEM,
    ORDER_WRITE_RULES,
    SAFE_FALLBACK_RESPONSE,
)
from app.agent.models import call_options, system_blocks
from app.agent.state import AgentState
from app.agent.usage import usage_record
from app.config import Settings

MAX_TOOL_ROUNDS = 3


def order_system(tools: Any, settings: Settings) -> str:
    if not tools.write_tool_names:
        return ORDER_SYSTEM
    gate_confirms = settings.mutation_gate and settings.gate_confirmation
    return ORDER_SYSTEM + ORDER_WRITE_RULES + (ORDER_GATE_RULE if gate_confirms else ORDER_CONFIRM_RULE)


def make_order_tools_node(client: AsyncAnthropic, model: str, tools: Any, settings: Settings):
    base_system = order_system(tools, settings)

    async def order_tools(state: AgentState) -> dict[str, Any]:
        feedback = state.get("gate_feedback")
        extra = GATE_FEEDBACK_TEMPLATE.format(feedback=feedback) if feedback else None
        system = system_blocks(base_system, state.get("context_digest"), extra)
        messages: list[Any] = list(state["messages"])
        tool_results: list[dict[str, Any]] = list(state.get("tool_results", []))
        executed: list[dict[str, Any]] = list(state.get("executed_actions", []))
        usage = list(state.get("usage", []))
        draft = ""

        for _ in range(MAX_TOOL_ROUNDS):
            response = await client.messages.create(
                **call_options(model, 1000),
                system=system,
                messages=messages,
                tools=tools.anthropic_tools,
            )
            usage.append(usage_record("order_tools", model, response))
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                draft = "".join(b.text for b in response.content if b.type == "text").strip()
                break

            gated = next((tu for tu in tool_uses if tu.name in tools.gated_tool_names), None)
            if gated is not None and settings.mutation_gate:
                candidate = {"name": gated.name, "args": dict(gated.input) if isinstance(gated.input, dict) else {}}
                return {
                    "gate_feedback": None,
                    "candidate_action": candidate,
                    "tool_results": tool_results,
                    "executed_actions": executed,
                    "usage": usage,
                    "draft": "",
                }

            messages.append({"role": "assistant", "content": response.content})
            results_content = []
            for tu in tool_uses:
                if tu.name not in tools.tool_names or not isinstance(tu.input, dict):
                    result_text = json.dumps({"error": f"unknown tool {tu.name}"})
                else:
                    result_text = await tools.call(tu.name, dict(tu.input))
                if tu.name in tools.write_tool_names:
                    executed.append(json.loads(result_text))
                tool_results.append({"name": tu.name, "args": dict(tu.input), "result": result_text})
                results_content.append({"type": "tool_result", "tool_use_id": tu.id, "content": result_text})
            messages.append({"role": "user", "content": results_content})

        if not draft:
            draft = SAFE_FALLBACK_RESPONSE
        return {"draft": draft, "tool_results": tool_results, "executed_actions": executed, "usage": usage, "gate_feedback": None}

    return order_tools
