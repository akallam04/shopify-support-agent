"""Eval runner: pushes every dataset case through the live agent and grades it.

Run from the repo root:
  .venv/bin/python -m evals.run_evals --label baseline
Useful flags: --limit 5 (smoke run), --concurrency 2, --dataset <path>.
Writes full per-case records to evals/results/, printed summary at the end.
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.graph import build_graph
from app.config import get_settings
from app.mcp_client import ShopifyTools
from app.rag.vectorstore import ChromaVectorStore
from evals.graders import (
    check_contains,
    check_intent,
    check_retrieval,
    check_tools,
    matches_refusal_template,
    run_judge,
)

RESULTS_DIR = Path("evals/results")

# usd per million tokens (input, output), sticker prices as of jul 2026
PRICES_PER_MTOK = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
}


def usage_cost(usage: list[dict[str, Any]]) -> float:
    total = 0.0
    for u in usage:
        prices = PRICES_PER_MTOK.get(u.get("model", ""), (0.0, 0.0))
        total += u["input_tokens"] / 1e6 * prices[0] + u["output_tokens"] / 1e6 * prices[1]
    return total


async def evaluate_case(
    case: dict[str, Any],
    graph: Any,
    client: AsyncAnthropic,
    judge_model: str,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    async with sem:
        started = time.perf_counter()
        try:
            state = await graph.ainvoke({"messages": list(case["messages"])})
        except Exception as e:  # noqa: BLE001, a broken case must not sink the whole run
            return {
                "id": case["id"],
                "category": case["category"],
                "pass": False,
                "checks": {"error": False},
                "judge_reason": "",
                "error": f"{type(e).__name__}: {e}",
                "intent_actual": "",
                "retries": 0,
                "latency_s": round(time.perf_counter() - started, 2),
                "agent_tokens": 0,
                "agent_cost_usd": 0.0,
                "judge_cost_usd": 0.0,
                "response": "",
            }
        latency = time.perf_counter() - started

        response = state.get("response", "")
        actual_intent = "injection" if state.get("hard_injection") else state.get("intent", "")
        expect = case["expect"]

        checks: dict[str, bool] = {}
        judge_reason = ""

        intent_ok = check_intent(expect, actual_intent)
        if intent_ok is not None:
            checks["intent"] = intent_ok
        retrieval_ok = check_retrieval(expect, state.get("retrieved", []))
        if retrieval_ok is not None:
            checks["retrieval"] = retrieval_ok
        contains_ok = check_contains(expect, response)
        if contains_ok is not None:
            checks["contains"] = contains_ok
        tools_ok = check_tools(expect, state.get("tool_results", []))
        if tools_ok is not None:
            checks["tools"] = tools_ok

        tool_results = state.get("tool_results", [])
        judge_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        if expect.get("refusal"):
            if matches_refusal_template(response):
                checks["refusal"] = True
            elif expect.get("judge"):
                ok, judge_reason, judge_usage = await run_judge(
                    client, judge_model, case["messages"], response, expect["judge"], tool_results
                )
                checks["refusal"] = ok
            else:
                checks["refusal"] = False
        elif expect.get("judge"):
            ok, judge_reason, judge_usage = await run_judge(
                client, judge_model, case["messages"], response, expect["judge"], tool_results
            )
            checks["judge"] = ok

        agent_usage = state.get("usage", [])
        agent_tokens = sum(u["input_tokens"] + u["output_tokens"] for u in agent_usage)
        return {
            "id": case["id"],
            "category": case["category"],
            "pass": all(checks.values()),
            "checks": checks,
            "judge_reason": judge_reason,
            "intent_actual": actual_intent,
            "retries": state.get("retry_count", 0),
            "latency_s": round(latency, 2),
            "agent_tokens": agent_tokens,
            "agent_cost_usd": round(usage_cost(agent_usage), 6),
            "judge_cost_usd": round(
                usage_cost([{**judge_usage, "model": judge_model}]), 6
            ),
            "response": response,
        }


def rollup(records: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(items: list[bool]) -> float | None:
        return round(100 * sum(items) / len(items), 1) if items else None

    categories: dict[str, dict[str, Any]] = {}
    for cat in sorted({r["category"] for r in records}):
        rows = [r for r in records if r["category"] == cat]
        categories[cat] = {
            "n": len(rows),
            "pass_rate": rate([r["pass"] for r in rows]),
            "intent_accuracy": rate(
                [r["checks"]["intent"] for r in rows if "intent" in r["checks"]]
            ),
            "retrieval_hit_rate": rate(
                [r["checks"]["retrieval"] for r in rows if "retrieval" in r["checks"]]
            ),
            "tool_success_rate": rate(
                [r["checks"]["tools"] for r in rows if "tools" in r["checks"]]
            ),
            "refusal_correct_rate": rate(
                [r["checks"]["refusal"] for r in rows if "refusal" in r["checks"]]
            ),
            "mean_latency_s": round(statistics.mean(r["latency_s"] for r in rows), 2),
        }

    latencies = sorted(r["latency_s"] for r in records)
    mean_cost = statistics.mean(r["agent_cost_usd"] for r in records)
    return {
        "n": len(records),
        "pass_rate": rate([r["pass"] for r in records]),
        "intent_accuracy": rate(
            [r["checks"]["intent"] for r in records if "intent" in r["checks"]]
        ),
        "retrieval_hit_rate": rate(
            [r["checks"]["retrieval"] for r in records if "retrieval" in r["checks"]]
        ),
        "tool_success_rate": rate(
            [r["checks"]["tools"] for r in records if "tools" in r["checks"]]
        ),
        "refusal_correct_rate": rate(
            [r["checks"]["refusal"] for r in records if "refusal" in r["checks"]]
        ),
        "mean_latency_s": round(statistics.mean(latencies), 2),
        "p95_latency_s": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 2),
        "mean_agent_tokens": round(statistics.mean(r["agent_tokens"] for r in records)),
        "cost_per_conversation_usd": round(mean_cost, 5),
        "cost_per_100_conversations_usd": round(mean_cost * 100, 2),
        "judge_cost_total_usd": round(sum(r["judge_cost_usd"] for r in records), 4),
        "categories": categories,
    }


def print_summary(summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    print(
        f"\n{'category':13} {'n':>3} {'pass':>6} {'intent':>7} {'retr':>6} "
        f"{'tools':>6} {'refuse':>7} {'lat(s)':>7}"
    )

    def cell(v: float | None) -> str:
        return "-" if v is None else f"{v:.0f}%"

    for cat, row in summary["categories"].items():
        print(
            f"{cat:13} {row['n']:>3} {cell(row['pass_rate']):>6} "
            f"{cell(row['intent_accuracy']):>7} {cell(row['retrieval_hit_rate']):>6} "
            f"{cell(row['tool_success_rate']):>6} {cell(row['refusal_correct_rate']):>7} "
            f"{row['mean_latency_s']:>7.2f}"
        )
    print(
        f"{'OVERALL':13} {summary['n']:>3} {cell(summary['pass_rate']):>6} "
        f"{cell(summary['intent_accuracy']):>7} {cell(summary['retrieval_hit_rate']):>6} "
        f"{cell(summary['tool_success_rate']):>6} {cell(summary['refusal_correct_rate']):>7} "
        f"{summary['mean_latency_s']:>7.2f}"
    )
    print(
        f"\np95 latency {summary['p95_latency_s']}s | mean {summary['mean_agent_tokens']} "
        f"tokens/conversation | est. {summary['cost_per_100_conversations_usd']} USD "
        f"per 100 conversations | judge cost {summary['judge_cost_total_usd']} USD"
    )

    failures = [r for r in records if not r["pass"]]
    if failures:
        print(f"\nfailures ({len(failures)}):")
        for r in failures:
            failed = [k for k, v in r["checks"].items() if not v]
            print(f"  {r['id']:16} failed {failed} intent={r['intent_actual']}")
            if r.get("error"):
                print(f"                   error: {r['error'][:140]}")
            if r["judge_reason"]:
                print(f"                   judge: {r['judge_reason'][:120]}")
            print(f"                   response: {r['response'][:140]}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--dataset", default="evals/dataset.jsonl")
    args = parser.parse_args()

    cases = [json.loads(line) for line in Path(args.dataset).read_text().splitlines() if line]
    if args.limit:
        cases = cases[: args.limit]

    settings = get_settings()
    tools = ShopifyTools()
    await tools.start()
    graph = build_graph(settings, ChromaVectorStore(), tools)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    sem = asyncio.Semaphore(args.concurrency)

    started = time.perf_counter()
    try:
        records = await asyncio.gather(
            *(evaluate_case(c, graph, client, settings.judge_model, sem) for c in cases)
        )
    finally:
        await tools.aclose()
    wall = time.perf_counter() - started

    records = sorted(records, key=lambda r: r["id"])
    summary = rollup(records)
    print(f"ran {len(records)} cases in {wall:.0f}s")
    print_summary(summary, records)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    out = {
        "label": args.label,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "models": {
            "router": settings.router_model,
            "answer": settings.answer_model,
            "judge": settings.judge_model,
        },
        "summary": summary,
        "cases": records,
    }
    path = RESULTS_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{args.label}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
