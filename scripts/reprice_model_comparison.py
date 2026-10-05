"""Reprices the saved July Haiku and Sonnet 5 eval runs at current model prices, without a new run.

The saved records keep each case's total tokens and cost but not the split by model, so the
script separates the Haiku router's share. Router-only cases (static refusal paths) pin the
router's input and output tokens exactly from their cost and token totals; the router's input
for every other case is counted with the free token-counting endpoint using the router prompt
from the commit the runs were made at. Sonnet 5's price fell by the same third on input and
output, so its share of each case scales by exactly 2/3. Token counting needs ANTHROPIC_API_KEY.

Run from the repo root: .venv/bin/python -m scripts.reprice_model_comparison
"""

import json
import re
import statistics
import subprocess
import types

import anthropic

from app.config import get_settings

HAIKU_RUN = "evals/results/20260707-203600_after-iteration.json"
SONNET_RUN = "evals/results/20260707-203804_sonnet-comparison.json"
JULY_SONNET = (3.00, 15.00)
CURRENT_SONNET = (2.00, 10.00)
HAIKU = (1.00, 5.00)
STATIC_INTENTS = {"handoff", "out_of_scope", "injection"}


def source_at(commit: str, path: str) -> str:
    return subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, text=True, check=True).stdout


def main() -> None:
    haiku_run, sonnet_run = json.load(open(HAIKU_RUN)), json.load(open(SONNET_RUN))
    commit = sonnet_run["git_commit"]
    prompts = types.ModuleType("prompts_at_commit")
    exec(compile(source_at(commit, "app/agent/prompts.py"), "prompts.py", "exec"), prompts.__dict__)
    sanitize = source_at(commit, "app/agent/nodes/sanitize.py")
    patterns = [re.compile(p, re.IGNORECASE) for p in re.findall(r'r"([^"]+)"', sanitize.split("INJECTION_PATTERNS")[1].split("]")[0])]
    dataset = {json.loads(line)["id"]: json.loads(line) for line in open("evals/dataset.jsonl")}

    scale = CURRENT_SONNET[0] / JULY_SONNET[0]
    assert scale == CURRENT_SONNET[1] / JULY_SONNET[1], "the price change is not uniform, this method does not apply"

    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)
    router_in: dict[str, int] = {}
    for case in sonnet_run["cases"]:
        messages = dataset[case["id"]]["messages"]
        hard_injection = any(p.search(messages[-1]["content"]) for p in patterns)
        router_in[case["id"]] = 0 if hard_injection else client.messages.count_tokens(
            model="claude-haiku-4-5", system=prompts.ROUTER_SYSTEM, messages=messages
        ).input_tokens

    calibration = []
    for case in sonnet_run["cases"]:
        if case["intent_actual"] in STATIC_INTENTS and router_in[case["id"]]:
            micro = case["agent_cost_usd"] * 1e6
            out = (micro - case["agent_tokens"]) / (HAIKU[1] / HAIKU[0] - 1)
            calibration.append((case["agent_tokens"] - out - router_in[case["id"]], out))
    overhead = statistics.mean(o for o, _ in calibration)
    router_out = statistics.mean(r for _, r in calibration)

    repriced = []
    for case in sonnet_run["cases"]:
        if case["intent_actual"] in STATIC_INTENTS:
            repriced.append(case["agent_cost_usd"])
            continue
        tokens_in = router_in[case["id"]]
        router_cost = ((tokens_in + overhead) * HAIKU[0] + router_out * HAIKU[1]) / 1e6 if tokens_in else 0.0
        sonnet_share = max(case["agent_cost_usd"] - router_cost, 0.0)
        repriced.append(router_cost + sonnet_share * scale)

    haiku = statistics.mean(c["agent_cost_usd"] for c in haiku_run["cases"])
    july = statistics.mean(c["agent_cost_usd"] for c in sonnet_run["cases"])
    current = statistics.mean(repriced)
    print(f"router calibration from {len(calibration)} router-only cases: {overhead:.0f} tokens of input overhead, {router_out:.0f} output tokens")
    print(f"haiku 4.5:                   ${haiku * 100:.2f} per 100 conversations")
    print(f"sonnet 5 at july prices:     ${july * 100:.2f} per 100 conversations, {july / haiku:.2f}x haiku")
    print(f"sonnet 5 at current prices:  ${current * 100:.2f} per 100 conversations, {current / haiku:.2f}x haiku")


if __name__ == "__main__":
    main()
