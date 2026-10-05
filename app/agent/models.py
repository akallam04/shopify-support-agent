"""Per-model request options, so every node calls a given model the same way."""

from typing import Any

ADAPTIVE_THINKING_MODELS = frozenset({"claude-sonnet-5-5"})
THINKING_MIN_MAX_TOKENS = 4000


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
