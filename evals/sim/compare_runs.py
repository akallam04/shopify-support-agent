"""Compare two simulation runs on the same tasks: pass^k, write safety, cost, and a paired interval.

    python -m evals.sim.compare_runs --a <run-dir> --b <run-dir> [--subset model_comparison] [--max-trials 2]
"""

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.sim.metrics import recorded, summarize

SUBSETS = Path("evals/sim/subsets.json")
BOOTSTRAP_SAMPLES = 10000
SEED = 2026


def graded_file(run: Path) -> Path:
    regraded = sorted(run.glob("regraded-*.jsonl"))
    return regraded[-1] if regraded else run / "trajectories.jsonl"


def load(run: Path, tasks: set[str] | None, max_trials: int | None) -> list[dict[str, Any]]:
    records = recorded([json.loads(line) for line in graded_file(run).read_text().splitlines()])
    return [
        r for r in records
        if r.get("grade") and (tasks is None or r["task_id"] in tasks) and (max_trials is None or r["trial"] < max_trials)
    ]


def task_rates(records: list[dict[str, Any]]) -> dict[str, float]:
    by_task: dict[str, list[float]] = defaultdict(list)
    for r in records:
        by_task[r["task_id"]].append(r["grade"]["reward"])
    return {t: statistics.mean(v) for t, v in by_task.items()}


def paired_interval(a: dict[str, float], b: dict[str, float]) -> tuple[float, float, float]:
    shared = sorted(set(a) & set(b))
    diffs = [b[t] - a[t] for t in shared]
    rng = random.Random(SEED)
    means = sorted(statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(BOOTSTRAP_SAMPLES))
    return statistics.mean(diffs), means[int(0.025 * BOOTSTRAP_SAMPLES)], means[int(0.975 * BOOTSTRAP_SAMPLES) - 1]


def unsafe_conversations(records: list[dict[str, Any]]) -> int:
    return sum(1 for r in records if r["grade"]["writes"]["unconfirmed"] or r["grade"]["writes"]["forbidden"])


def compare(a_dir: Path, b_dir: Path, subset: str | None, max_trials: int | None) -> dict[str, Any]:
    tasks = set(json.loads(SUBSETS.read_text())[subset]["task_ids"]) if subset else None
    a, b = load(a_dir, tasks, max_trials), load(b_dir, tasks, max_trials)
    k = min(max(r["trial"] for r in a), max(r["trial"] for r in b)) + 1
    diff, low, high = paired_interval(task_rates(a), task_rates(b))
    result: dict[str, Any] = {"a": str(graded_file(a_dir)), "b": str(graded_file(b_dir)), "subset": subset, "k": k}
    for name, records in (("a", a), ("b", b)):
        s = summarize(records, k)
        result[f"summary_{name}"] = s
        result[f"unsafe_conversations_{name}"] = unsafe_conversations(records)
    result["pass1_difference_b_minus_a"] = {"mean": diff, "ci95": [low, high], "tasks": len(set(task_rates(a)) & set(task_rates(b)))}
    return result


def table(result: dict[str, Any], label_a: str, label_b: str) -> str:
    sa, sb = result["summary_a"], result["summary_b"]
    rows = [f"| | {label_a} | {label_b} |", "|---|---|---|"]
    rows.append(f"| Conversations resolved | {sa['resolved']} of {sa['conversations']} | {sb['resolved']} of {sb['conversations']} |")
    for key in sa["pass_hat_k"]:
        rows.append(f"| {key} | {sa['pass_hat_k'][key]:.3f} | {sb['pass_hat_k'][key]:.3f} |")
    for name, label in (("unconfirmed", "Writes without a clear yes"), ("forbidden", "Forbidden writes")):
        rows.append(f"| {label} | {sa['writes'][name]} | {sb['writes'][name]} |")
    rows.append(f"| Conversations with an unsafe write | {result['unsafe_conversations_a']} | {result['unsafe_conversations_b']} |")
    rows.append(f"| Write precision | {sa['writes']['precision']:.3f} | {sb['writes']['precision']:.3f} |")
    rows.append(f"| Write recall | {sa['writes']['recall']:.3f} | {sb['writes']['recall']:.3f} |")
    rows.append(f"| Agent cost per resolved conversation | ${sa['cost']['agent_per_resolved_usd']:.4f} | ${sb['cost']['agent_per_resolved_usd']:.4f} |")
    rows.append(f"| Turn latency p50 / p95 | {sa['latency']['turn_p50_s']:.2f}s / {sa['latency']['turn_p95_s']:.2f}s | {sb['latency']['turn_p50_s']:.2f}s / {sb['latency']['turn_p95_s']:.2f}s |")
    rows.append(f"| Agent turns per conversation | {sa['turns_per_conversation']:.2f} | {sb['turns_per_conversation']:.2f} |")
    d = result["pass1_difference_b_minus_a"]
    rows.append(f"\npass^1 difference ({label_b} minus {label_a}) over {d['tasks']} tasks: {d['mean']:+.3f}, 95% paired bootstrap interval [{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}]")
    return "\n".join(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--label-a", default="A")
    p.add_argument("--label-b", default="B")
    p.add_argument("--subset", default=None)
    p.add_argument("--max-trials", type=int, default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    result = compare(Path(args.a), Path(args.b), args.subset, args.max_trials)
    text = table(result, args.label_a, args.label_b)
    print(text)
    if args.out:
        Path(args.out).write_text(json.dumps({**result, "table": text}, indent=1) + "\n")


if __name__ == "__main__":
    main()
