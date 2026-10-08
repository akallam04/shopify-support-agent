"""One usage record per model call, including prompt-cache writes and reads and thinking tokens."""

from typing import Any


def usage_record(node: str, model: str, response: Any) -> dict[str, Any]:
    usage = response.usage
    return {
        "node": node,
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None) or 0,
        "thinking_tokens": getattr(getattr(usage, "output_tokens_details", None), "thinking_tokens", None) or 0,
    }
