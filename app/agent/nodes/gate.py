"""Mutation gate: policy check, targeted reflection, and confirmation held in graph state."""

import json
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import (
    CONFIRM_CLASSIFIER_SCHEMA,
    CONFIRM_CLASSIFIER_SYSTEM,
    ADDRESS_ASK,
    CONDITIONAL_LEAD,
    CONDITIONAL_REASK,
    CONFIRMATION_TEMPLATE,
    DECLINED_RESPONSE,
    DEMO_RECEIPT_NOTE,
    GATE_INPUT_RESPONSE,
    HANDOFF_CLAIM,
    HANDOFF_CLAIM_DEMO,
    HANDOFF_DEMO,
    HANDOFF_EXCEPTION,
    HANDOFF_EXCEPTION_DEMO,
    HANDOFF_DEMO_FOLLOWUP,
    HANDOFF_FOLLOWUP,
    HANDOFF_RESPONSE,
    NOT_FOUND_HINT,
    POLICY_RULES,
    REASK_TEMPLATE,
    REASON_ASK,
    RECONFIRM_TEMPLATE,
    REFLECTION_SCHEMA,
    REFLECTION_SYSTEM,
    with_digest,
)
from app.agent.asks import claims_warranty, demands_exception
from app.agent.models import call_options, response_text
from app.agent.addresses import ungrounded_parts
from app.agent.reasons import condition_mentions, customer_texts, grounded, item_condition, stated_reason
from app.agent.state import AgentState
from app.agent.usage import usage_record
from app.config import Settings
from mcp_server.policy import condition_refusal
from mcp_server.tools import ToolInputError, idempotency_key

YES_RE = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|correct|go ahead|please do|do it)"
    r"([\s,.!]+(please|go ahead|do it|thanks|thank you|that is right|that's right))*[\s.!]*$"
)
NO_RE = re.compile(r"^(no|nope|nah|don't|do not|stop|wait|hold on|not yet|never mind|nevermind)\b")
CONDITIONAL_RE = re.compile(
    r"\b(only if|if and only if|unless|as long as|provided (that|you|i)|on (the )?condition|otherwise)\b"
    r"|^(yes|yeah|yep|sure|ok|okay)\b.*\bif\b",
    re.IGNORECASE,
)


ADDITION_RE = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay)\b[\s,.!]*(and|plus)\b|^(yes|yeah|yep|yup|sure|ok|okay)\b.*\b(also|as well)\b",
    re.IGNORECASE,
)
INSTEAD_RE = re.compile(r"\b(instead|rather|actually|different|not that one|wrong order)\b", re.IGNORECASE)
FAST_DECLINE_MAX_WORDS = 4


def classify_fast(text: str) -> str | None:
    normalized = " ".join(text.lower().split())
    if CONDITIONAL_RE.search(normalized):
        return "conditional"
    if ADDITION_RE.search(normalized) and not INSTEAD_RE.search(normalized):
        return "addition"
    if YES_RE.match(normalized):
        return "confirm"
    if NO_RE.match(normalized) and len(normalized.split()) <= FAST_DECLINE_MAX_WORDS:
        return "decline"
    return None


def _last_user_text(state: AgentState) -> str:
    return next((str(m["content"]) for m in reversed(state["messages"]) if m["role"] == "user"), "")


def _usage(state: AgentState, node: str, model: str, response: Any) -> list[dict[str, Any]]:
    return list(state.get("usage", [])) + [usage_record(node, model, response)]


MONEY_ACTIONS = frozenset({"cancel_order", "request_return"})


def result_message(result: dict[str, Any], sandbox: bool = False) -> str:
    if not result.get("ok"):
        reason = result.get("reason", "")
        return f"{reason} {NOT_FOUND_HINT}" if result.get("code") == "order_not_found" else reason
    parts = [result["message"]]
    parts += [result[k] for k in ("approval", "label", "fee", "amount", "refund") if result.get(k)]
    if sandbox and result.get("action") in MONEY_ACTIONS:
        parts.append(DEMO_RECEIPT_NOTE)
    text = " ".join(parts)
    return f"That is already done. {text}" if result.get("duplicate") else text


REASON_REQUIRED = frozenset({"request_return"})
REASON_OPTIONAL = frozenset({"cancel_order"})
REASON_CHECK = "The reason came from the customer"
def reason_from_customer(quote: str, state: AgentState) -> bool:
    return grounded(quote, customer_texts(state))


def customer_reason(args: dict[str, Any], state: AgentState) -> dict[str, Any] | None:
    texts = customer_texts(state)
    quote, code = str(args.get("reason_quote") or ""), str(args.get("reason") or "")
    stated = stated_reason(texts)
    if stated is not None:
        return {
            **args,
            "reason": code if code in stated.codes else stated.codes[0],
            "reason_quote": quote if grounded(quote, texts) else stated.phrase,
        }
    return args if grounded(quote, texts) else None


