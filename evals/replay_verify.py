"""Replay saved simulation replies through the verify checks offline, with no model calls.

Each saved turn is rebuilt into the state verify sees: the conversation so far, the turn's tool
results, lookups carried from earlier turns, receipts, any pending change, and the passages that
retrieval returns for the question (plus every passage the reply cites). Store-built text such as
confirmation cards and receipts is skipped, as it is live. The output lists every reply the checks
would flag, for review.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import chromadb

from app.agent import prompts
from app.agent.claims import claims_in
from app.agent.facts import merge_facts
from app.agent.nodes.retrieve import companion_ids
from app.agent.nodes.verify import BRACKET_RE, RECONFIRM_MARK, SYSTEM_START_RE, UNCHECKED_INTENTS, findings
from app.config import get_settings

RUNS = Path("evals/results/sim")
WRITE_NAMES = ("cancel_order", "update_shipping_address", "request_return", "transfer_to_human")
STATIC = (
    prompts.DECLINED_RESPONSE, prompts.ADDRESS_ASK, prompts.REASON_ASK, prompts.GATE_INPUT_RESPONSE,
    prompts.HANDOFF_RESPONSE, prompts.HANDOFF_FOLLOWUP, prompts.HANDOFF_DEMO, prompts.HANDOFF_DEMO_FOLLOWUP,
    prompts.ORDER_NUMBER_HELP, prompts.ORDER_NUMBER_WHERE, prompts.INJECTION_RESPONSE, prompts.OUT_OF_SCOPE_RESPONSE,
    prompts.SAFE_FALLBACK_RESPONSE,
)
CONFIRM_RE = re.compile(r"I will (.+?) Should I go ahead\?", re.S)
# removed after the headline runs: its results stayed in the model's context for the rest of the chat
LISTING_TOOL = "list_customer_orders"


class Passages:
    def __init__(self, path: str) -> None:
        client = chromadb.PersistentClient(path=path)
        self.collections = {name: client.get_collection(name) for name in ("catalog", "policies")}
        self.by_key: dict[str, dict[str, Any]] = {}
        for name, collection in self.collections.items():
            got = collection.get()
            for i, doc_id in enumerate(got["ids"]):
                doc = {"id": doc_id, "text": got["documents"][i], "metadata": got["metadatas"][i] or {}}
                self.by_key[doc_id] = doc
                if doc["metadata"].get("handle"):
                    self.by_key[doc["metadata"]["handle"]] = doc

    def for_turn(self, intent: str, question: str, reply: str) -> list[dict[str, Any]]:
        name = "catalog" if intent == "product" else "policies"
        res = self.collections[name].query(query_texts=[question], n_results=4)
        docs = [{"id": res["ids"][0][i], "text": res["documents"][0][i], "metadata": res["metadatas"][0][i] or {}} for i in range(len(res["ids"][0]))]
        if name == "policies":
            docs += [self.by_key[i] for i in companion_ids(docs, question) if i in self.by_key]
        docs += [self.by_key[i] for i in BRACKET_RE.findall(reply) if i in self.by_key]
        return list({d["id"]: d for d in docs}.values())


def model_part(reply: str, calls: list[dict[str, Any]]) -> str | None:
    text = reply.strip()
    if text in STATIC or SYSTEM_START_RE.match(text):
        return None
    refusals = [json.loads(c["result"]).get("reason", "") for c in calls if c["name"].startswith("prepare:")]
    if any(r and text.startswith(r.strip()[:60]) for r in refusals):
        return None
    at = text.find(RECONFIRM_MARK)
    return text[:at].strip() if at > 0 else (None if at == 0 else text)


def parsed(result: str) -> Any:
    try:
        return json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return None


def turn_states(row: dict[str, Any]) -> list[tuple[int, dict[str, Any], str]]:
    transcript, turns = row["transcript"], row.get("turns", [])
    facts: list[dict[str, Any]] = []
    executed: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    listed: list[dict[str, Any]] = []
    out = []
    for i, turn in enumerate(turns):
        calls = turn.get("tool_calls", [])
        messages = transcript[: 2 * i + 1]
        hidden = next((parsed(c["result"]) for c in calls if c["name"] == "pending_action"), None)
        listed += [c for c in calls if c["name"] == LISTING_TOOL]
        state = {
            "messages": messages,
            "intent": turn.get("intent", ""),
            "tool_results": [c for c in listed if c not in calls] + calls,
            "listing_tool": bool(listed),
            "verified_facts": facts,
            "executed_actions": list(executed),
            "pending_action": hidden or pending,
            "order_number": "#" if any(re.search(r"#?\d{4}", str(m["content"])) for m in messages if m["role"] == "user") else "",
        }
        out.append((i, state, turn.get("agent", "")))
        facts = merge_facts(facts, calls)
        for c in calls:
            result = parsed(c["result"])
            if c["name"] in WRITE_NAMES and isinstance(result, dict) and result.get("ok"):
                executed.append(result)
        match = CONFIRM_RE.search(turn.get("agent", ""))
        pending = {"summary": match.group(1), "checks": []} if turn.get("pending_after") and match else None
    return out


def suite_states(result_file: Path) -> list[tuple[str, dict[str, Any], str]]:
    cases = {c["id"]: c for c in map(json.loads, Path("evals/dataset.jsonl").read_text().splitlines())}
    out = []
    for case in json.loads(result_file.read_text())["cases"]:
        intent = case.get("intent_actual", "")
        if intent in ("product", "policy", "smalltalk") and case["id"] in cases:
            state = {"messages": cases[case["id"]]["messages"], "intent": intent, "tool_results": [], "order_number": ""}
            out.append((case["id"], state, case.get("response", "")))
    return out


def replay(run_dirs: list[Path], passages: Passages, suite: Path | None = None) -> dict[str, Any]:
    checked = flagged = skipped = claims = 0
    by_check: Counter[str] = Counter()
    flags = []
    if suite is not None:
        for case_id, state, reply in suite_states(suite):
            draft = model_part(reply, [])
            if draft is None:
                skipped += 1
                continue
            if state["intent"] != "smalltalk":
                state["retrieved"] = passages.for_turn(state["intent"], str(state["messages"][-1]["content"]), draft)
            checks = findings(draft, state)
            checked += 1
            claims += len(claims_in(draft))
            failed = [c for c in checks if not c.ok]
            if failed:
                flagged += 1
                by_check.update(c.name for c in failed)
                flags.append({"suite_case": case_id, "intent": state["intent"], "listing_tool": False,
                              "failed": [{"check": c.name, "detail": c.detail, "feedback": c.feedback} for c in failed], "reply": draft})
    for run in run_dirs:
        for line in (run / "trajectories.jsonl").open():
            row = json.loads(line)
            for index, state, reply in turn_states(row):
                draft = model_part(reply, state["tool_results"])
                if draft is None or state["intent"] in UNCHECKED_INTENTS or not state["intent"]:
                    skipped += 1
                    continue
                if state["intent"] in ("product", "policy"):
                    state["retrieved"] = passages.for_turn(state["intent"], str(state["messages"][-1]["content"]), draft)
                checks = findings(draft, state)
                checked += 1
                claims += len(claims_in(draft))
                failed = [c for c in checks if not c.ok]
                if not failed:
                    continue
                flagged += 1
                by_check.update(c.name for c in failed)
                flags.append({
                    "run": run.name, "task": row["task_id"], "trial": row["trial"], "turn": index, "listing_tool": state["listing_tool"],
                    "reward": row.get("grade", {}).get("reward"), "intent": state["intent"],
                    "failed": [{"check": c.name, "detail": c.detail, "feedback": c.feedback} for c in failed],
                    "reply": draft,
                })
    listing = sum(1 for f in flags if f["listing_tool"])
    return {
        "checked": checked, "numbers_checked": claims, "skipped_store_built_or_fixed": skipped, "flagged": flagged,
        "flagged_using_removed_listing_tool": listing, "by_check": dict(by_check), "flags": flags,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="*", help="run folder names under evals/results/sim, default all")
    parser.add_argument("--suite", help="also replay the product, policy and small talk answers of this 53-case result file")
    parser.add_argument("--out", help="write the flagged replies to this JSON file")
    args = parser.parse_args()
    dirs = sorted(p for p in RUNS.iterdir() if (p / "trajectories.jsonl").exists())
    if args.runs:
        dirs = [d for d in dirs if d.name in set(args.runs)]
    report = replay(dirs, Passages(get_settings().chroma_path), Path(args.suite) if args.suite else None)
    print(json.dumps({k: v for k, v in report.items() if k != "flags"}, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
