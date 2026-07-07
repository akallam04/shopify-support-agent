"""Grading logic: deterministic checks first, an LLM judge only where text quality needs judgment."""

import json
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import (
    HANDOFF_RESPONSE,
    INJECTION_RESPONSE,
    OUT_OF_SCOPE_RESPONSE,
)

REFUSAL_TEMPLATES = {INJECTION_RESPONSE, OUT_OF_SCOPE_RESPONSE, HANDOFF_RESPONSE}

JUDGE_SYSTEM = (
    "You are grading a customer support agent for Aurora Outfitters, an online "
    "outdoor gear store. Grade ONLY against the criteria given, not your own "
    "preferences. pass is true only if the response satisfies every part of the "
    "criteria."
)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"pass": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["pass", "reason"],
    "additionalProperties": False,
}


def expected_intents(expect: dict[str, Any]) -> list[str]:
    intent = expect.get("intent")
    if intent is None:
        return []
    return [intent] if isinstance(intent, str) else list(intent)


def check_intent(expect: dict[str, Any], actual: str) -> bool | None:
    allowed = expected_intents(expect)
    if not allowed:
        return None
    return actual in allowed


def check_retrieval(expect: dict[str, Any], retrieved: list[dict[str, Any]]) -> bool | None:
    wanted = expect.get("retrieval_contains")
    if not wanted:
        return None
    keys = " ".join(
        f"{d['id']} {d['metadata'].get('handle', '')}" for d in retrieved
    )
    return all(w in keys for w in wanted)


def check_contains(expect: dict[str, Any], response: str) -> bool | None:
    wanted = expect.get("answer_contains")
    banned = expect.get("answer_not_contains")
    if not wanted and not banned:
        return None
    low = response.lower()
    if wanted and not all(w.lower() in low for w in wanted):
        return False
    if banned and any(b.lower() in low for b in banned):
        return False
    return True


def check_tools(expect: dict[str, Any], tool_results: list[dict[str, Any]]) -> bool | None:
    wants = expect.get("tools_called")
    no_tools = expect.get("no_tools")
    expect_found = expect.get("expect_found")
    if wants is None and not no_tools and expect_found is None:
        return None
    if no_tools:
        return len(tool_results) == 0
    called = {t["name"] for t in tool_results}
    if wants and not set(wants) <= called:
        return False
    if any('"error"' in t["result"] for t in tool_results):
        return False
    if expect_found is not None:
        founds = []
        for t in tool_results:
            try:
                founds.append(json.loads(t["result"]).get("found"))
            except json.JSONDecodeError:
                pass
        if expect_found not in founds:
            return False
    return True


def matches_refusal_template(response: str) -> bool:
    return response.strip() in REFUSAL_TEMPLATES


async def run_judge(
    client: AsyncAnthropic,
    model: str,
    messages: list[dict[str, Any]],
    response: str,
    criteria: str,
) -> tuple[bool, str, dict[str, int]]:
    transcript = "\n".join(
        f"{'customer' if m['role'] == 'user' else 'agent'}: {m['content']}"
        for m in messages
    )
    try:
        # sonnet 5 rejects non-default sampling params, so no temperature here
        result = await client.messages.create(
            model=model,
            max_tokens=500,
            system=JUDGE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Conversation so far:\n{transcript}\n\n"
                        f"Agent response to grade:\n{response}\n\n"
                        f"Criteria:\n{criteria}"
                    ),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        )
        text = next((b.text for b in result.content if b.type == "text"), "")
        parsed = json.loads(text)
        usage = {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
        }
        return bool(parsed["pass"]), parsed["reason"], usage
    except Exception as e:  # noqa: BLE001, a judge hiccup must not sink the run
        return False, f"JUDGE_ERROR {type(e).__name__}: {e}", {"input_tokens": 0, "output_tokens": 0}
