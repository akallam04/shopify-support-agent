"""Re-grade saved simulated conversations with the current task file, without re-running them.

The end state is rebuilt by replaying each conversation's recorded write calls on a fresh store,
and checked against the original grade's database result before anything is re-graded. Handoffs
are rebuilt from the customer's message, so whether a handoff happened is exact but the count may
differ.

    python -m evals.sim.regrade --run evals/results/sim/<run-id> [--tasks a,b] --max-usd 0.10
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from app.config import get_settings
from evals.sim.config import get_sim_settings
from evals.sim.env import build_db, load_seed
from evals.sim.grader import grade
from evals.sim.metrics import summarize
from evals.sim.orchestrator import Conversation
from evals.sim.schema import WRITE_TOOLS, Task, load_tasks
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.simdb import SimDB
from mcp_server.tools import HANDOFF_ACTION, execute


class ReplayMismatch(RuntimeError):
    pass


def rebuild(record: dict[str, Any], task: Task, seed: SimDB) -> Conversation:
    backend = SimStoreBackend(build_db(seed, task.initial_state))
    for turn in record["turns"]:
        for call in turn["tool_calls"]:
            if call["name"] in WRITE_TOOLS or call["name"] == HANDOFF_ACTION:
                execute(backend, call["name"], call["args"])
        for step in turn["gate_trace"]:
            if step.get("step") == "handoff_recorded" and step.get("ok"):
                execute(backend, HANDOFF_ACTION, {"summary": f"Customer asked for a person: {turn['user'][:300]}", "order_number": ""})
    return Conversation(
        task=task,
        trial=record["trial"],
        backend=backend,
        sim=None,
        transcript=record["transcript"],
        turns=record["turns"],
        stop_reason=record["stop_reason"],
        error=record.get("error"),
    )


async def regrade_run(run_dir: Path, task_file: str, only: set[str], max_usd: float) -> dict[str, Any]:
    tasks = {t.id: t for t in load_tasks(task_file)}
    seed = load_seed()
    records = [json.loads(line) for line in (run_dir / "trajectories.jsonl").read_text().splitlines()]
    client = AsyncAnthropic(api_key=get_settings().anthropic_api_key, max_retries=4)
    judge = get_sim_settings().sim_judge_model
    spent, out, changes = 0.0, [], []
    for record in records:
        if record.get("grade") is None or (only and record["task_id"] not in only):
            out.append(record)
            continue
        if spent >= max_usd:
            raise SystemExit(f"stopped at the ${max_usd:.2f} cap before regrading everything; nothing was written")
        conv = rebuild(record, tasks[record["task_id"]], seed)
        graded = await grade(conv, seed, client, judge)
        if graded["db_match"] != record["grade"]["db_match"]:
            raise ReplayMismatch(f"{record['task_id']} trial {record['trial']}: rebuilt end state does not match the original grade")
        spent += graded["judge_cost_usd"]
        graded["agent_cost_usd"] = record["grade"]["agent_cost_usd"]
        if graded["reward"] != record["grade"]["reward"]:
            changes.append({"task_id": record["task_id"], "trial": record["trial"], "before": record["grade"]["reward"], "after": graded["reward"]})
        out.append({**record, "grade": graded, "regraded_from": record["grade"]["reward"]})
    k = json.loads((run_dir / "config.json").read_text())["k"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (run_dir / f"regraded-{stamp}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
    report = {
        "regraded_at": stamp,
        "task_file": task_file,
        "tasks": sorted(only) if only else "all",
        "judge_usd": round(spent, 4),
        "changes": changes,
        "before": summarize(records, k),
        "after": summarize(out, k),
    }
    (run_dir / f"regrade-{stamp}.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True)
    p.add_argument("--task-file", default="evals/sim/tasks.json")
    p.add_argument("--tasks", default="")
    p.add_argument("--max-usd", type=float, required=True)
    args = p.parse_args()
    only = {t.strip() for t in args.tasks.split(",") if t.strip()}
    report = asyncio.run(regrade_run(Path(args.run), args.task_file, only, args.max_usd))
    for key in ("before", "after"):
        s = report[key]
        print(f"{key}: resolved {s['resolved']}/{s['conversations']}, {s['pass_hat_k']}, unsafe writes {s['writes']['unsafe']}")
    print(f"changed grades: {report['changes']}")
    print(f"judge cost ${report['judge_usd']:.3f}")


if __name__ == "__main__":
    main()
