"""Run every homepage scenario button against a deployed API with the real model, and check what each one should do.

    python -m evals.scenario_check [--api https://...] [--max-usd 0.05] [--label name]

The button texts are read from the page code, so a new button without a scenario here fails the check.
"""

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

SCENARIOS = Path("evals/scenarios.json")
PAGES = (Path("frontend/app.js"), Path("frontend/index.html"))
ASK_RE = re.compile(r'label: "[^"]+",\s*text: "([^"]+)"|\bask: "([^"]+)"|data-ask="([^"]+)"')
API_RE = re.compile(r'name="api-base" content="([^"]+)"')
OUT = Path("evals/results/scenarios")


def homepage_asks() -> set[str]:
    found = set()
    for page in PAGES:
        for m in ASK_RE.finditer(page.read_text()):
            found.add(html.unescape(m.group(1) or m.group(2) or m.group(3)))
    return found


def missing_scenarios(scenarios: list[dict[str, Any]]) -> set[str]:
    first = {s["turns"][0]["say"] for s in scenarios}
    return homepage_asks() - first


def problems(expect: dict[str, Any], data: dict[str, Any]) -> list[str]:
    found = []
    reply = (data.get("response") or "").lower()
    trace = data.get("trace") or {}
    pending = (data.get("pending") or {}).get("action")
    if "pending" in expect and pending != expect["pending"]:
        found.append(f"pending is {pending}, expected {expect['pending']}")
    for text in expect.get("contains_all", []):
        if text.lower() not in reply:
            found.append(f"reply lacks {text!r}")
    if expect.get("contains_any") and not any(t.lower() in reply for t in expect["contains_any"]):
        found.append(f"reply has none of {expect['contains_any']}")
    for text in expect.get("contains_none", []):
        if text.lower() in reply:
            found.append(f"reply has {text!r}")
    if "intent" in expect and data.get("intent") != expect["intent"]:
        found.append(f"intent is {data.get('intent')}, expected {expect['intent']}")
    if "blocked" in expect and bool(trace.get("blocked")) != expect["blocked"]:
        found.append(f"blocked is {trace.get('blocked')}")
    executed = any(g.get("step") == "executed" for g in trace.get("gate", []))
    if "executed" in expect and executed != expect["executed"]:
        found.append(f"executed is {executed}")
    if expect.get("sources") and not data.get("sources"):
        found.append("no cited passage came back")
    if str(trace.get("verify", "")).startswith("fell back"):
        found.append("the reply fell back to the safe answer")
    return found


def percentile(values: list[float], q: float) -> float:
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def latency_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"turns": 0}
    return {"turns": len(values), "p50_s": percentile(values, 0.5), "p95_s": percentile(values, 0.95), "max_s": values[-1]}


def run_scenario(client: httpx.Client, api: str, scenario: dict[str, Any]) -> dict[str, Any]:
    history: list[dict[str, str]] = []
    token = None
    turns, cost = [], 0.0
    for turn in scenario["turns"]:
        history.append({"role": "user", "content": turn["say"]})
        response = client.post(f"{api}/chat", json={"messages": history, "session_state": token}, timeout=90)
        data = response.json()
        if response.status_code != 200:
            turns.append({"say": turn["say"], "status": response.status_code, "problems": [f"HTTP {response.status_code}: {data.get('error')}"]})
            break
        token = data.get("session_state")
        history.append({"role": "assistant", "content": data["response"]})
        cost += float((data.get("trace") or {}).get("cost_usd") or 0)
        turns.append({
            "say": turn["say"],
            "reply": data["response"],
            "intent": data.get("intent"),
            "pending": (data.get("pending") or {}).get("action"),
            "verify": (data.get("trace") or {}).get("verify"),
            "latency_s": data.get("latency_s"),
            "problems": problems(turn["expect"], data),
        })
    return {"id": scenario["id"], "ok": all(not t["problems"] for t in turns), "cost_usd": round(cost, 6), "turns": turns}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=API_RE.search(Path("frontend/index.html").read_text()).group(1))
    parser.add_argument("--max-usd", type=float, default=0.05)
    parser.add_argument("--label", default="scenarios")
    args = parser.parse_args()
    scenarios = json.loads(SCENARIOS.read_text())
    missing = missing_scenarios(scenarios)
    if missing:
        sys.exit(f"homepage buttons without a scenario: {sorted(missing)}")
    results, spent = [], 0.0
    with httpx.Client() as client:
        for scenario in scenarios:
            if spent >= args.max_usd:
                sys.exit(f"stopped at the {args.max_usd} USD cap after {len(results)} scenarios")
            result = run_scenario(client, args.api.rstrip("/"), scenario)
            spent += result["cost_usd"]
            results.append(result)
            print(f"{'ok  ' if result['ok'] else 'FAIL'} {scenario['id']}" + "".join(f"\n     {p}" for t in result["turns"] for p in t["problems"]))
    passed = sum(r["ok"] for r in results)
    latencies = sorted(t["latency_s"] for r in results for t in r["turns"] if t.get("latency_s") is not None)
    report = {
        "api": args.api, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "passed": passed, "of": len(results),
        "cost_usd": round(spent, 6), "latency": latency_summary(latencies), "results": results,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{datetime.now():%Y%m%d-%H%M%S}_{args.label}.json"
    path.write_text(json.dumps(report, indent=1) + "\n")
    print(f"{passed} of {len(results)} scenarios passed, {spent:.4f} USD, turn latency {report['latency']}, wrote {path}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
