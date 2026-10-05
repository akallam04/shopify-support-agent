"""Model prices and the cost of recorded token usage."""

from typing import Any

# usd per million tokens (input, output), sticker prices checked 2026-10-04
PRICES_PER_MTOK = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = {"claude-opus-5-5": 0.05}
DEFAULT_CACHE_READ_MULTIPLIER = 0.10


def usage_cost(usage: list[dict[str, Any]]) -> float:
    total = 0.0
    for u in usage:
        model = u.get("model", "")
        if model not in PRICES_PER_MTOK:
            raise KeyError(f"no price for model {model!r}, add it to PRICES_PER_MTOK")
        price_in, price_out = PRICES_PER_MTOK[model]
        total += u["input_tokens"] / 1e6 * price_in
        total += u.get("cache_creation_input_tokens", 0) / 1e6 * price_in * CACHE_WRITE_MULTIPLIER
        read_multiplier = CACHE_READ_MULTIPLIER.get(model, DEFAULT_CACHE_READ_MULTIPLIER)
        total += u.get("cache_read_input_tokens", 0) / 1e6 * price_in * read_multiplier
        total += u["output_tokens"] / 1e6 * price_out
    return total
