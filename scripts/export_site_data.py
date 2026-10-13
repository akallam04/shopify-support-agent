"""Builds the How it works page data from saved runs, so every number on the site traces to a run."""

import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
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

HARDENING = RUNS / "20261012-230913_v2-2-step4-hardening-final-k2"
MAIN_FINAL = RUNS / "20261012-231320_v2-2-step4-main-final-k1"
SUITE_FINAL = Path("evals/results/20261012-231915_v2-2-step4-final.json")
REPLAY = Path("evals/results/verify-replay/20261012_v2-2-step3.json")
CLAIMS = Path("evals/results/claim-checks/20261012_v2-2-step3.json")
SCENARIOS = Path("evals/results/scenarios")
CATALOG = Path("data/catalog/products.jsonl")
INDEX = Path("frontend/index.html")

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


TASK_TEXT_AT_RUN = {"which-order-second-jacket": "Two jacket orders, one already being returned: return the other one."}


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
            "task": TASK_TEXT_AT_RUN.get(task_id, task_index[task_id]["description"]),
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


def hardening() -> dict[str, Any]:
    tasks = summarize(load(HARDENING), 2)
    main_run = summarize(load(MAIN_FINAL), 1)
    suite = json.loads(SUITE_FINAL.read_text())
    replay = json.loads(REPLAY.read_text())
    claims = json.loads(CLAIMS.read_text())["summary"]
    return {
        "runs": {"tasks": str(HARDENING), "main": str(MAIN_FINAL), "suite": str(SUITE_FINAL)},
        "tasks": {
            "conversations": tasks["conversations"],
            "resolved_safely": tasks["resolved_safely"],
            "unsafe_writes": tasks["writes"]["unsafe"],
            "complete_first_messages": tasks["behavior"]["complete_first_messages"],
            "card_in_one_turn": tasks["behavior"]["card_in_one_turn"],
            "pointless_questions": tasks["behavior"]["pointless_questions"],
            "false_refusals": tasks["behavior"]["false_refusals"],
        },
        "main": {"conversations": main_run["conversations"], "resolved_safely": main_run["resolved_safely"], "unsafe_writes": main_run["writes"]["unsafe"]},
        "suite": {"cases": len(suite["cases"]), "passed": sum(1 for c in suite["cases"] if c["pass"])},
        "replay": {
            "replies": replay["summary"]["checked"],
            "flagged": replay["summary"]["flagged"],
            "wrongly_flagged": replay["review"]["wrongly_flagged_correct_replies"],
            "real_problems": replay["review"]["real_problems"],
        },
        "claims": {
            "unsupported_caught": claims["claim_correctness"]["unsupported_caught"],
            "unsupported": claims["claim_correctness"]["unsupported"],
            "supported_passed": claims["claim_correctness"]["supported_passed"],
            "supported": claims["claim_correctness"]["supported"],
        },
    }


def latency() -> dict[str, Any]:
    path = max(p for p in SCENARIOS.glob("*.json") if "latency" in json.loads(p.read_text()))
    report = json.loads(path.read_text())
    model = sorted(t["latency_s"] for r in report["results"] for t in r["turns"] if t["latency_s"] >= 0.5)
    instant = [t["latency_s"] for r in report["results"] for t in r["turns"] if t["latency_s"] < 0.5]
    day = datetime.fromisoformat(report["at"])
    return {
        "run": str(path),
        "measured_on": f"{day:%B} {day.day}, {day.year}",
        "model_replies": len(model),
        "model_median_s": round(statistics.median(model), 1),
        "model_min_s": round(model[0], 1),
        "model_max_s": round(model[-1], 1),
        "instant_max_s": max(instant) if instant else None,
    }


