"""Builds the How it works page data from saved runs, so every number on the site traces to a run."""

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.costs import usage_cost
from evals.sim.compare_runs import compare, paired_interval, task_rates
from evals.sim.metrics import graded_file, recorded, resolved_safely, summarize

RUNS = Path("evals/results/sim")
OUT = Path("frontend/data")

HEADLINE_ON = RUNS / "20261004-220530_headline-gate-on-k4"
HEADLINE_OFF = RUNS / "20261004-220533_headline-gate-off-k4"
HELDOUT = RUNS / "20261005-005107_heldout-gate-on-k4"
BASELINE = RUNS / "20261004-214247_baseline-gate-on-k2"
SONNET = RUNS / "20261005-010158_compare-sonnet-5-5-k2"
NO_REFLECTION = RUNS / "20261005-010802_gate-parts-no-reflection-k2"
NO_CONFIRMATION = RUNS / "20261005-010804_gate-parts-no-confirmation-k2"
AFTER_FIXES = RUNS / "20261005-203656_phase6-fixcheck2-k2"
UPGRADE_OLD = RUNS / "20261007-190900_upgrade-haiku-4-5-k2"
UPGRADE_NEW = RUNS / "20261007-190900_upgrade-haiku-5-5-k2"

TASK_FILES = (Path("evals/sim/tasks.json"), Path("evals/sim/heldout_tasks.json"))

EXAMPLES = [
    ("cancel-gate-on", HEADLINE_ON, "cancel-eligible", 0, "A cancellation, held until the customer says yes"),
    ("cancel-gate-off", HEADLINE_OFF, "cancel-eligible", 0, "The same request with the gate off"),
    ("decline-gate-on", HEADLINE_ON, "confirm-decline", 0, "The customer says no at the confirmation"),
    ("decline-gate-off", HEADLINE_OFF, "confirm-decline", 0, "Gate off: cancelled before the customer could say no"),
    ("return-partial", HEADLINE_ON, "return-partial-quantity", 0, "Returning one of two items"),
    ("question-then-yes", HEADLINE_ON, "confirm-question-then-yes", 0, "A question at the confirmation, then a yes"),
    ("injection", HEADLINE_ON, "injection-fake-override", 0, "A fake manager override mid-chat"),
    ("guessed-reason", HEADLINE_ON, "which-order-second-jacket", 1, "A failure: the return reason was guessed"),
    ("refusal-after-fix", AFTER_FIXES, "return-final-sale", 1, "A final-sale return, explained (after the fixes)"),
    ("correction-after-fix", AFTER_FIXES, "confirm-correct-zip", 0, "A corrected ZIP code at the confirmation (after the fixes)"),
]


def load(run: Path, max_trials: int | None = None) -> list[dict[str, Any]]:
    records = recorded([json.loads(line) for line in graded_file(run).read_text().splitlines()])
    return [r for r in records if max_trials is None or r["trial"] < max_trials]


def interval(a: list[dict[str, Any]], b: list[dict[str, Any]], safe: bool) -> dict[str, float]:
    mean, low, high = paired_interval(task_rates(a, safe), task_rates(b, safe))
    return {"mean": round(mean, 3), "low": round(low, 3), "high": round(high, 3)}


def arm(records: list[dict[str, Any]], k: int) -> dict[str, Any]:
    s = summarize(records, k)
    unsafe = sum(1 for r in records if r["grade"]["writes"]["unconfirmed"] or r["grade"]["writes"]["forbidden"])
    return {
        "conversations": s["conversations"],
        "tasks": s["tasks"],
        "resolved": s["resolved"],
        "resolved_safely": s["resolved_safely"],
        "pass_hat_k": [round(v, 3) for v in s["pass_hat_k"].values()],
        "pass_hat_k_safe": [round(v, 3) for v in s["pass_hat_k_safe"].values()],
        "unconfirmed_writes": s["writes"]["unconfirmed"],
        "forbidden_writes": s["writes"]["forbidden"],
        "unsafe_conversations": unsafe,
        "precision": round(s["writes"]["precision"], 3) if s["writes"]["precision"] is not None else None,
        "recall": round(s["writes"]["recall"], 3) if s["writes"]["recall"] is not None else None,
        "cost_per_resolved_usd": s["cost"]["agent_per_resolved_usd"],
        "latency_p50_s": round(s["latency"]["turn_p50_s"], 2),
        "latency_p95_s": round(s["latency"]["turn_p95_s"], 2),
    }


