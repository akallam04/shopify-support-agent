"""Address parts must come from the customer's own words, never filled in from what the model knows."""

import re
from typing import Any

from mcp_server.policy import PROVINCE_NAMES

WORD_RE = re.compile(r"[a-z0-9]+")
STREET_WORDS = {
    "street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr", "lane": "ln",
    "court": "ct", "place": "pl", "apartment": "apt", "suite": "ste", "north": "n", "south": "s", "east": "e", "west": "w",
}
CHECKED_FIELDS = ("address1", "address2", "city", "province_code", "zip")


def _words(text: str) -> list[str]:
    return [STREET_WORDS.get(w, w) for w in WORD_RE.findall(text.lower())]


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def ungrounded_parts(args: dict[str, Any], customer_texts: list[str]) -> list[str]:
    said = " ".join(customer_texts)
    words = set(_words(said))
    missing = []
    for field in CHECKED_FIELDS:
        value = str(args.get(field) or "").strip()
        if not value:
            continue
        if field == "province_code":
            name = PROVINCE_NAMES.get(value.upper(), "")
            found = value.lower() in words or (bool(name) and _compact(name) in _compact(said))
        elif field == "zip":
            found = _compact(value) in _compact(said)
        else:
            found = set(_words(value)) <= words
        if not found:
            missing.append(field)
    return missing
