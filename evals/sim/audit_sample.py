"""Pick the conversations for a manual grader audit and print them for reading.

The sample is every passing gate-off conversation that made a write, plus a random 10 percent of
all other passing conversations, drawn with a fixed seed so the audit can be repeated.

    python -m evals.sim.audit_sample --run evals/results/sim/<gate-on> --run evals/results/sim/<gate-off>
"""

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

from evals.sim.metrics import recorded

SEED = 2026
SHARE = 0.10


def load(run: Path) -> list[dict[str, Any]]:
    gate = json.loads((run / "config.json").read_text())["agent"]["mutation_gate"]
    records = recorded([json.loads(line) for line in (run / "trajectories.jsonl").read_text().splitlines()])
    return [{**r, "run": run.name, "gate": gate} for r in records if r.get("grade") and r["grade"]["reward"] == 1.0]


def sample(runs: list[Path]) -> list[dict[str, Any]]:
    passes = [r for run in runs for r in load(run)]
    must = [r for r in passes if not r["gate"] and r["grade"]["writes"]["executed"]]
    rest = sorted((r for r in passes if r not in must), key=lambda r: (r["run"], r["task_id"], r["trial"]))
    drawn = random.Random(SEED).sample(rest, math.ceil(SHARE * len(passes)))
    return must + drawn


def show(r: dict[str, Any]) -> str:
    g = r["grade"]
    lines = [f"== {r['run']} | {r['task_id']} trial {r['trial']} | gate {'on' if r['gate'] else 'off'} | writes {g['writes']['executed']} | components {g['components']}"]
    lines += [f"   {m['role'][:4]}: {m['content']}" for m in r["transcript"]]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="append", required=True)
    args = p.parse_args()
    picked = sample([Path(r) for r in args.run])
    for r in picked:
        print(show(r))
    print(f"{len(picked)} conversations")


if __name__ == "__main__":
    main()
