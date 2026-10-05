"""Turn a failing simulated conversation into a regression task that replays the customer's side.

    python -m evals.sim.make_regression_task --run evals/results/sim/<run-id> --task cancel-eligible --trial 0
"""

import argparse
import json
from pathlib import Path

from evals.sim.env import load_seed
from evals.sim.schema import Task, load_tasks
from evals.sim.validate_tasks import DOC_PATHS, check_task

REGRESSION_FILE = Path("evals/sim/regression_tasks.json")
SCRIPT_INTRO = "Follow this script. Send these messages in order, one per turn, and only depart from them if the assistant asks for something they do not cover:"


def find_record(run_dir: Path, task_id: str, trial: int) -> dict:
    for line in (run_dir / "trajectories.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["task_id"] == task_id and r["trial"] == trial and r.get("transcript"):
            return r
    raise SystemExit(f"no recorded conversation for {task_id} trial {trial} in {run_dir}")


def regression_task(task: Task, record: dict, run_id: str) -> Task:
    lines = [m["content"] for m in record["transcript"] if m["role"] == "user"]
    script = "\n".join(f"{i}. {text}" for i, text in enumerate(lines, 1))
    new = task.model_copy(deep=True)
    new.id = f"{task.id}--{run_id}-t{record['trial']}"
    new.description = f"Regression from run {run_id}, trial {record['trial']}: {task.description}"
    new.user_scenario.instructions.task_instructions = f"{SCRIPT_INTRO}\n{script}\n\nOtherwise: {task.user_scenario.instructions.task_instructions}"
    return new


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--trial", type=int, default=0)
    args = p.parse_args()

    run_dir = Path(args.run)
    config = json.loads((run_dir / "config.json").read_text())
    tasks = {t.id: t for t in load_tasks(config.get("task_file", "evals/sim/tasks.json"))}
    new = regression_task(tasks[args.task], find_record(run_dir, args.task, args.trial), run_dir.name.split("_")[0])

    existing = json.loads(REGRESSION_FILE.read_text()) if REGRESSION_FILE.exists() else []
    if any(t["id"] == new.id for t in existing):
        raise SystemExit(f"{new.id} is already a regression task")
    docs = " ".join(Path(d).read_text() for d in DOC_PATHS)
    problems = check_task(new, load_seed(), docs)
    if problems:
        raise SystemExit(f"{new.id} fails validation: {problems}")
    existing.append(new.model_dump(exclude_defaults=True))
    REGRESSION_FILE.write_text(json.dumps(existing, indent=1) + "\n")
    print(f"added {new.id} to {REGRESSION_FILE}; run it with --task-file {REGRESSION_FILE}")


if __name__ == "__main__":
    main()
