"""Numbers a reply states as facts, normalized so each one can be matched against its sources."""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any

from mcp_server.clock import STORE_TIMEZONE

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30, "sixty": 60, "ninety": 90,
}
UNITS = {"minute": ("minute", 1), "min": ("minute", 1), "hour": ("minute", 60), "hr": ("minute", 60),
         "day": ("day", 1), "week": ("day", 7), "month": ("month", 1), "year": ("month", 12)}

MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
NUM = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"
DATE_MD_RE = re.compile(rf"\b{MONTH} (\d{{1,2}})(?:st|nd|rd|th)?\b(?:,? (\d{{4}}))?", re.I)
DATE_DM_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)? (?:of )?{MONTH}(?![a-z])(?:,? (\d{{4}}))?", re.I)
DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(T\d{2}:\d{2}(?::\d{2})?Z?)?")
DATE_SLASH_RE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])")
MONEY_RE = re.compile(r"(?:US)?\$\s?(\d[\d,]*(?:\.\d{1,2})?)|\b(\d[\d,]*(?:\.\d{1,2})?)\s?(?:USD|CAD|dollars?)\b", re.I)
DECIMAL_RE = re.compile(r"(?<![\d.])(\d[\d,]*\.\d{2})(?!\d)")
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s?([ap])\.?m\b\.?|\b(noon|midday)\b", re.I)
COMPOSITE_RE = re.compile(r"\b(\d+) (?:hours?|hrs?),? (?:and )?(\d+) (?:minutes?|mins?)\b", re.I)
DURATION_RE = re.compile(
    rf"\b{NUM}(?:\s?(?:-|to|or|and|through)\s?{NUM})?[\s-]+(?:(?:more|extra|additional|remaining|full|whole)[\s-]+)?"
    r"(?:(?:business|calendar|working)[\s-]+)?(minute|min|hour|hr|day|week|month|year)s?\b",
    re.I,
)
PERCENT_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.I)
STOCK_RE = re.compile(r"\b(\d+)\s(?:units?\s|pieces?\s)?(?:left|available|in stock|remaining)\b|\bonly (\d+)\b", re.I)
STOCK_FACT_RE = re.compile(r"\"(?:quantity|inventory_quantity)\":\s?(\d+)|\((\d+) available\)")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
ATTRIBUTION_RE = re.compile(
    r"\byou(?:'ve| have)? (said|mentioned|wrote|told me|noted|stated|asked (about|for)|requested|quoted|are asking|were quoted|saw|want|would like)\b"
    r"|\byou'?re asking\b|\byou'?d like\b|\byour (message|note|question|request)\b|\bas you (said|mentioned|noted)\b",
    re.I,
)

Fact = tuple[str, Any]


@dataclass(frozen=True)
class Claim:
    kind: str
    value: Any
    text: str
    start: int


def _number(word: str) -> int:
    return int(word) if word.isdigit() else NUMBER_WORDS[word.lower()]


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    year = int(raw)
    return year + 2000 if year < 100 else year


def _free(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return all(end <= s or start >= e for s, e in spans)


def claims_in(text: str) -> list[Claim]:
    found: list[Claim] = []
    taken: list[tuple[int, int]] = []

    def add(kind: str, value: Any, match: re.Match[str]) -> None:
        if value is not None and _free(taken, match.start(), match.end()):
            found.append(Claim(kind, value, match.group(0), match.start()))
            taken.append((match.start(), match.end()))

    for m in DATE_ISO_RE.finditer(text):
        add("date", (int(m.group(1)), int(m.group(2)), int(m.group(3))), m)
    for m in DATE_MD_RE.finditer(text):
        add("date", (_year(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))), m)
    for m in DATE_DM_RE.finditer(text):
        add("date", (_year(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))), m)
    for m in DATE_SLASH_RE.finditer(text):
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            add("date", (_year(m.group(3)), month, day), m)
    for m in MONEY_RE.finditer(text):
        add("money", _decimal(m.group(1) or m.group(2)), m)
    for m in TIME_RE.finditer(text):
        if m.group(4):
            add("time", (12, 0), m)
            continue
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if 1 <= hour <= 12:
            add("time", (hour % 12 + (12 if m.group(3).lower() == "p" else 0), minute), m)
    for m in COMPOSITE_RE.finditer(text):
        add("duration", ("minute", int(m.group(1)) * 60 + int(m.group(2))), m)
    for m in DURATION_RE.finditer(text):
        unit, scale = UNITS[m.group(3).lower()]
        for raw in (m.group(1), m.group(2)):
            if raw and _free(taken, m.start(), m.end()):
                found.append(Claim("duration", (unit, _number(raw) * scale), m.group(0), m.start()))
        taken.append((m.start(), m.end()))
    for m in PERCENT_RE.finditer(text):
        add("percent", _decimal(m.group(1)), m)
    for m in STOCK_RE.finditer(text):
        add("stock", int(m.group(1) or m.group(2)), m)
    return sorted(found, key=lambda c: c.start)


@lru_cache(maxsize=256)
def facts_in(text: str) -> frozenset[Fact]:
    facts = {(c.kind, c.value) for c in claims_in(text)}
    facts |= {("money", _decimal(m.group(1))) for m in DECIMAL_RE.finditer(text)}
    facts |= {("stock", int(m.group(1) or m.group(2))) for m in STOCK_FACT_RE.finditer(text)}
    for m in DATE_ISO_RE.finditer(text):
        if m.group(4):
            local = datetime.fromisoformat(m.group(0).replace("Z", "+00:00")).astimezone(STORE_TIMEZONE)
            facts.add(("date", (local.year, local.month, local.day)))
    return frozenset(facts)


def supported(claim: Claim, facts: frozenset[Fact]) -> bool:
    if claim.kind != "date":
        return (claim.kind, claim.value) in facts
    year, month, day = claim.value
    return any(
        kind == "date" and value[1:] == (month, day) and (year is None or value[0] is None or value[0] == year)
        for kind, value in facts
    )


def sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_RE.split(text) if s.strip()]


ELAPSED_BEFORE_RE = re.compile(r"\b(after|for|at)\s+(only\s+|just\s+|about\s+)?$", re.I)
ELAPSED_AFTER_RE = re.compile(r"^\s*(ago|old)\b", re.I)


def attributed(sentence: str, claim: Claim | None = None) -> bool:
    if ATTRIBUTION_RE.search(sentence):
        return True
    if claim is None or claim.kind != "duration":
        return False
    at = sentence.find(claim.text)
    return at >= 0 and bool(ELAPSED_BEFORE_RE.search(sentence[:at]) or ELAPSED_AFTER_RE.search(sentence[at + len(claim.text):]))
