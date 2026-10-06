"""Masks email addresses before they reach the screen or the logs."""

import re
from typing import Any

EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mask_email(text: str) -> str:
    return EMAIL_RE.sub(lambda m: f"{m.group(1)}\u2022\u2022\u2022@{m.group(2)}", text)


def masked(value: Any) -> Any:
    if isinstance(value, str):
        return mask_email(value)
    if isinstance(value, dict):
        return {k: masked(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [masked(v) for v in value]
    return value
