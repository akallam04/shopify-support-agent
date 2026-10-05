"""The simulated customer: an OpenAI-compatible chat model playing a scenario, with stop signals."""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from evals.sim.schema import UserScenario

STOP, TRANSFER, OUT_OF_SCOPE = "###STOP###", "###TRANSFER###", "###OUT-OF-SCOPE###"
SIGNALS = (STOP, TRANSFER, OUT_OF_SCOPE)
OPENING = "(The support chat just opened. Write your first message to the store's support assistant.)"
THROTTLE_MARKERS = ("throttling", "rate limit", "ratequota")
QUOTA_MARKERS = ("quota", "freetier", "free tier", "arrearage", "allocation", "insufficient balance")
CITATION_RE = re.compile(r"\s*\[[a-z0-9][a-z0-9-]*\]")

SYSTEM_TEMPLATE = """You are role-playing a customer who is chatting with the online support assistant of Aurora Outfitters, an outdoor gear store. You are the customer, never the assistant.

Who you are: {persona}

Why you are contacting support: {reason_for_call}

What you know: {known_info}

What you do not know: {unknown_info}

How to behave: {task_instructions}

Rules:
- Write only your next chat message as the customer, usually one or two short sentences, in plain text.
- Share a detail only when the assistant asks for it or when it is needed to move forward. Do not give everything at once.
- Use only the facts above. If the assistant asks for something you do not know, say you do not have it. Never invent order numbers, email addresses, or other details.
- When you give an order number, email address, or street address, copy it exactly as written above.
- Follow the behavior instructions exactly, including when to agree to or refuse a proposed change.
- When your goal is done, or the assistant has clearly said it cannot do more, reply with exactly {stop} and nothing else.
- If the assistant hands your request over to a person or to the support team instead of solving it here, reply with exactly {transfer} and nothing else.
- If the conversation reaches something your instructions do not cover, reply with exactly {out_of_scope} and nothing else."""


class SimulatorQuotaError(RuntimeError):
    pass


class SimulatorError(RuntimeError):
    pass


@dataclass
class SimTokens:
    prompt: int = 0
    completion: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


@dataclass
class UserTurn:
    text: str
    signal: str | None


@dataclass
class UserSimulator:
    scenario: UserScenario
    model: str
    base_url: str
    api_key: str
    temperature: float = 0.7
    tokens: SimTokens = field(default_factory=SimTokens)
    retries: int = 3

    def system_prompt(self) -> str:
        i = self.scenario.instructions
        return SYSTEM_TEMPLATE.format(
            persona=self.scenario.persona,
            reason_for_call=i.reason_for_call,
            known_info=i.known_info,
            unknown_info=i.unknown_info or "Nothing in particular.",
            task_instructions=i.task_instructions,
            stop=STOP,
            transfer=TRANSFER,
            out_of_scope=OUT_OF_SCOPE,
        )

    def _messages(self, transcript: list[dict[str, str]]) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self.system_prompt()}, {"role": "user", "content": OPENING}]
        for m in transcript:
            if m["role"] == "user":
                messages.append({"role": "assistant", "content": m["content"]})
            else:
                messages.append({"role": "user", "content": CITATION_RE.sub("", m["content"])})
        return messages

    async def next_message(self, client: httpx.AsyncClient, transcript: list[dict[str, str]]) -> UserTurn:
        body = {
            "model": self.model,
            "messages": self._messages(transcript),
            "temperature": self.temperature,
            "max_tokens": 300,
            "enable_thinking": False,
        }
        data = await self._post(client, body)
        usage = data.get("usage") or {}
        self.tokens.prompt += usage.get("prompt_tokens", 0)
        self.tokens.completion += usage.get("completion_tokens", 0)
        self.tokens.calls += 1
        text = (data["choices"][0]["message"].get("content") or "").strip()
        signal = next((s for s in SIGNALS if s in text), None)
        if signal:
            text = text.replace(signal, "").strip()
        if not text and not signal:
            raise SimulatorError("the simulator returned an empty message")
        return UserTurn(text=text, signal=signal)

    async def _post(self, client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
        last = ""
        for attempt in range(self.retries + 1):
            try:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                    timeout=60,
                )
            except httpx.HTTPError as e:
                last = type(e).__name__
            else:
                if r.status_code == 200:
                    return r.json()
                detail = r.text[:300].lower()
                throttled = r.status_code == 429 and any(m in detail for m in THROTTLE_MARKERS)
                if not throttled and any(m in detail for m in QUOTA_MARKERS):
                    raise SimulatorQuotaError(f"simulator model {self.model} is out of quota: HTTP {r.status_code}")
                if r.status_code < 500 and r.status_code != 429:
                    raise SimulatorError(f"simulator request rejected: HTTP {r.status_code} {r.text[:200]}")
                last = f"HTTP {r.status_code}"
            await asyncio.sleep(2 ** attempt)
        raise SimulatorError(f"simulator unavailable after retries: {last}")
