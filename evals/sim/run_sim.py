"""Simulated-customer evaluation runs: k trials per task, graded on end state, with a hard spend cap.

    python -m evals.sim.run_sim --label bringup --tasks cancel-eligible,return-final-sale --k 1 --max-usd 0.10
"""

import argparse
import asyncio
import json
import logging
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from anthropic import AsyncAnthropic

from app.config import get_settings
from app.rag.vectorstore import ChromaVectorStore
from evals.sim.config import get_sim_settings
from evals.sim.env import load_seed
from evals.sim.grader import JudgeError, grade
from evals.sim.metrics import recorded, summarize
from evals.sim.orchestrator import INFRA_ERRORS, RETRYABLE, UNRECORDED, AgentConfig, Conversation, is_billing_error, run_conversation
from evals.sim.schema import Task, load_tasks
from evals.sim.user_sim import SimTokens
from mcp_server.simdb import db_hash

RESULTS_DIR = Path("evals/results/sim")
TASK_FILE = Path("evals/sim/tasks.json")
SUBSETS = Path("evals/sim/subsets.json")
EST_USD_PER_CONVERSATION = {
    ("claude-haiku-4-5", True): 0.0136,
    ("claude-haiku-4-5", False): 0.0163,
    ("claude-sonnet-5-5", True): 0.055,
    ("claude-sonnet-5-5", False): 0.060,
}
EST_JUDGE_USD_PER_CONVERSATION = 0.0021
APPROVAL_THRESHOLD_USD = 1.0


@dataclass
class Budget:
    max_usd: float
    per_conversation: float
    spent: float = 0.0
    in_flight: int = 0
    completed: int = 0
    completed_usd: float = 0.0
    stopped: str | None = None
    sim_tokens: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def charge(self, usd: float) -> None:
        self.spent += usd

    def exhausted(self) -> bool:
        if self.spent >= self.max_usd:
            self.stopped = self.stopped or "budget"
        return self.spent >= self.max_usd

    def expected_per_conversation(self) -> float:
        observed = self.completed_usd / self.completed if self.completed else 0.0
        return max(self.per_conversation, observed)

    def can_start(self) -> bool:
        return self.stopped is None and self.spent + (self.in_flight + 1) * self.expected_per_conversation() <= self.max_usd


def git_sha() -> tuple[str, bool]:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "app", "mcp_server", "evals/sim"], capture_output=True, text=True).stdout.strip())
    return sha, dirty


def select_tasks(tasks: list[Task], ids: str, categories: str, limit: int) -> list[Task]:
    if ids:
        wanted = [i.strip() for i in ids.split(",") if i.strip()]
        by_id = {t.id: t for t in tasks}
        missing = [i for i in wanted if i not in by_id]
        if missing:
            raise SystemExit(f"unknown task ids: {', '.join(missing)}")
        tasks = [by_id[i] for i in wanted]
    if categories:
        cats = {c.strip() for c in categories.split(",")}
        tasks = [t for t in tasks if t.category in cats]
    return tasks[:limit] if limit else tasks


def estimate(agent: AgentConfig, conversations: int, per_conversation: float | None) -> float:
    agent_cost = per_conversation or EST_USD_PER_CONVERSATION[(agent.model, agent.mutation_gate)]
    return conversations * (agent_cost + EST_JUDGE_USD_PER_CONVERSATION)


def record(conv: Conversation, graded: dict[str, Any] | None, excluded: str | None) -> dict[str, Any]:
    return {
        "task_id": conv.task.id,
        "category": conv.task.category,
        "trial": conv.trial,
        "stop_reason": conv.stop_reason,
        "error": conv.error,
        "excluded": excluded,
        "agent_turns": len(conv.turns),
        "turn_latency_s": [t["latency_s"] for t in conv.turns],
        "wall_s": conv.wall_s,
        "sim_model": conv.sim.model,
        "sim_tokens": {**asdict(conv.sim.tokens), "total": conv.sim.tokens.total},
        "grade": graded,
        "transcript": conv.transcript,
        "turns": conv.turns,
    }


