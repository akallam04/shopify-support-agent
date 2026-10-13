"""Tool-calling loop over the store tools, capped so a confused model cannot spin."""

import json
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import (
    CONDITION_NOTE,
    CORRECTION_TEMPLATE,
    GATE_FEEDBACK_TEMPLATE,
    ORDER_CONFIRM_RULE,
    ORDER_GATE_RULE,
    ORDER_SYSTEM,
    ORDER_WRITE_RULES,
    SAFE_FALLBACK_RESPONSE,
    SELF_CONFIRM_FEEDBACK,
)
from app.agent.facts import LOOKUP, facts_block, merge_facts
from app.agent.models import call_options, carries_thinking, forced_tool, system_blocks
from app.agent.reasons import customer_texts, stated_condition
from app.agent.state import AgentState
from app.agent.usage import usage_record
from app.config import Settings
from mcp_server import policy
from mcp_server.tools import HANDOFF_ACTION

CONDITION_WORDS = {"worn": "worn", "washed": "washed", "used": "used", "tags_removed": "missing its tags", "opened": "opened"}

MAX_TOOL_ROUNDS = 3
NUDGE_ROUNDS = 2

SELF_CONFIRM_RE = re.compile(
    r"\b(should i (go ahead|proceed)|shall i (go ahead|proceed)|would you like me to (go ahead|proceed)|"
    r"want me to (go ahead|proceed)|(is|does) (that|this|everything|all of (that|this)) (look |sound )?(right|correct)|"
    r"can you confirm|please confirm|just to confirm)",
    re.IGNORECASE,
)


CHANGE_WORDS = (
    ("request_return", re.compile(r"\breturn", re.IGNORECASE)),
    ("cancel_order", re.compile(r"\bcancel", re.IGNORECASE)),
    ("update_shipping_address", re.compile(r"\baddress\b", re.IGNORECASE)),
)


def asks_for_yes(text: str) -> bool:
    return bool(SELF_CONFIRM_RE.search(text))


def proposed_change(text: str, gated: set[str]) -> str | None:
    named = [name for name, pattern in CHANGE_WORDS if name in gated and pattern.search(text)]
    return named[0] if len(named) == 1 else None


def order_system(tools: Any, settings: Settings) -> str:
    if not tools.write_tool_names:
        return ORDER_SYSTEM
    gate_confirms = settings.mutation_gate and settings.gate_confirmation
    return ORDER_SYSTEM + ORDER_WRITE_RULES + (ORDER_GATE_RULE if gate_confirms else ORDER_CONFIRM_RULE)


def system_extra(state: AgentState, nudged: bool = False, fresh: list[dict[str, Any]] | None = None) -> str | None:
    known = facts_block(state, fresh or [])
    parts = [known] if known else []
    condition = stated_condition(customer_texts(state))
    refusal = policy.condition_refusal(condition)
    if refusal is not None:
        parts.append(CONDITION_NOTE.format(condition=CONDITION_WORDS[condition], reason=refusal.reason))
    if nudged:
        parts.append(SELF_CONFIRM_FEEDBACK)
    if state.get("gate_feedback"):
        parts.append(GATE_FEEDBACK_TEMPLATE.format(feedback=state["gate_feedback"]))
    if state.get("corrected_action"):
        parts.append(CORRECTION_TEMPLATE.format(summary=state["corrected_action"]))
    return "\n\n".join(parts) or None


def make_order_tools_node(client: AsyncAnthropic, model: str, tools: Any, settings: Settings):
    base_system = order_system(tools, settings)
    can_nudge = bool(tools.gated_tool_names) and settings.mutation_gate and settings.gate_confirmation

    async def lookup(state: AgentState) -> list[dict[str, Any]]:
        number, email = state.get("order_number"), state.get("email")
        if not (number and email) or LOOKUP not in tools.tool_names:
            return []
        args = {"order_number": number, "email": email}
        if any(c["name"] == LOOKUP and c["args"] == args for c in state.get("tool_results", [])):
            return []
        return [{"name": LOOKUP, "args": args, "result": await tools.call(LOOKUP, args)}]

    async def order_tools(state: AgentState) -> dict[str, Any]:
        fresh = await lookup(state)
        tool_results: list[dict[str, Any]] = list(state.get("tool_results", [])) + fresh
        system = system_blocks(base_system, state.get("context_digest"), system_extra(state, fresh=fresh))
        messages: list[Any] = list(state["messages"])
        executed: list[dict[str, Any]] = list(state.get("executed_actions", []))
        usage = list(state.get("usage", []))
        trace = list(state.get("gate_trace", []))
        handoff = state.get("handoff")
        read_only = bool(state.get("pending_action"))
        offered = [t for t in tools.anthropic_tools if not (read_only and t["name"] in tools.write_tool_names)]
        draft = ""
        nudged = False
        forced: str | None = None
        rounds, limit = 0, MAX_TOOL_ROUNDS

        while rounds < limit:
            rounds += 1
            response = await client.messages.create(
                **call_options(model, 1000, thinking=settings.model_thinking, cache_tail=True),
                **forced_tool(model, forced),
                system=system,
                messages=messages,
                tools=offered,
            )
            usage.append(usage_record("order_tools", model, response))
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                draft = "".join(b.text for b in response.content if b.type == "text").strip()
                if can_nudge and not read_only and not nudged and asks_for_yes(draft):
                    if carries_thinking(messages):
                        messages = list(state["messages"])
                        limit = rounds + NUDGE_ROUNDS
                    if rounds < limit:
                        nudged = True
                        forced = proposed_change(draft, tools.gated_tool_names)
                        trace.append({"step": "self_confirm_nudge", "forced": forced})
                        system = system_blocks(base_system, state.get("context_digest"), system_extra(state, nudged, fresh))
                        continue
                break

            gated = next((tu for tu in tool_uses if tu.name in tools.gated_tool_names), None)
            if gated is not None and settings.mutation_gate:
                candidate = {"name": gated.name, "args": dict(gated.input) if isinstance(gated.input, dict) else {}}
                return {
                    "gate_feedback": None,
                    "corrected_action": None,
                    "gate_trace": trace,
                    "candidate_action": candidate,
                    "tool_results": tool_results,
                    "executed_actions": executed,
                    "verified_facts": merge_facts(state.get("verified_facts") or [], tool_results),
                    "handoff": handoff,
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
                    outcome = json.loads(result_text)
                    executed.append(outcome)
                    if tu.name == HANDOFF_ACTION and outcome.get("ok"):
                        handoff = {"recorded": True, "notified": False}
                tool_results.append({"name": tu.name, "args": dict(tu.input), "result": result_text})
                results_content.append({"type": "tool_result", "tool_use_id": tu.id, "content": result_text})
            messages.append({"role": "user", "content": results_content})

        if not draft:
            draft = SAFE_FALLBACK_RESPONSE
        return {
            "draft": draft,
            "system_draft": False,
            "tool_results": tool_results,
            "executed_actions": executed,
            "verified_facts": merge_facts(state.get("verified_facts") or [], tool_results),
            "handoff": handoff,
            "usage": usage,
            "gate_feedback": None,
            "corrected_action": None,
            "gate_trace": trace,
        }

    return order_tools
