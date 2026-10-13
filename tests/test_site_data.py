import json
import re
from pathlib import Path
from typing import Any

import pytest

FRONTEND = Path("frontend")
PAGE = (FRONTEND / "how-it-works.html").read_text()
HOME = (FRONTEND / "index.html").read_text()
SUMMARY = json.loads((FRONTEND / "data" / "summary.json").read_text())
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
    if fmt == "s1":
        return f"{value:.1f}s"
    if fmt == "text":
        return str(value)
    assert isinstance(value, int), f"{value!r} needs a format"
    return str(value)


@pytest.mark.parametrize(("path", "fmt", "shown"), NUM_RE.findall(PAGE) + NUM_RE.findall(HOME))
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


def test_the_store_page_shows_the_catalog_and_test_orders_as_static_html() -> None:
    from scripts.export_site_data import products, static_index

    assert HOME == static_index()
    assert HOME.count('class="product"') == len(products()) == RESULTS["catalog"]["products"]
    titles = re.findall(r'class="product__name">([^<]+)<', HOME)
    catalog = {json.loads(line)["metadata"]["title"] for line in Path("data/catalog/products.jsonl").read_text().splitlines()}
    assert titles and set(titles) <= catalog


def test_the_three_guarantees_read_the_same_on_the_page_in_the_readme_and_in_the_tour() -> None:
    readme = Path("README.md").read_text()
    assert SUMMARY["what"] in PAGE and SUMMARY["what"] in readme
    for g in SUMMARY["guarantees"]:
        assert f"<b>{g['title']}.</b> {g['text']}" in PAGE
        assert g["title"] in readme and g["text"] in readme and g["code"] in readme
    assert "summary.guarantees" in (FRONTEND / "tour.js").read_text()


def test_the_tour_replays_a_saved_conversation_through_the_card_and_a_yes() -> None:
    tour = json.loads((FRONTEND / "data" / "tour.json").read_text())
    first, second = tour["turns"]
    assert first["response"]["pending"]["action"] in ("request_return", "cancel_order")
    assert second["user"] == "Yes" and any(g["step"] == "executed" for g in second["response"]["trace"]["gate"])
    assert all(t["response"]["session_state"] is None for t in tour["turns"]) and tour["recorded_on"]


def test_the_readme_headline_and_latency_match_the_export() -> None:
    readme = Path("README.md").read_text()
    on, off = RESULTS["headline"]["on"], RESULTS["headline"]["off"]
    n = on["conversations"]
    assert f"| {off['unsafe_conversations']} of {n} | {on['unsafe_conversations']} of {n} |" in readme
    assert f"| {off['resolved']} of {n} | {on['resolved']} of {n} |" in readme
    for k in (0, 3):
        assert f"| {off['pass_hat_k_safe'][k]:.3f} | {on['pass_hat_k_safe'][k]:.3f} |" in readme
    lat = RESULTS["latency"]
    assert f"took {lat['model_min_s']} to {lat['model_max_s']} seconds (median {lat['model_median_s']}s over {lat['model_replies']} replies)" in readme
    assert lat["measured_on"] in readme


def test_the_readme_diagram_shows_the_fixed_reply_and_the_path_where_nothing_runs() -> None:
    diagram = Path("README.md").read_text().split("```mermaid", 1)[1].split("```", 1)[0]
    assert "injection pattern| FIXED[fixed reply, no model call]" in diagram
    assert "no, unclear, or conditional: nothing runs" in diagram
    assert "reflection off by default" in diagram
    assert "question, or yes and also| ROUTE" in diagram


def test_the_readme_live_box_matches_the_saved_release_manifest_and_the_tour_ran_on_it() -> None:
    from scripts.live_config import readme_box, render

    live = json.loads((FRONTEND / "data" / "live.json").read_text())
    tour = json.loads((FRONTEND / "data" / "tour.json").read_text())
    assert readme_box(Path("README.md").read_text()) == render(live)
    assert tour["release"] == live["release"]
