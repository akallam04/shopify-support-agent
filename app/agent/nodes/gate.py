"""Mutation gate: policy check, targeted reflection, and confirmation held in graph state."""

import json
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import (
    CONFIRM_CLASSIFIER_SCHEMA,
    CONFIRM_CLASSIFIER_SYSTEM,
    CONFIRMATION_TEMPLATE,
    DECLINED_RESPONSE,
    GATE_INPUT_RESPONSE,
    HANDOFF_RESPONSE,
    NOT_FOUND_HINT,
    POLICY_RULES,
    REASK_TEMPLATE,
    REFLECTION_SCHEMA,
    REFLECTION_SYSTEM,
    with_digest,
)
from app.agent.models import call_options
from app.agent.state import AgentState
from app.agent.usage import usage_record
from app.config import Settings
from mcp_server.tools import ToolInputError, idempotency_key

YES_RE = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|correct|go ahead|please do|do it)"
    r"([\s,.!]+(please|go ahead|do it|thanks|thank you|that is right|that's right))*[\s.!]*$"
)
NO_RE = re.compile(r"^(no|nope|nah|don't|do not|stop|wait|hold on|not yet|never mind|nevermind)\b")


def classify_fast(text: str) -> str | None:
    normalized = " ".join(text.lower().split())
    if YES_RE.match(normalized):
        return "confirm"
    if NO_RE.match(normalized):
        return "decline"
    return None


def _last_user_text(state: AgentState) -> str:
    return next((str(m["content"]) for m in reversed(state["messages"]) if m["role"] == "user"), "")


def _usage(state: AgentState, node: str, model: str, response: Any) -> list[dict[str, Any]]:
    return list(state.get("usage", [])) + [usage_record(node, model, response)]


