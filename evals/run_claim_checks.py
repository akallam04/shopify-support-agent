"""Run the unsupported-claim set through the verify checks, offline, and score two things apart.

Citation validity: does the citation check pass real citations and catch invented or missing ones?
Claim correctness: do the other checks catch claims the sources do not support, and pass the ones they do?
"""

import argparse
import json
from pathlib import Path
from typing import Any

from app.agent.nodes.verify import findings

CASES = Path("evals/claim_checks.json")


def case_state(case: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    retrieved = [{"id": i, **data["passages"][i]} for i in case.get("retrieved", [])]
    return {
        "messages": case["messages"],
        "intent": case["intent"],
        "retrieved": retrieved,
        "tool_results": [data["tools"][name] for name in case.get("tools", [])],
        "order_number": "#" if case.get("tools") else "",
        **case.get("state", {}),
    }


def score(case: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    checks = findings(case["draft"], case_state(case, data))
    failed = [c.name for c in checks if not c.ok]
    row: dict[str, Any] = {"id": case["id"], "kind": case["kind"], "failed": failed}
    if case.get("citation"):
        caught = "citations" in failed
        row["citation"] = case["citation"]
        row["citation_right"] = caught == (case["citation"] != "valid")
    flagged = any(name != "citations" for name in failed)
    row["claim"] = case["claim"]
    row["claim_right"] = flagged == (case["claim"] == "unsupported")
    return row


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cited = [r for r in rows if "citation" in r]
    bad_claims = [r for r in rows if r["claim"] == "unsupported"]
    good_claims = [r for r in rows if r["claim"] == "supported"]
    return {
        "cases": len(rows),
        "citation_validity": {
            "right": sum(r["citation_right"] for r in cited),
            "of": len(cited),
            "missed": [r["id"] for r in cited if not r["citation_right"]],
        },
        "claim_correctness": {
            "unsupported_caught": sum(r["claim_right"] for r in bad_claims),
            "unsupported": len(bad_claims),
            "supported_passed": sum(r["claim_right"] for r in good_claims),
            "supported": len(good_claims),
            "missed": [r["id"] for r in bad_claims if not r["claim_right"]],
            "wrongly_flagged": [r["id"] for r in good_claims if not r["claim_right"]],
        },
    }


def run() -> dict[str, Any]:
    data = json.loads(CASES.read_text())
    rows = [score(case, data) for case in data["cases"]]
    return {"summary": summary(rows), "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write the per-case results to this JSON file")
    args = parser.parse_args()
    report = run()
    print(json.dumps(report["summary"], indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
