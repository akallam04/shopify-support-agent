"""Intent classification and slot extraction, one structured call on the router model."""

import json
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import ROUTER_SCHEMA, ROUTER_SYSTEM
from app.agent.state import AgentState


def make_route_node(client: AsyncAnthropic, model: str):
    async def route(state: AgentState) -> dict[str, Any]:
        # no sampling params: newer models reject them, and the enum schema
        # constrains the output anyway
        response = await client.messages.create(
            model=model,
            max_tokens=300,
            system=ROUTER_SYSTEM,
            messages=state["messages"],
            output_config={"format": {"type": "json_schema", "schema": ROUTER_SCHEMA}},
        )
        parsed = json.loads(next(b.text for b in response.content if b.type == "text"))
        usage = list(state.get("usage", []))
        usage.append(
            {
                "node": "route",
                "model": model,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
        )
        return {
            "intent": parsed["intent"],
            "search_query": parsed["search_query"],
            "order_number": parsed["order_number"],
            "email": parsed["email"],
            "usage": usage,
        }

    return route
