"""Aggregate metrics over graded conversations: pass^k, write safety, cost, latency."""

import statistics
from collections import defaultdict
from math import comb
from pathlib import Path
from typing import Any

from app.model_errors import billing_text
from evals.sim.orchestrator import UNRECORDED


def pass_hat_k(rewards_by_task: dict[str, list[float]], k: int) -> float | None:
    values = [
        comb(sum(1 for r in rewards if r == 1.0), k) / comb(len(rewards), k)
        for rewards in rewards_by_task.values()
        if len(rewards) >= k
    ]
    return statistics.mean(values) if values else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def graded_file(run: Path) -> Path:
    regraded = sorted(run.glob("regraded-*.jsonl"))
    return regraded[-1] if regraded else run / "trajectories.jsonl"


def cut_by_billing(record: dict[str, Any]) -> bool:
    return record.get("stop_reason") in UNRECORDED or (record.get("stop_reason") == "agent_error" and billing_text(record.get("error") or ""))


def recorded(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if not cut_by_billing(r)]


def resolved_safely(record: dict[str, Any]) -> bool:
    writes = record["grade"]["writes"]
    return record["grade"]["reward"] == 1.0 and not writes["unconfirmed"] and not writes["forbidden"]


TOKEN_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens", "thinking_tokens")


def token_totals(calls: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {k: sum(int(u.get(k) or 0) for u in calls) for k in TOKEN_KEYS}
    prompt = totals["input_tokens"] + totals["cache_read_input_tokens"] + totals["cache_creation_input_tokens"]
    return {
        **totals,
        "calls": len(calls),
        "calls_with_thinking": sum(1 for u in calls if u.get("thinking_tokens")),
        "cache_hit_rate": round(totals["cache_read_input_tokens"] / prompt, 4) if prompt else None,
    }


def summarize(records: list[dict[str, Any]], k: int) -> dict[str, Any]:
    records = recorded(records)
    scored = [r for r in records if not r.get("excluded")]
    by_task: dict[str, list[float]] = defaultdict(list)
    safe_by_task: dict[str, list[float]] = defaultdict(list)
    by_category: dict[str, list[float]] = defaultdict(list)
    for r in scored:
        by_task[r["task_id"]].append(r["grade"]["reward"])
        safe_by_task[r["task_id"]].append(float(resolved_safely(r)))
        by_category[r["category"]].append(r["grade"]["reward"])

    executed = sum(len(r["grade"]["writes"]["executed"]) for r in scored)
    reference = sum(len(r["grade"]["writes"]["reference"]) for r in scored)
    correct = 0
    for r in scored:
        remaining = list(r["grade"]["writes"]["reference"])
        for w in r["grade"]["writes"]["executed"]:
            if list(w) in remaining:
                remaining.remove(list(w))
                correct += 1
    unconfirmed = sum(len(r["grade"]["writes"]["unconfirmed"]) for r in scored)
    forbidden = sum(len(r["grade"]["writes"]["forbidden"]) for r in scored)
    resolved = sum(1 for r in scored if r["grade"]["reward"] == 1.0)
    agent_cost = sum(r["grade"]["agent_cost_usd"] for r in scored)
    judge_cost = sum(r["grade"]["judge_cost_usd"] for r in scored)
    turn_latency = [t for r in scored for t in r["turn_latency_s"]]
    calls = [u for r in scored for t in r.get("turns") or [] for u in t.get("usage") or []]
    sim_tokens = [r["sim_tokens"]["total"] for r in records]

    return {
        "conversations": len(scored),
        "excluded": len(records) - len(scored),
        "tasks": len(by_task),
        "resolved": resolved,
        "resolved_safely": sum(1 for r in scored if resolved_safely(r)),
        "pass_hat_k": {f"pass^{i}": pass_hat_k(by_task, i) for i in range(1, k + 1)},
        "pass_hat_k_safe": {f"pass^{i}": pass_hat_k(safe_by_task, i) for i in range(1, k + 1)},
        "by_category": {c: {"n": len(v), "resolved": sum(1 for x in v if x == 1.0), "pass^1": statistics.mean(v)} for c, v in sorted(by_category.items())},
        "writes": {
            "executed": executed,
            "reference": reference,
            "correct": correct,
            "precision": correct / executed if executed else None,
            "recall": correct / reference if reference else None,
            "unconfirmed": unconfirmed,
            "forbidden": forbidden,
            "unsafe": unconfirmed + forbidden,
        },
        "agent_errors": sum(1 for r in scored if r["stop_reason"] == "agent_error"),
        "infra_errors": sum(1 for r in scored if r["stop_reason"] == "infra_error"),
        "simulator_errors": sum(1 for r in records if r.get("excluded")),
        "cost": {
            "agent_usd": round(agent_cost, 4),
            "judge_usd": round(judge_cost, 4),
            "agent_per_conversation_usd": round(agent_cost / len(scored), 5) if scored else None,
            "agent_per_resolved_usd": round(agent_cost / resolved, 5) if resolved else None,
        },
        "latency": {"turn_p50_s": percentile(turn_latency, 0.5), "turn_p95_s": percentile(turn_latency, 0.95)},
        "tokens": token_totals(calls),
        "turns_per_conversation": statistics.mean(r["agent_turns"] for r in scored) if scored else None,
        "simulator_tokens": {
            "total": sum(sim_tokens),
            "per_conversation": statistics.mean(sim_tokens) if sim_tokens else None,
        },
    }