def categories(on: list[dict[str, Any]], off: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table: dict[str, dict[str, Any]] = defaultdict(dict)
    for name, records in (("on", on), ("off", off)):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            grouped[r["category"]].append(r)
        for category, rs in grouped.items():
            table[category][name] = {
                "n": len(rs),
                "resolved": sum(1 for r in rs if r["grade"]["reward"] == 1.0),
                "resolved_safely": sum(1 for r in rs if resolved_safely(r)),
            }
    return [{"category": c, **v} for c, v in sorted(table.items())]


def headline() -> dict[str, Any]:
    on, off = load(HEADLINE_ON), load(HEADLINE_OFF)
    original_on = recorded([json.loads(line) for line in (HEADLINE_ON / "trajectories.jsonl").read_text().splitlines()])
    original_off = recorded([json.loads(line) for line in (HEADLINE_OFF / "trajectories.jsonl").read_text().splitlines()])
    return {
        "runs": {"on": str(graded_file(HEADLINE_ON)), "off": str(graded_file(HEADLINE_OFF))},
        "trials": 4,
        "on": arm(on, 4),
        "off": arm(off, 4),
        "difference": {"resolved": interval(off, on, False), "resolved_safely": interval(off, on, True)},
        "categories": categories(on, off),
        "judge_fix": {
            "before": {
                "on_resolved_safely": sum(1 for r in original_on if resolved_safely(r)),
                "off_resolved_safely": sum(1 for r in original_off if resolved_safely(r)),
                "difference": interval(original_off, original_on, True),
            },
            "after": {
                "on_resolved_safely": sum(1 for r in on if resolved_safely(r)),
                "off_resolved_safely": sum(1 for r in off if resolved_safely(r)),
                "difference": interval(off, on, True),
            },
        },
    }


def heldout() -> dict[str, Any]:
    return {"run": str(graded_file(HELDOUT)), **arm(load(HELDOUT), 4)}


def fixes() -> dict[str, Any]:
    result = compare(BASELINE, HEADLINE_ON, None, 2)
    before, after = result["summary_a"], result["summary_b"]
    diff = result["pass1_difference_b_minus_a"]
    return {
        "runs": {"before": result["a"], "after": result["b"]},
        "conversations": before["conversations"],
        "before_resolved": before["resolved"],
        "after_resolved": after["resolved"],
        "difference": {"mean": round(diff["mean"], 3), "low": round(diff["ci95"][0], 3), "high": round(diff["ci95"][1], 3)},
        "recall_before": round(before["writes"]["recall"], 3),
        "recall_after": round(after["writes"]["recall"], 3),
    }


def gate_parts() -> list[dict[str, Any]]:
    rows = []
    full = None
    for label, run in (("Reflection off", NO_REFLECTION), ("Confirmation off", NO_CONFIRMATION), ("Gate off", HEADLINE_OFF)):
        result = compare(HEADLINE_ON, run, "gate_parts", 2)
        full = full or result["summary_a"]
        rows.append((label, result["summary_b"]))
    rows.insert(0, ("Full gate", full))
    return [
        {
            "setting": label,
            "conversations": s["conversations"],
            "resolved": s["resolved"],
            "resolved_safely": s["resolved_safely"],
            "unconfirmed_writes": s["writes"]["unconfirmed"],
            "forbidden_writes": s["writes"]["forbidden"],
        }
        for label, s in rows
    ]


def models() -> dict[str, Any]:
    result = compare(HEADLINE_ON, SONNET, "model_comparison", 2)
    diff = result["safe_pass1_difference_b_minus_a"]
    view = {}
    for name, key in (("haiku", "summary_a"), ("sonnet", "summary_b")):
        s = result[key]
        view[name] = {
            "conversations": s["conversations"],
            "resolved_safely": s["resolved_safely"],
            "pass_hat_k_safe": [round(v, 3) for v in s["pass_hat_k_safe"].values()],
            "cost_per_resolved_usd": s["cost"]["agent_per_resolved_usd"],
            "latency_p50_s": round(s["latency"]["turn_p50_s"], 2),
            "latency_p95_s": round(s["latency"]["turn_p95_s"], 2),
        }
    view["difference"] = {"mean": round(diff["mean"], 3), "low": round(diff["ci95"][0], 3), "high": round(diff["ci95"][1], 3)}
    return view


def upgrade() -> dict[str, Any]:
    old, new = load(UPGRADE_OLD), load(UPGRADE_NEW)
    return {"haiku_4_5": arm(old, 2), "haiku_5_5": arm(new, 2), "difference": interval(old, new, safe=True)}


def failures() -> dict[str, Any]:
    labels = json.loads((HEADLINE_ON / "failure_labels.json").read_text())
    counts = Counter(f["label"] for f in labels["failures"])
    return {
        "run": str(HEADLINE_ON),
        "total": len(labels["failures"]),
        "by_owner": labels["by_owner"],
        "by_label": [{"label": label, "count": n} for label, n in counts.most_common()],
        "items": labels["failures"],
    }


def audit() -> dict[str, Any]:
    data = json.loads((HEADLINE_OFF / "grader_audit.json").read_text())
    return {key: data[key] for key in ("conversations", "resolved_grades_wrong", "yes_check_verdicts_audited", "yes_check_verdicts_wrong")}


def tasks() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in TASK_FILES:
        data = json.loads(path.read_text())
        for task in data["tasks"] if isinstance(data, dict) else data:
            found[task["id"]] = task
    return found


def short_result(name: str, raw: str) -> Any:
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:400]
    if name.startswith("prepare:") and isinstance(result, dict):
        return {k: result.get(k) for k in ("allowed", "code", "reason", "summary")}
    return result


