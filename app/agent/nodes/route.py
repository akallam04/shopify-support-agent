"""Intent classification and slot extraction, one structured call on the router model."""

import json
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import ROUTER_SCHEMA, ROUTER_SYSTEM, with_digest
from app.agent.models import call_options
from app.agent.state import AgentState
from app.agent.usage import usage_record


def make_route_node(client: AsyncAnthropic, model: str):
    async def route(state: AgentState) -> dict[str, Any]:
        # no sampling params: newer models reject them, and the enum schema
        # constrains the output anyway
        response = await client.messages.create(
            **call_options(model, 300, {"type": "json_schema", "schema": ROUTER_SCHEMA}),
            system=with_digest(ROUTER_SYSTEM, state.get("context_digest")),
            messages=state["messages"],
        )
        parsed = json.loads(next(b.text for b in response.content if b.type == "text"))
        usage = list(state.get("usage", []))
        usage.append(usage_record("route", model, response))
        return {
            "intent": parsed["intent"],
            "search_query": parsed["search_query"],
            "order_number": parsed["order_number"],
            "email": parsed["email"],
            "usage": usage,
        }

    return route
