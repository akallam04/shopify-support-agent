"""Send one real chat message to the public demo and fail unless it answers properly.

Uses only the public endpoint named in frontend/index.html, so it needs no secrets.

    python scripts/check_live_demo.py
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

QUESTION = "Do you ship to Canada, and how much does it cost?"
EXPECTED = ("19.95",)
TIMEOUT_S = 60


def api_base() -> str:
    match = re.search(r'name="api-base" content="([^"]+)"', Path("frontend/index.html").read_text())
    if not match:
        raise SystemExit("could not find the api-base meta tag in frontend/index.html")
    return match.group(1).rstrip("/")


def ask(base: str) -> tuple[int, dict]:
    body = json.dumps({"messages": [{"role": "user", "content": QUESTION}]}).encode()
    request = urllib.request.Request(f"{base}/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, {"error": f"could not reach the demo: {getattr(e, 'reason', e)}"}


def main() -> None:
    base = api_base()
    status, data = ask(base)
    if status == 503 and data.get("error") == "model_unavailable":
        sys.exit("the demo is up but the model API is refusing requests (billing, limits, or an outage)")
    answer = str(data.get("response") or "")
    if status != 200 or not answer:
        sys.exit(f"the demo did not answer: HTTP {status} {json.dumps(data)[:300]}")
    if not all(token in answer for token in EXPECTED):
        sys.exit(f"the demo answered without the expected facts {EXPECTED}: {answer[:300]}")
    print(f"ok: HTTP {status}, release {data.get('release', 'unknown')}, {data.get('latency_s')}s")


if __name__ == "__main__":
    main()