DECLINED_REFLECTION = {"verdict": "ask", "issues": ["the check declined"], "question": "Could you tell me exactly what you would like me to change?"}

CORRECTABLE_CODES = frozenset({"invalid_input", "item_not_found", "no_items", "invalid_reason"})
MAX_GATE_RETRIES = 1


def _retry(state: AgentState, name: str, reason: str) -> dict[str, Any] | None:
    retries = state.get("gate_retries", 0)
    if retries >= MAX_GATE_RETRIES:
        return None
    return {"gate_feedback": f"{name} was not run: {reason}", "gate_retries": retries + 1}


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
            **call_options(model, 300, {"type": "json_schema", "schema": REFLECTION_SCHEMA}, thinking=settings.model_thinking),
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        text = response_text(response)
        verdict = json.loads(text) if text else DECLINED_REFLECTION
        return verdict, _usage(state, "reflect", model, response)

    async def gate(state: AgentState) -> dict[str, Any]:
        candidate = state["candidate_action"]
        name, args = candidate["name"], candidate["args"]
        trace = list(state.get("gate_trace", []))
        reason_given = False
        condition = None
        if name in REASON_REQUIRED:
            found = item_condition(customer_texts(state))
            mentions = condition_mentions(customer_texts(state))
            shown = found or (mentions[0] if mentions else None)
            if shown is not None:
                trace.append({"step": "condition_check", "action": name, "condition": shown.condition, "stated": shown.stated, "quote": shown.quote[:200]})
            if found is not None:
                condition = found.condition
                args = {**args, "condition": condition}
        if name == "update_shipping_address":
            missing = ungrounded_parts(args, customer_texts(state))
            trace.append({"step": "address_check", "action": name, "missing": missing})
            if missing:
                return {"draft": ADDRESS_ASK, "system_draft": True, "candidate_action": None, "gate_trace": trace}
        if name in REASON_REQUIRED and not condition:
            resolved = customer_reason(args, state)
            trace.append({"step": "reason_check", "action": name, "found": resolved is not None})
            if resolved is None:
                return {"draft": REASON_ASK, "system_draft": True, "candidate_action": None, "gate_trace": trace}
            args, reason_given = resolved, True
        elif name in REASON_OPTIONAL and args.get("reason"):
            reason_given = reason_from_customer(str(args.get("reason_quote") or ""), state)
            trace.append({"step": "reason_check", "action": name, "found": reason_given, "optional": True})
            if not reason_given:
                args = {**args, "reason": "", "reason_quote": ""}
        try:
            prepared = tools.prepare(name, args)
        except ToolInputError as e:
            trace.append({"step": "policy", "action": name, "allowed": False, "code": "invalid_input", "detail": str(e)})
            retry = _retry(state, name, str(e))
            if retry is not None:
                return {**retry, "candidate_action": None, "gate_trace": trace}
            return {"draft": GATE_INPUT_RESPONSE, "system_draft": True, "candidate_action": None, "gate_trace": trace}

        tool_results = list(state.get("tool_results", [])) + [
            {"name": f"prepare:{name}", "args": args, "result": json.dumps(prepared.view())}
        ]
        trace.append({"step": "policy", "action": name, "allowed": prepared.allowed, "code": prepared.code})
        if not prepared.allowed:
            retry = _retry(state, name, prepared.reason) if prepared.code in CORRECTABLE_CODES else None
            if retry is not None:
                return {**retry, "candidate_action": None, "gate_trace": trace, "tool_results": tool_results}
            draft = result_message({"ok": False, "code": prepared.code, "reason": prepared.reason}, bool(state.get("sandbox")))
            return {"draft": draft, "system_draft": True, "candidate_action": None, "gate_trace": trace, "tool_results": tool_results}

        update: dict[str, Any] = {"candidate_action": None, "tool_results": tool_results}
        if settings.gate_reflection:
            verdict, usage = await reflect(state, prepared)
            update["usage"] = usage
            trace.append({"step": "reflection", "verdict": verdict["verdict"], "issues": verdict["issues"]})
            if verdict["verdict"] == "ask":
                return {**update, "draft": verdict["question"], "system_draft": False, "gate_trace": trace}

        key = idempotency_key(name, prepared.args)
        if settings.gate_confirmation:
            trace.append({"step": "confirmation_requested", "action": name, "key": key})
            checks = prepared.checks + ([REASON_CHECK] if reason_given else [])
            note = DEMO_RECEIPT_NOTE if state.get("sandbox") and name in MONEY_ACTIONS else ""
            pending = {
                "action": name,
                "args": args,
                "order_number": prepared.order_name,
                "summary": prepared.summary,
                "checks": checks,
                "key": key,
                "note": note,
            }
            return {
                **update,
                "system_draft": True,
                "draft": CONFIRMATION_TEMPLATE.format(summary=prepared.summary, note=f" {note}" if note else ""),
                "pending_action": pending,
                "gate_trace": trace,
            }

        result = tools.execute(name, args, key)
        trace.append({"step": "executed" if result.get("ok") else "refused", "action": name, "key": key})
        recorded = _record({**state, "tool_results": tool_results}, result, name, args)
        return {**update, **recorded, "draft": result_message(result, bool(state.get("sandbox"))), "system_draft": True, "gate_trace": trace}

    return gate