def result_message(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        reason = result.get("reason", "")
        return f"{reason} {NOT_FOUND_HINT}" if result.get("code") == "order_not_found" else reason
    parts = [result["message"]]
    parts += [result[k] for k in ("approval", "label", "fee", "refund") if result.get(k)]
    text = " ".join(parts)
    return f"That is already done. {text}" if result.get("duplicate") else text


def _record(state: AgentState, result: dict[str, Any], name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "executed_actions": list(state.get("executed_actions", [])) + [result],
        "tool_results": list(state.get("tool_results", []))
        + [{"name": name, "args": args, "result": json.dumps(result)}],
    }


def make_gate_node(client: AsyncAnthropic, model: str, tools: Any, settings: Settings):
    async def reflect(state: AgentState, prepared: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        request = "\n".join(
            f"customer: {m['content']}" for m in state["messages"] if m["role"] == "user"
        )[-2000:]
        content = (
            f"Customer messages:\n{request}\n\n"
            f"Order facts:\n{json.dumps(prepared.facts, indent=1)}\n\n"
            f"Proposed action: {prepared.action}\n{json.dumps(prepared.args, indent=1)}"
        )
        system = with_digest(REFLECTION_SYSTEM.format(rule=POLICY_RULES[prepared.action]), state.get("context_digest"))
        response = await client.messages.create(
            **call_options(model, 300, {"type": "json_schema", "schema": REFLECTION_SCHEMA}),
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        verdict = json.loads(next(b.text for b in response.content if b.type == "text"))
        return verdict, _usage(state, "reflect", model, response)

    async def gate(state: AgentState) -> dict[str, Any]:
        candidate = state["candidate_action"]
        name, args = candidate["name"], candidate["args"]
        trace = list(state.get("gate_trace", []))
        try:
            prepared = tools.prepare(name, args)
        except ToolInputError as e:
            trace.append({"step": "policy", "action": name, "allowed": False, "code": "invalid_input", "detail": str(e)})
            return {"draft": GATE_INPUT_RESPONSE, "candidate_action": None, "gate_trace": trace}

        tool_results = list(state.get("tool_results", [])) + [
            {"name": f"prepare:{name}", "args": args, "result": json.dumps(prepared.view())}
        ]
        trace.append({"step": "policy", "action": name, "allowed": prepared.allowed, "code": prepared.code})
        if not prepared.allowed:
            draft = result_message({"ok": False, "code": prepared.code, "reason": prepared.reason})
            return {"draft": draft, "candidate_action": None, "gate_trace": trace, "tool_results": tool_results}

        update: dict[str, Any] = {"candidate_action": None, "tool_results": tool_results}
        if settings.gate_reflection:
            verdict, usage = await reflect(state, prepared)
            update["usage"] = usage
            trace.append({"step": "reflection", "verdict": verdict["verdict"], "issues": verdict["issues"]})
            if verdict["verdict"] == "ask":
                return {**update, "draft": verdict["question"], "gate_trace": trace}

        key = idempotency_key(name, prepared.args)
        if settings.gate_confirmation:
            trace.append({"step": "confirmation_requested", "action": name, "key": key})
            pending = {
                "action": name,
                "args": args,
                "order_number": prepared.order_name,
                "summary": prepared.summary,
                "key": key,
            }
            return {
                **update,
                "draft": CONFIRMATION_TEMPLATE.format(summary=prepared.summary),
                "pending_action": pending,
                "gate_trace": trace,
            }

        result = tools.execute(name, args, key)
        trace.append({"step": "executed" if result.get("ok") else "refused", "action": name, "key": key})
        recorded = _record({**state, "tool_results": tool_results}, result, name, args)
        return {**update, **recorded, "draft": result_message(result), "gate_trace": trace}

    return gate


def make_confirm_node(client: AsyncAnthropic, model: str):
    async def confirm(state: AgentState) -> dict[str, Any]:
        pending = state["pending_action"]
        text = _last_user_text(state)
        trace = list(state.get("gate_trace", []))
        update: dict[str, Any] = {
            "intent": "order",
            "tool_results": list(state.get("tool_results", []))
            + [{"name": "pending_action", "args": {}, "result": json.dumps(pending)}],
        }
        label = classify_fast(text)
        source = "pattern"
        if label is None:
            response = await client.messages.create(
                **call_options(model, 50, {"type": "json_schema", "schema": CONFIRM_CLASSIFIER_SCHEMA}),
                system=CONFIRM_CLASSIFIER_SYSTEM.format(summary=pending["summary"]),
                messages=[{"role": "user", "content": text}],
            )
            label = json.loads(next(b.text for b in response.content if b.type == "text"))["label"]
            source = "model"
            update["usage"] = _usage(state, "confirm", model, response)
        trace.append({"step": "confirmation", "label": label, "source": source, "action": pending["action"]})
        update.update({"confirmation": label, "gate_trace": trace})
        if label == "decline":
            update.update({"pending_action": None, "draft": DECLINED_RESPONSE})
        elif label == "unclear":
            update["draft"] = REASK_TEMPLATE.format(summary=pending["summary"])
        elif label == "change":
            update.update({"pending_action": None, "intent": ""})
        return update

    return confirm


def make_execute_node(tools: Any):
    def execute(state: AgentState) -> dict[str, Any]:
        pending = state["pending_action"]
        result = tools.execute(pending["action"], pending["args"], pending["key"])
        trace = list(state.get("gate_trace", []))
        trace.append({"step": "executed" if result.get("ok") else "refused", "action": pending["action"], "key": pending["key"]})
        recorded = _record(state, result, pending["action"], pending["args"])
        return {**recorded, "draft": result_message(result), "pending_action": None, "gate_trace": trace}

    return execute


def make_handoff_node(tools: Any):
    def handoff(state: AgentState) -> dict[str, Any]:
        update: dict[str, Any] = {"draft": HANDOFF_RESPONSE}
        if "transfer_to_human" in tools.write_tool_names:
            args = {
                "summary": f"Customer asked for a person: {_last_user_text(state)[:300]}",
                "order_number": state.get("order_number") or "",
            }
            result = tools.execute("transfer_to_human", args)
            trace = list(state.get("gate_trace", []))
            trace.append({"step": "handoff_recorded", "ok": bool(result.get("ok"))})
            update.update({"gate_trace": trace, "executed_actions": list(state.get("executed_actions", [])) + [result]})
        return update

    return handoff
