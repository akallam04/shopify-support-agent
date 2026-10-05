"""Per-model request options, so every node calls a given model the same way."""

from typing import Any

ADAPTIVE_THINKING_MODELS = frozenset({"claude-sonnet-5-5"})
THINKING_MIN_MAX_TOKENS = 4000


DIGEST_HEADER = "Earlier in this conversation (summarized):"


def system_blocks(stable: str, digest: str | None = None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}]
    if digest:
        blocks.append({"type": "text", "text": f"{DIGEST_HEADER}\n{digest}"})
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
