import json
import re
from pathlib import Path

from evals.scenario_check import homepage_asks, missing_scenarios


def defined_tests() -> set[str]:
    return {m for p in Path("tests").rglob("test_*.py") for m in re.findall(r"^def (test_[a-z0-9_]+)", p.read_text(), re.M)}


def test_every_test_named_in_the_review_fixes_doc_exists() -> None:
    named = set(re.findall(r"`(test_[a-z0-9_]+)`", Path("docs/review-fixes.md").read_text()))
    assert len(named) > 40 and named <= defined_tests(), sorted(named - defined_tests())


def test_every_homepage_button_has_a_scenario() -> None:
    scenarios = json.loads(Path("evals/scenarios.json").read_text())
    assert len(homepage_asks()) >= 9 and missing_scenarios(scenarios) == set()


def test_the_paid_workflows_read_only_ci_secrets() -> None:
    for name in ("agent-checks.yml", "sim.yml"):
        text = Path(".github/workflows", name).read_text()
        used = set(re.findall(r"secrets\.([A-Z_]+)", text))
        assert used and all(s.startswith("CI_") for s in used), (name, used)
    assert "secrets." not in Path(".github/workflows/ci.yml").read_text()
    assert "environment: paid-runs" in Path(".github/workflows/sim.yml").read_text()


def test_only_the_daily_demo_check_runs_on_a_github_schedule() -> None:
    scheduled = {p.name: re.findall(r'cron: "([^"]+)"', p.read_text()) for p in Path(".github/workflows").glob("*.yml")}
    assert {name: crons for name, crons in scheduled.items() if crons} == {"demo-check.yml": ["17 14 * * *"]}