def make_confirm_node(client: AsyncAnthropic, model: str, thinking: bool = True):
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
                **call_options(model, 50, {"type": "json_schema", "schema": CONFIRM_CLASSIFIER_SCHEMA}, thinking=thinking),
                system=CONFIRM_CLASSIFIER_SYSTEM.format(summary=pending["summary"]),
                messages=[{"role": "user", "content": text}],
            )
            text = response_text(response)
            label = json.loads(text)["label"] if text else "unclear"
            source = "model"
            update["usage"] = _usage(state, "confirm", model, response)
        trace.append({"step": "confirmation", "label": label, "source": source, "action": pending["action"]})
        update.update({"confirmation": label, "gate_trace": trace, "system_draft": True})
        if label == "decline":
            update.update({"pending_action": None, "draft": DECLINED_RESPONSE})
        elif label == "conditional":
            update.update({"draft": CONDITIONAL_REASK.format(summary=pending["summary"]), "lead": CONDITIONAL_LEAD})
        elif label in ("question", "addition"):
            update["intent"] = ""
        elif label == "change":
            update.update({"pending_action": None, "intent": "", "corrected_action": pending["summary"]})
        elif label != "confirm":
            update["draft"] = REASK_TEMPLATE.format(summary=pending["summary"])
        return update

    return confirm


def make_execute_node(tools: Any):
    def execute(state: AgentState) -> dict[str, Any]:
        pending = state["pending_action"]
        result = tools.execute(pending["action"], pending["args"], pending["key"])
        trace = list(state.get("gate_trace", []))
        trace.append({"step": "executed" if result.get("ok") else "refused", "action": pending["action"], "key": pending["key"]})
        recorded = _record(state, result, pending["action"], pending["args"])
        return {**recorded, "draft": result_message(result, bool(state.get("sandbox"))), "system_draft": True, "pending_action": None, "gate_trace": trace}

    return execute


def make_handoff_node(tools: Any):
    def handoff(state: AgentState) -> dict[str, Any]:
        sandbox = bool(state.get("sandbox"))
        stated = item_condition(customer_texts(state))
        refusal = condition_refusal(stated.condition if stated else None)
        rule = f"{refusal.reason} " if refusal else ""
        if (state.get("handoff") or {}).get("recorded"):
            return {"draft": rule + (HANDOFF_DEMO_FOLLOWUP if sandbox else HANDOFF_FOLLOWUP), "system_draft": True}
        said = _last_user_text(state)
        claim = claims_warranty(said)
        if claim:
            reply = HANDOFF_CLAIM_DEMO if sandbox else HANDOFF_CLAIM
        elif demands_exception(said):
            reply = HANDOFF_EXCEPTION_DEMO if sandbox else HANDOFF_EXCEPTION
        else:
            reply = HANDOFF_DEMO if sandbox else HANDOFF_RESPONSE
        update: dict[str, Any] = {"draft": rule + reply, "system_draft": True}
        if "transfer_to_human" in tools.write_tool_names:
            args = {
                "summary": f"{'Warranty or damage claim' if claim else 'Customer asked for a person'}: {said[:300]}",
                "order_number": state.get("order_number") or "",
            }
            result = tools.execute("transfer_to_human", args)
            trace = list(state.get("gate_trace", []))
            trace.append({"step": "handoff_recorded", "ok": bool(result.get("ok"))})
            update.update({
                "gate_trace": trace,
                "executed_actions": list(state.get("executed_actions", [])) + [result],
                "handoff": {"recorded": bool(result.get("ok")), "notified": False},
            })
        return update

    return handoff


SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
GO_AHEAD_RE = re.compile(r"\b(go ahead|proceed)\b.*\?\s*$|\breply (yes|no)\b", re.IGNORECASE)


def without_yes_question(text: str) -> str:
    sentences = SENTENCE_END_RE.split(text.strip())
    while len(sentences) > 1 and GO_AHEAD_RE.search(sentences[-1]):
        sentences.pop()
    return " ".join(sentences)


def reconfirm_node(state: AgentState) -> dict[str, Any]:
    pending = state["pending_action"]
    answer = without_yes_question(state["response"])
    return {"lead": answer, "response": f"{answer}\n\n{RECONFIRM_TEMPLATE.format(summary=pending['summary'])}"}
