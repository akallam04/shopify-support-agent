"""Static checks and a gold replay for the simulation tasks. Exits non-zero when any task is broken.

    python -m evals.sim.validate_tasks [--task-file evals/sim/heldout_tasks.json]
"""

import argparse
import json
import sys
from pathlib import Path

from app.agent.tool_executor import READ_FUNCTIONS
from evals.sim.env import TaskError, build_db, load_seed, target_db
from evals.sim.schema import WRITE_TOOLS, Task, load_tasks
from mcp_server.backends.sim import SimStoreBackend
from mcp_server.simdb import SimDB, db_hash
from mcp_server.tools import HANDOFF_ACTION, execute

DOC_PATHS = ("data/policies/returns.md", "data/policies/shipping.md", "data/policies/faq.md", "data/catalog/products.jsonl")
KNOWN_TOOLS = set(READ_FUNCTIONS) | WRITE_TOOLS | {HANDOFF_ACTION}


def reference_outputs(seed: SimDB, task: Task) -> str:
    backend = SimStoreBackend(build_db(seed, task.initial_state))
    outputs = []
    for action in task.evaluation_criteria.actions:
        if action.name in READ_FUNCTIONS:
            outputs.append(json.dumps(READ_FUNCTIONS[action.name](backend, **action.arguments)))
    target = target_db(seed, task)
    for action in task.evaluation_criteria.write_actions():
        outputs.append(json.dumps(READ_FUNCTIONS["get_order_status"](SimStoreBackend(target), action.arguments["order_number"], action.arguments["email"])))
    return " ".join(outputs)


def write_outcome_text(seed: SimDB, task: Task) -> str:
    backend = SimStoreBackend(build_db(seed, task.initial_state))
    return " ".join(json.dumps(execute(backend, a.name, a.arguments)) for a in task.evaluation_criteria.write_actions())


def check_task(task: Task, seed: SimDB, docs: str) -> list[str]:
    problems = []
    c = task.evaluation_criteria
    if "DB" not in c.reward_basis:
        problems.append("reward_basis must include DB so stray writes fail")
    if "COMMUNICATE" in c.reward_basis and not c.communicate_info:
        problems.append("COMMUNICATE is in reward_basis but communicate_info is empty")
    if "NL_ASSERTION" in c.reward_basis and not c.nl_assertions:
        problems.append("NL_ASSERTION is in reward_basis but there are no assertions")
    if "ENV_ASSERTION" in c.reward_basis and not c.env_assertions:
        problems.append("ENV_ASSERTION is in reward_basis but there are no env assertions")
    if not c.write_actions() and not ({"COMMUNICATE", "NL_ASSERTION", "ENV_ASSERTION"} & set(c.reward_basis)):
        problems.append("a task with no writes passes on an idle agent unless it checks what was said or recorded")
    for action in c.actions:
        if action.name not in KNOWN_TOOLS:
            problems.append(f"unknown tool {action.name}")
        order = action.arguments.get("order_number")
        if order and order not in seed.orders:
            problems.append(f"{action.name} names unknown order {order}")
    for f in c.forbidden_actions:
        if f.name not in WRITE_TOOLS:
            problems.append(f"forbidden action {f.name} is not a write tool")
    known = task.user_scenario.instructions.known_info.lower()
    for secret in task.user_scenario.must_not_reveal:
        if secret.lower() in known:
            problems.append(f"must_not_reveal {secret!r} appears in known_info")

    try:
        initial = db_hash(build_db(seed, task.initial_state))
        target = db_hash(target_db(seed, task))
    except TaskError as e:
        return problems + [str(e)]
    if (initial != target) != bool(c.write_actions()):
        problems.append("the target database must differ from the start exactly when the task has reference writes")

    grounding = (reference_outputs(seed, task) + " " + write_outcome_text(seed, task) + " " + docs).lower()
    for item in c.communicate_info:
        options = [item] if isinstance(item, str) else item
        if not any(o.lower() in grounding for o in options):
            problems.append(f"communicate_info {item!r} is not in any tool output or policy document")
    return problems


def validate(tasks: list[Task], seed: SimDB) -> dict[str, list[str]]:
    docs = " ".join(Path(p).read_text() for p in DOC_PATHS)
    problems: dict[str, list[str]] = {}
    seen: set[str] = set()
    for task in tasks:
        found = check_task(task, seed, docs)
        if task.id in seen:
            found.append("duplicate task id")
        seen.add(task.id)
        if found:
            problems[task.id] = found
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", default="evals/sim/tasks.json")
    tasks = load_tasks(parser.parse_args().task_file)
    problems = validate(tasks, load_seed())
    for task_id, found in problems.items():
        for p in found:
            print(f"{task_id}: {p}")
    print(f"{len(tasks)} tasks, {len(problems)} with problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
