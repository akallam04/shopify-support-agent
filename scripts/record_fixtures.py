"""Record the replies the browser tests and the UI audit replay, from the deployed API.

    python -m scripts.record_fixtures

Writes tests/e2e/fixtures.json: four short conversations (a cancel then no, a return then yes, a
cancel then a question, and a tracking request). The session token is dropped from each reply.
"""

import json
import re
from pathlib import Path
from typing import Any

import httpx

OUT = Path("tests/e2e/fixtures.json")
API_RE = re.compile(r'name="api-base" content="([^"]+)"')
CANCEL = "Please cancel order #1023. My email is maya.thompson@example.com. I ordered it by mistake."
FLOWS = {
    "cancel-decline": [CANCEL, "No"],
    "return-confirm": ["I want to return the rain jacket from order #1022. It is too big. My email is jordan.lee@example.com.", "Yes"],
    "cancel-question": [CANCEL, "Before I decide, what is your return policy?"],
    "track": ["Where is my order #1001? My email is maya.thompson@example.com"],
}


def record_flow(client: httpx.Client, api: str, flow: str, says: list[str]) -> list[dict[str, Any]]:
    history: list[dict[str, str]] = []
    token = None
    out = []
    for turn, say in enumerate(says, start=1):
        history.append({"role": "user", "content": say})
        data = client.post(f"{api}/chat", json={"messages": history, "session_state": token}).json()
        token = data.get("session_state")
        history.append({"role": "assistant", "content": data["response"]})
        out.append({"flow": flow, "user_turn": turn, "last": say, "response": {**data, "session_state": None}})
    return out


def main() -> None:
    api = API_RE.search(Path("frontend/index.html").read_text()).group(1).rstrip("/")
    with httpx.Client(timeout=90) as client:
        fixtures = [f for flow, says in FLOWS.items() for f in record_flow(client, api, flow, says)]
    OUT.write_text(json.dumps(fixtures, indent=1) + "\n")
    print(f"wrote {len(fixtures)} replies to {OUT}")


if __name__ == "__main__":
    main()
