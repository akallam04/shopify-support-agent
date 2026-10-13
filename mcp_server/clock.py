"""Clocks: the real one for live traffic, a frozen one so simulations never go stale."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FrozenClock:
    at: datetime

    def now(self) -> datetime:
        return self.at


STORE_TIMEZONE = ZoneInfo("America/Denver")
STORE_TIMEZONE_NAME = "Mountain Time"


def store_date(moment: datetime) -> str:
    return f"{moment.astimezone(STORE_TIMEZONE):%B %-d, %Y}"


def store_time(moment: datetime) -> str:
    local = moment.astimezone(STORE_TIMEZONE)
    return f"{local:%-I:%M} {'am' if local.hour < 12 else 'pm'}"


def days_until(now: datetime, moment: datetime) -> int:
    return (moment.astimezone(STORE_TIMEZONE).date() - now.astimezone(STORE_TIMEZONE).date()).days


def format_instant(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must carry a timezone: {value}")
    return parsed.astimezone(UTC)


DURATION_RE = re.compile(r"^(\d+)([dhm])$")


def parse_ago(value: str) -> timedelta:
    match = DURATION_RE.match(value)
    if not match:
        raise ValueError(f"durations look like 45d, 2h or 30m, got {value!r}")
    amount, unit = int(match.group(1)), match.group(2)
    return {"d": timedelta(days=amount), "h": timedelta(hours=amount), "m": timedelta(minutes=amount)}[unit]
