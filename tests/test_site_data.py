import json
import re
from pathlib import Path
from typing import Any

import pytest

FRONTEND = Path("frontend")
PAGE = (FRONTEND / "how-it-works.html").read_text()
RESULTS = json.loads((FRONTEND / "data" / "results.json").read_text())
EXAMPLES = json.loads((FRONTEND / "data" / "transcripts.json").read_text())

NUM_RE = re.compile(r'data-num="([^"]+)"(?: data-fmt="([^"]+)")?>([^<]*)<')
INTERVAL_RE = re.compile(r'data-interval="([^"]+)">([^<]*)<')


def lookup(path: str) -> Any:
    value: Any = RESULTS
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def formatted(value: Any, fmt: str | None) -> str:
    if fmt == "p3":
        return f"{value:.3f}"
    if fmt == "usd4":
        return f"${value:.4f}"
    if fmt == "s2":
        return f"{value:.2f}s"
    assert isinstance(value, int), f"{value!r} needs a format"
    return str(value)


@pytest.mark.parametrize(("path", "fmt", "shown"), NUM_RE.findall(PAGE))
def test_every_number_on_the_page_matches_the_export(path: str, fmt: str, shown: str) -> None:
    assert shown == formatted(lookup(path), fmt or None)


@pytest.mark.parametrize(("path", "shown"), INTERVAL_RE.findall(PAGE))
def test_every_interval_on_the_page_matches_the_export(path: str, shown: str) -> None:
    d = lookup(path)
    assert shown == f"{d['mean']:+.3f} [{d['low']:+.3f}, {d['high']:+.3f}]"


def test_the_page_tags_its_numbers() -> None:
    assert len(NUM_RE.findall(PAGE)) > 60 and len(INTERVAL_RE.findall(PAGE)) >= 6


def test_side_by_side_examples_exist_and_differ_only_in_the_gate() -> None:
    ids = {e["id"]: e for e in EXAMPLES}
    for key in re.findall(r'data-example="([^"]+)"', PAGE):
        assert key in ids
    off, on = ids["cancel-gate-off"], ids["cancel-gate-on"]
    assert off["task_id"] == on["task_id"] and (off["gate"], on["gate"]) == ("off", "on")
    assert off["resolved"] and not off["resolved_safely"] and on["resolved_safely"]