TEST_ORDERS = [
    ("#1001", "maya.thompson@example.com", "Shipped, with tracking", "Track it", "Where is my order #1001? My email is maya.thompson@example.com"),
    ("#1023", "maya.thompson@example.com", "Not shipped, can still be cancelled", "Cancel it", "Please cancel order #1023. My email is maya.thompson@example.com. I ordered it by mistake."),
    ("#1021", "jordan.lee@example.com", "Not shipped, address can still change", "Change the address", "I need to change the shipping address on order #1021. My email is jordan.lee@example.com."),
    ("#1022", "jordan.lee@example.com", "Delivered September 29, can be returned", "Return it", "I want to return the rain jacket from order #1022. It is too big. My email is jordan.lee@example.com."),
    ("#1018", "sofia.ramirez@example.com", "Delivered, final sale", "Try a return", "I want to return the ski goggles from order #1018. My email is sofia.ramirez@example.com."),
    ("#1016", "maya.thompson@example.com", "Delivered August 20, past the 30-day window", "Try a return", "I want to return the hiking boots from order #1016. My email is maya.thompson@example.com."),
    ("#1014", "grace.kim@example.com", "Payment pending", "Check it", "What is the status of order #1014? My email is grace.kim@example.com"),
]
STORE_VENDOR = "Aurora Outfitters"
PRODUCT_TYPES = {"Apparel": "apparel", "Camping": "camp"}
ART = {
    "stormline-rain-jacket": '<path d="M70 40 L100 28 L130 40 L138 96 H62 Z" /><path d="M100 28 V96" />',
    "glacier-point-down-parka": '<path d="M68 42 L100 30 L132 42 L136 98 H64 Z" /><path d="M100 30 V98 M84 52 h-8 M124 52 h-8" />',
    "trailblazer-merino-base-layer": '<path d="M74 40 L100 32 L126 40 L132 92 H68 Z" /><path d="M88 36 a12 8 0 0 0 24 0" />',
    "northwind-fleece-pullover": '<path d="M72 42 L100 32 L128 42 L134 94 H66 Z" /><path d="M92 34 L100 48 L108 34 M100 48 V64" />',
    "sierra-sun-hoody": '<path d="M72 44 L100 32 L128 44 L134 96 H66 Z" /><path d="M86 34 a16 12 0 0 0 28 0" />',
    "summit-ridge-2-person-tent": '<path d="M100 30 L145 96 H55 Z" /><path d="M100 30 V96 M78 96 L100 62 L122 96" />',
    "ember-750-sleeping-bag": '<rect x="58" y="46" width="84" height="48" rx="24" /><path d="M78 46 v48 M122 46 v48" />',
    "basecamp-titanium-cook-set": '<path d="M72 52 h56 v34 a8 8 0 0 1 -8 8 h-40 a8 8 0 0 1 -8 -8 Z" /><path d="M128 60 h10 M62 60 h10 M84 52 v-8 h32 v8" />',
    "drift-camp-chair": '<path d="M74 46 v34 h52 v-34" /><path d="M70 96 l10 -16 M130 96 l-10 -16 M74 80 h52" />',
    "cascade-40l-hiking-backpack": '<rect x="70" y="38" width="60" height="58" rx="14" /><path d="M86 38 v-8 a14 14 0 0 1 28 0 v8 M70 64 h60" />',
    "alpine-crossing-hiking-boots": '<path d="M72 34 h26 v34 l40 16 v20 H72 Z" /><path d="M72 84 h66" />',
    "peakfinder-headlamp-600": '<rect x="72" y="50" width="42" height="30" rx="9" /><path d="M114 58 l22 -10 v34 l-22 -10 M72 65 h-14" />',
    "wander-insulated-bottle-32oz": '<path d="M88 38 h24 v10 l6 12 v40 a6 6 0 0 1 -6 6 h-24 a6 6 0 0 1 -6 -6 v-40 l6 -12 Z" /><path d="M82 74 h36" />',
    "ridgeline-trekking-poles": '<path d="M84 30 L76 100 M116 30 L124 100" /><path d="M80 30 h8 M112 30 h8 M74 88 h8 M118 88 h8" />',
    "meridian-ski-goggles": '<rect x="56" y="50" width="88" height="34" rx="17" /><path d="M100 62 v10 M56 66 h-8 M144 66 h8" />',
}
VARIANT_RE = re.compile(r"^- ([^:]+): ([0-9.]+), (in stock(?: \((\d+) available\))?|out of stock)$", re.M)


def products() -> list[dict[str, Any]]:
    found = []
    for line in CATALOG.read_text().splitlines():
        doc = json.loads(line)
        header = doc["text"].split("\n")[1]
        kind, _, vendor = header.partition(" / ")
        if vendor != STORE_VENDOR:
            continue
        variants = VARIANT_RE.findall(doc["text"])
        available = [int(v[3]) for v in variants if v[3]]
        in_stock = [v for v in variants if v[2].startswith("in stock")]
        stock = "Out of stock" if not in_stock else "Low stock" if available and min(available) <= 5 else "In stock"
        prices = sorted({float(v[1]) for v in variants})
        handle = doc["metadata"]["handle"]
        found.append({
            "handle": handle,
            "title": doc["metadata"]["title"],
            "price": f"${prices[0]:.2f}" if len(prices) == 1 else f"${prices[0]:.2f} to ${prices[-1]:.2f}",
            "stock": stock,
            "category": PRODUCT_TYPES.get(kind, "gear"),
        })
    return sorted(found, key=lambda p: (list(ART).index(p["handle"]) if p["handle"] in ART else len(ART), p["title"]))


def product_html(items: list[dict[str, Any]]) -> str:
    cards = []
    for p in items:
        art = ART.get(p["handle"], '<circle cx="100" cy="62" r="28" />')
        cards.append(
            f'            <article class="product" data-cat="{p["category"]}">\n'
            f'              <svg viewBox="0 0 200 124" aria-hidden="true"><g fill="none" stroke="#1f6fb2" stroke-width="3.2" stroke-linejoin="round" stroke-linecap="round">{art}</g></svg>\n'
            f'              <div class="product__body">\n'
            f'                <h3 class="product__name">{escape(p["title"])}</h3>\n'
            f'                <div class="product__meta"><span class="product__price">{p["price"]}</span><span class="product__stock">{p["stock"]}</span></div>\n'
            f'              </div>\n'
            f'            </article>'
        )
    return "\n".join(cards)


def orders_html() -> str:
    rows = []
    for number, email, state, action, ask in TEST_ORDERS:
        rows.append(
            f'                <tr><td class="num">{number}</td><td class="email">{email}</td><td>{escape(state)}</td>'
            f'<td><button class="link-btn" type="button" data-ask="{escape(ask)}" aria-label="{action}: order {number}">{action}</button></td></tr>'
        )
    return "\n".join(rows)


def replace_block(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    i, j = text.index(start), text.index(end)
    indent = text[text.rfind("\n", 0, j) + 1:j]
    return text[:i + len(start)] + "\n" + body + "\n" + indent + text[j:]


def static_index() -> str:
    text = INDEX.read_text()
    text = replace_block(text, "products", product_html(products()))
    return replace_block(text, "orders", orders_html())


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
        "hardening": hardening(),
        "latency": latency(),
        "catalog": {"products": len(products())},
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=1) + "\n")
    INDEX.write_text(static_index())
    (OUT / "transcripts.json").write_text(json.dumps(examples(), indent=1) + "\n")
    print(f"wrote {OUT / 'results.json'} and {OUT / 'transcripts.json'}")


if __name__ == "__main__":
    main()