def turn_view(turn: dict[str, Any]) -> dict[str, Any]:
    tools = [
        {
            "kind": "policy_check" if c["name"].startswith("prepare:") else "tool",
            "name": c["name"].removeprefix("prepare:"),
            "args": c.get("args", {}),
            "result": short_result(c["name"], c.get("result", "")),
        }
        for c in turn.get("tool_calls", [])
        if c["name"] != "pending_action"
    ]
    return {
        "user": turn["user"],
        "agent": turn["agent"],
        "intent": turn.get("intent", ""),
        "tools": tools,
        "gate": turn.get("gate_trace", []),
        "latency_s": round(turn.get("latency_s") or 0, 2),
        "cost_usd": round(usage_cost(turn.get("usage", [])), 5),
    }


def outcome_note(record: dict[str, Any], labels: dict[tuple[str, int], str]) -> str:
    writes = record["grade"]["writes"]
    if writes["forbidden"]:
        return "A forbidden write: the store changed something the customer did not want changed."
    if writes["unconfirmed"]:
        return "Resolved, but the write ran before the customer agreed to it."
    if record["grade"]["reward"] != 1.0:
        return labels.get((record["task_id"], record["trial"]), "Failed.")
    return "Resolved safely."


def examples() -> list[dict[str, Any]]:
    task_index = tasks()
    out = []
    for key, run, task_id, trial, title in EXAMPLES:
        record = next(r for r in load(run) if r["task_id"] == task_id and r["trial"] == trial)
        config = json.loads((run / "config.json").read_text())
        label_file = run / "failure_labels.json"
        labels = {}
        if label_file.exists():
            labels = {(f["task_id"], f["trial"]): f["note"] for f in json.loads(label_file.read_text())["failures"]}
        out.append({
            "id": key,
            "title": title,
            "task": task_index[task_id]["description"],
            "run": run.name,
            "task_id": task_id,
            "trial": trial,
            "gate": "on" if config.get("agent", {}).get("mutation_gate", True) else "off",
            "resolved": record["grade"]["reward"] == 1.0,
            "resolved_safely": resolved_safely(record),
            "note": outcome_note(record, labels),
            "turns": [turn_view(t) for t in record["turns"]],
        })
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = {
        "headline": headline(),
        "heldout": heldout(),
        "fixes": fixes(),
        "gate_parts": gate_parts(),
        "models": models(),
        "upgrade": upgrade(),
        "failures": failures(),
        "audit": audit(),
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=1) + "\n")
    (OUT / "transcripts.json").write_text(json.dumps(examples(), indent=1) + "\n")
    print(f"wrote {OUT / 'results.json'} and {OUT / 'transcripts.json'}")


if __name__ == "__main__":
    main()
