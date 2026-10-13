"""Record the conversation the 60-second tour replays: one real return through the gate, from the deployed API.

    python -m scripts.record_tour

Writes frontend/data/tour.json. The session token is replaced, since the tour never sends it anywhere.
"""

import json
import re
from datetime import datetime
from pathlib import Path

import httpx

OUT = Path("frontend/data/tour.json")
API_RE = re.compile(r'name="api-base" content="([^"]+)"')
SAYS = (
    "I want to return the rain jacket from order #1022. It is too big. My email is jordan.lee@example.com.",
    "Yes",
)


def main() -> None:
    api = API_RE.search(Path("frontend/index.html").read_text()).group(1).rstrip("/")
    history: list[dict[str, str]] = []
    token = None
    turns = []
    with httpx.Client(timeout=90) as client:
        for say in SAYS:
            history.append({"role": "user", "content": say})
            data = client.post(f"{api}/chat", json={"messages": history, "session_state": token}).json()
            token = data.get("session_state")
            history.append({"role": "assistant", "content": data["response"]})
            turns.append({"user": say, "response": {**data, "session_state": None}})
    if not turns[0]["response"].get("pending") or not any(g.get("step") == "executed" for g in turns[1]["response"]["trace"]["gate"]):
        raise SystemExit("the recorded conversation did not go through the card and the yes")
    now = datetime.now()
    OUT.write_text(json.dumps({"recorded_on": f"{now:%B} {now.day}, {now.year}", "release": turns[0]["response"]["release"], "turns": turns}, indent=1) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
