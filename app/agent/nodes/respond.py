"""Answer generation: static templates for refusal paths, grounded generation elsewhere."""

from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import (
    GROUNDED_SYSTEM,
    HANDOFF_RESPONSE,
    INJECTION_RESPONSE,
    ORDER_ASK_SYSTEM,
    OUT_OF_SCOPE_RESPONSE,
    RETRY_SYSTEM,
    SMALLTALK_SYSTEM,
)
from app.agent.state import AgentState

# refusal and escalation paths are static on purpose: deterministic, free,
# and hostile input never gets to steer the wording
STATIC_RESPONSES = {
    "injection": INJECTION_RESPONSE,
    "out_of_scope": OUT_OF_SCOPE_RESPONSE,
    "handoff": HANDOFF_RESPONSE,
}


def _context_block(docs: list[dict[str, Any]]) -> str:
    parts = []
    for d in docs:
        cite_id = d["metadata"].get("handle") or d["id"]
        parts.append(f"[id: {cite_id}]\n{d['text']}")
    return "\n\n".join(parts)


def make_respond_node(client: AsyncAnthropic, model: str):
    async def respond(state: AgentState) -> dict[str, Any]:
        if state.get("hard_injection"):
            return {"draft": INJECTION_RESPONSE}
        intent = state["intent"]
        if intent in STATIC_RESPONSES:
            return {"draft": STATIC_RESPONSES[intent]}

        feedback = state.get("verify_feedback")
        if intent == "smalltalk":
            system = SMALLTALK_SYSTEM
        elif intent == "order":
            if state.get("tool_results") or feedback:
                # verify retry, regenerate from stored tool results
                context = "\n\n".join(
                    f"tool {t['name']} returned:\n{t['result']}"
                    for t in state.get("tool_results", [])
                )
                system = RETRY_SYSTEM.format(
                    feedback=feedback or "ground every claim in the tool results",
                    context=context or "(no tool results this turn)",
                )
            else:
                # the graph sent the order here because the email is missing
                missing = []
                if not state.get("order_number"):
                    missing.append("the order number")
                if not state.get("email"):
                    missing.append("the email address used at checkout")
                system = ORDER_ASK_SYSTEM.format(missing=" and ".join(missing))
        else:
            context = _context_block(state.get("retrieved", []))
            system = GROUNDED_SYSTEM.format(context=context or "(no matching documents found)")
            if feedback:
                system += f"\n\nYour previous draft failed a grounding check: {feedback}\nFix that in the rewrite."

        response = await client.messages.create(
            model=model,
            max_tokens=1000,
            system=system,
            messages=state["messages"],
        )
        draft = "".join(b.text for b in response.content if b.type == "text").strip()
        usage = list(state.get("usage", []))
        usage.append(
            {
                "node": "respond",
                "model": model,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
        )
        return {"draft": draft, "usage": usage}

    return respond