def load_done(path: Path) -> set[tuple[str, int]]:
    if not path.exists():
        return set()
    done = set()
    for r in recorded([json.loads(line) for line in path.read_text().splitlines()]):
        if not r.get("excluded"):
            done.add((r["task_id"], r["trial"]))
    return done


async def attempt(task: Task, trial: int, ctx: dict[str, Any], budget: Budget) -> dict[str, Any] | None:
    for n in range(2):
        tokens = SimTokens()
        conv = await run_conversation(task, trial, ctx["seed"], ctx["agent"], ctx["settings"], ctx["store"], ctx["client"], ctx["http"], ctx["sim_settings"], tokens, budget)
        budget.sim_tokens += tokens.total
        if conv.stop_reason in UNRECORDED:
            budget.stopped = budget.stopped or conv.stop_reason
            return None
        if conv.stop_reason in RETRYABLE:
            if n == 0:
                continue
            return record(conv, None, conv.error or conv.stop_reason)
        try:
            graded = await grade(conv, ctx["seed"], ctx["client"], ctx["sim_settings"].sim_judge_model)
        except (*INFRA_ERRORS, JudgeError) as e:
            return record(conv, None, f"grading failed: {type(e).__name__}: {e}"[:300])
        except Exception as e:
            if is_billing_error(e):
                budget.stopped = "billing_error"
                return None
            raise
        budget.charge(graded["judge_cost_usd"])
        budget.completed += 1
        budget.completed_usd += graded["agent_cost_usd"] + graded["judge_cost_usd"]
        return record(conv, graded, None)
    return None


