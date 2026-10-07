"""Per-model request options, so every node calls a given model the same way."""

from typing import Any

ADAPTIVE_THINKING_MODELS = frozenset({"claude-sonnet-5-5", "claude-haiku-5-5"})
THINKING_MIN_MAX_TOKENS = 4000


DIGEST_HEADER = "Earlier in this conversation (summarized):"


def system_blocks(stable: str, digest: str | None = None, extra: str | None = None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}]
    if digest:
        blocks.append({"type": "text", "text": f"{DIGEST_HEADER}\n{digest}"})
    if extra:
        blocks.append({"type": "text", "text": extra})
    return blocks


def call_options(model: str, max_tokens: int, output_format: dict[str, Any] | None = None) -> dict[str, Any]:
    output_config: dict[str, Any] = {}
    if output_format is not None:
        output_config["format"] = output_format
    if model in ADAPTIVE_THINKING_MODELS:
        output_config["effort"] = "low"
        max_tokens = max(max_tokens, THINKING_MIN_MAX_TOKENS)
    options: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
    if output_config:
        options["output_config"] = output_config
    return options


def response_text(response: Any) -> str | None:
    if getattr(response, "stop_reason", None) == "refusal":
        return None
    texts = [b.text for b in response.content if b.type == "text"]
    return "".join(texts) if texts else None


def carries_thinking(messages: list[Any]) -> bool:
    return any(
        getattr(block, "type", None) in ("thinking", "redacted_thinking")
        for m in messages
        if m["role"] == "assistant" and isinstance(m["content"], list)
        for block in m["content"]
    )

