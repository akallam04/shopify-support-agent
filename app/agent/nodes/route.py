"""Intent classification and slot extraction, one structured call on the router model."""

import json
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.models import call_options, response_text, system_blocks
from app.agent.prompts import ROUTER_SCHEMA, ROUTER_SYSTEM, ROUTER_WRITE_RULE
from app.agent.state import AgentState
from app.agent.usage import usage_record


DECLINED_ROUTE = {"intent": "out_of_scope", "search_query": "", "order_number": "", "email": ""}


def make_route_node(client: AsyncAnthropic, model: str, can_act: bool = False, thinking: bool = True):
    system = ROUTER_SYSTEM + (ROUTER_WRITE_RULE if can_act else "")

    async def route(state: AgentState) -> dict[str, Any]:
        # no sampling params: newer models reject them, and the enum schema
        # constrains the output anyway
        response = await client.messages.create(
            **call_options(model, 300, {"type": "json_schema", "schema": ROUTER_SCHEMA}, thinking=thinking, cache_tail=True),
            system=system_blocks(system, state.get("context_digest")),
            messages=state["messages"],
        )
        text = response_text(response)
        parsed = json.loads(text) if text else DECLINED_ROUTE
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
