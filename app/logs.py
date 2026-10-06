"""One JSON line per request on stdout, which Lambda ships to CloudWatch."""

import json
import sys
from typing import Any

from app.costs import usage_cost
from app.privacy import masked


def request_cost(usage: list[dict[str, Any]]) -> float | None:
    try:
        return round(usage_cost(usage), 6)
    except KeyError:
        return None


def token_counts(usage: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: sum(int(u.get(k) or 0) for u in usage) for k in keys}


def log_event(**fields: Any) -> None:
    sys.stdout.write(json.dumps(masked(fields), separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()