async def run_one(task: Task, trial: int, ctx: dict[str, Any], budget: Budget, out: Path, write_lock: asyncio.Lock) -> None:
    async with ctx["sem"]:
        async with budget.lock:
            if not budget.can_start():
                budget.stopped = budget.stopped or "budget"
                return
            budget.in_flight += 1
        try:
            rec = await attempt(task, trial, ctx, budget)
        finally:
            async with budget.lock:
                budget.in_flight -= 1
        if rec is None:
            return
        async with write_lock:
            with out.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        reward = (rec.get("grade") or {}).get("reward")
        print(f"  {task.id} trial {trial}: reward {reward} stop {rec['stop_reason']} spent ${budget.spent:.3f}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True)
    p.add_argument("--task-file", default=str(TASK_FILE))
    p.add_argument("--tasks", default="")
    p.add_argument("--subset", default="")
    p.add_argument("--categories", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--k", type=int, default=1)
    p.add_argument("--agent-model", default="claude-haiku-4-5")
    p.add_argument("--no-gate", action="store_true")
    p.add_argument("--no-reflection", action="store_true")
    p.add_argument("--no-confirmation", action="store_true")
    p.add_argument("--max-usd", type=float, required=True)
    p.add_argument("--est-per-conversation", type=float, default=None)
    p.add_argument("--approved-over-1", action="store_true")
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--resume", default="")
    p.add_argument("--estimate-only", action="store_true")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sim_settings = get_sim_settings()
    if not (sim_settings.sim_user_api_key and sim_settings.sim_user_base_url and sim_settings.sim_user_model):
        raise SystemExit("set SIM_USER_BASE_URL, SIM_USER_MODEL and SIM_USER_API_KEY in .env")
    agent = AgentConfig(args.agent_model, not args.no_gate, not args.no_reflection, not args.no_confirmation)
    if args.subset:
        args.tasks = ",".join(json.loads(SUBSETS.read_text())[args.subset]["task_ids"])
    tasks = select_tasks(load_tasks(args.task_file), args.tasks, args.categories, args.limit)
    seed = load_seed()
    sha, dirty = git_sha()

    if args.resume:
        run_dir = Path(args.resume)
        config = json.loads((run_dir / "config.json").read_text())
        if config["sim_model"] != sim_settings.sim_user_model or config["agent"] != asdict(agent):
            raise SystemExit("resume must keep the same simulator model and agent settings")
        config.setdefault("resumed", []).append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_sha": sha, "dirty": dirty})
        (run_dir / "config.json").write_text(json.dumps(config, indent=1) + "\n")
        tasks = select_tasks(load_tasks(config.get("task_file", TASK_FILE)), ",".join(config["task_ids"]), "", 0)
        args.k = config["k"]
    else:
        run_dir = RESULTS_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{args.label}"
    out = run_dir / "trajectories.jsonl"
    done = load_done(out)
    pending = [(t, i) for t in tasks for i in range(args.k) if (t.id, i) not in done]

    est = estimate(agent, len(pending), args.est_per_conversation)
    print(f"{len(pending)} conversations ({len(tasks)} tasks x k={args.k}), agent {agent.model}, gate {'on' if agent.mutation_gate else 'off'}")
    print(f"estimated cost ${est:.2f} (agent plus judge), cap ${args.max_usd:.2f}, simulator {sim_settings.sim_user_model}")
    if args.estimate_only:
        return
    if est > APPROVAL_THRESHOLD_USD and not args.approved_over_1:
        raise SystemExit(f"estimate is over ${APPROVAL_THRESHOLD_USD:.2f}; rerun with --approved-over-1 once approved")
    if args.max_usd > est * 1.5 + 0.05:
        raise SystemExit("--max-usd is far above the estimate; keep the cap near 1.3x the estimate")

    run_dir.mkdir(parents=True, exist_ok=True)
    if not args.resume:
        config = {
            "label": args.label,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_sha": sha,
            "dirty": dirty,
            "seed_hash": db_hash(seed),
            "agent": asdict(agent),
            "judge_model": sim_settings.sim_judge_model,
            "sim_model": sim_settings.sim_user_model,
            "sim_host": urlparse(sim_settings.sim_user_base_url).netloc,
            "sim_temperature": sim_settings.sim_user_temperature,
            "max_agent_turns": sim_settings.max_agent_turns,
            "k": args.k,
            "task_file": args.task_file,
            "subset": args.subset or None,
            "task_ids": [t.id for t in tasks],
            "max_usd": args.max_usd,
        }
        (run_dir / "config.json").write_text(json.dumps(config, indent=1) + "\n")

    settings = get_settings()
    budget = Budget(args.max_usd, args.est_per_conversation or EST_USD_PER_CONVERSATION[(agent.model, agent.mutation_gate)] + EST_JUDGE_USD_PER_CONVERSATION)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=60) as http:
        ctx = {
            "sem": asyncio.Semaphore(args.concurrency or sim_settings.concurrency),
            "seed": seed,
            "agent": agent,
            "settings": settings,
            "store": ChromaVectorStore(),
            "client": AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=4),
            "http": http,
            "sim_settings": sim_settings,
        }
        write_lock = asyncio.Lock()
        await asyncio.gather(*(run_one(t, i, ctx, budget, out, write_lock) for t, i in pending))

    records = [json.loads(line) for line in out.read_text().splitlines()] if out.exists() else []
    summary = {
        "config": config,
        "stopped": budget.stopped,
        "elapsed_s": round(time.perf_counter() - started, 1),
        "spent_this_session_usd": round(budget.spent, 4),
        "simulator_tokens_this_session": budget.sim_tokens,
        **summarize(records, config["k"]),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("stopped", "conversations", "excluded", "pass_hat_k", "writes", "cost", "simulator_tokens")}, indent=1))
    if budget.stopped == "simulator_quota":
        print("stopped: the simulator's quota ran out. Conversations cut off by it are not recorded.")
    if budget.stopped == "budget":
        print(f"stopped: spend reached the ${args.max_usd:.2f} cap. Conversations cut off by it are not recorded.")
    if budget.completed and budget.completed_usd / budget.completed > budget.per_conversation * 1.3:
        print(f"note: measured ${budget.completed_usd / budget.completed:.4f} per conversation, above the ${budget.per_conversation:.4f} estimate")
    if budget.stopped == "billing_error":
        print("stopped: the Anthropic API refused requests for billing (credit balance or usage limit). Conversations cut off by it are not recorded.")
    print(f"results in {run_dir}")


if __name__ == "__main__":
    asyncio.run(main())
