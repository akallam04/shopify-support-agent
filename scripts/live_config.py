"""Read the release manifest of the deployed API (models, thinking, gate, release) and write it where the README and site show it.

    python -m scripts.live_config            # fetch /release, write frontend/data/live.json and the README box
    python -m scripts.live_config --check    # fail if the README box does not match frontend/data/live.json
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

LIVE = Path("frontend/data/live.json")
README = Path("README.md")
START, END = "<!-- live-config:start -->", "<!-- live-config:end -->"
API_RE = re.compile(r'name="api-base" content="([^"]+)"')
MODEL_NAMES = {"claude-haiku-5-5": "Claude Haiku 5.5", "claude-haiku-4-5": "Claude Haiku 4.5", "claude-sonnet-5-5": "Claude Sonnet 5.5"}


def model_name(model: str) -> str:
    return MODEL_NAMES.get(model, model)


def on_off(value: bool) -> str:
    return "on" if value else "off"


def render(live: dict[str, Any]) -> str:
    models = live["models"]
    same = models["router"] == models["answer"]
    model_line = f"{model_name(models['answer'])} for routing and answers" if same else f"{model_name(models['router'])} routes, {model_name(models['answer'])} answers"
    gate = live["gate"]
    return "\n".join([
        START,
        f"> **What's live now**, read from the deployed API on {live['checked_on']}",
        ">",
        f"> - **Model:** {model_line}, thinking {on_off(live['thinking'])}",
        f"> - **Gate:** {on_off(gate['mutation_gate'])}, confirmation {on_off(gate['confirmation'])}, reflection {on_off(gate['reflection'])}; write actions {on_off(gate['write_actions'])}",
        f"> - **Release:** `{live['release']}` from commit `{live['git_sha'][:7]}`",
        END,
    ])


def readme_box(text: str) -> str:
    start, end = text.find(START), text.find(END)
    return text[start:end + len(END)] if start >= 0 and end > start else ""


def fetch(api: str) -> dict[str, Any]:
    body = httpx.get(f"{api.rstrip('/')}/release", timeout=60).json()
    keep = ("release", "git_sha", "prompts", "knowledge", "models", "thinking", "gate")
    return {**{k: body[k] for k in keep}, "checked_on": f"{datetime.now():%B} {datetime.now().day}, {datetime.now().year}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--api", default=API_RE.search(Path("frontend/index.html").read_text()).group(1))
    args = parser.parse_args()
    if args.check:
        ok = readme_box(README.read_text()) == render(json.loads(LIVE.read_text()))
        sys.exit(0 if ok else "the README box does not match frontend/data/live.json")
    live = fetch(args.api)
    LIVE.write_text(json.dumps(live, indent=1) + "\n")
    text = README.read_text()
    box = readme_box(text)
    if not box:
        sys.exit(f"add {START} and {END} to the README first")
    README.write_text(text.replace(box, render(live)))
    print(render(live))


if __name__ == "__main__":
    main()
