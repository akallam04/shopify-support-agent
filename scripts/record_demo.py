"""Record the tour conversation and the README GIF together, so they always show the same release.

    python -m scripts.record_demo              # one return and a yes on the deployed API, then the GIF
    python -m scripts.record_demo --gif-only   # rebuild the GIF from the saved conversation, no API call

Writes frontend/data/tour.json (the conversation the 60-second tour replays), docs/demo.gif (that
same conversation played on the local site at 1024 by 720), and docs/demo.json (which release and
date the GIF shows). The session token is dropped, since neither the tour nor the GIF sends it.
"""

import argparse
import functools
import io
import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from playwright.sync_api import Page, Route, sync_playwright

FRONTEND = Path("frontend")
TOUR = FRONTEND / "data" / "tour.json"
GIF = Path("docs/demo.gif")
GIF_INFO = Path("docs/demo.json")
API_RE = re.compile(r'name="api-base" content="([^"]+)"')
SAYS = (
    "I want to return the rain jacket from order #1022. It is too big. My email is jordan.lee@example.com.",
    "Yes",
)
VIEW = {"width": 1024, "height": 720}
REPLY_DELAY_MS = 1400
CURSOR_CSS = """
#demo-cursor { position: fixed; z-index: 9999; width: 26px; height: 26px; margin: -13px 0 0 -13px; border: 2px solid #fff;
  border-radius: 50%; background: rgba(19, 32, 42, 0.35); box-shadow: 0 4px 14px rgba(19, 32, 42, 0.35); pointer-events: none;
  transition: left 0.7s cubic-bezier(0.16, 1, 0.3, 1), top 0.7s cubic-bezier(0.16, 1, 0.3, 1), transform 0.15s ease; }
#demo-cursor.is-down { transform: scale(0.75); }
"""


def record(api: str) -> dict[str, Any]:
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
    return {"recorded_on": f"{now:%B} {now.day}, {now.year}", "release": turns[0]["response"]["release"], "turns": turns}


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


@contextmanager
def serve() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(FRONTEND)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


class Frames:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.images: list[Image.Image] = []
        self.durations: list[int] = []

    def grab(self, hold_ms: int) -> None:
        shot = Image.open(io.BytesIO(self.page.screenshot())).convert("RGB").resize((VIEW["width"], VIEW["height"]), Image.Resampling.LANCZOS)
        self.images.append(shot)
        self.durations.append(hold_ms)

    def run(self, total_ms: int, step_ms: int = 120) -> None:
        for _ in range(max(1, total_ms // step_ms)):
            self.page.wait_for_timeout(step_ms)
            self.grab(step_ms)

    def save(self, path: Path) -> None:
        keys = self.images[:: max(1, len(self.images) // 8)]
        strip = Image.new("RGB", (VIEW["width"], VIEW["height"] * len(keys)))
        for i, img in enumerate(keys):
            strip.paste(img, (0, i * VIEW["height"]))
        palette = strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        frames = [img.quantize(palette=palette, dither=Image.Dither.NONE) for img in self.images]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=self.durations, loop=0, optimize=False, disposal=1)


SCROLL_TO_TOP = "(n) => { const log = document.querySelector('#log'); log.scrollTo({ top: log.scrollTop + n.getBoundingClientRect().top - log.getBoundingClientRect().top - 12, behavior: 'smooth' }); }"


def move_cursor(page: Page, frames: Frames, selector: str) -> None:
    box = page.locator(selector).first.bounding_box()
    page.evaluate("([x, y]) => { const c = document.querySelector('#demo-cursor'); c.style.left = x + 'px'; c.style.top = y + 'px'; }", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
    frames.run(720)


def tap(page: Page, frames: Frames, selector: str) -> None:
    move_cursor(page, frames, selector)
    page.evaluate("document.querySelector('#demo-cursor').classList.add('is-down')")
    frames.grab(160)
    page.evaluate("document.querySelector('#demo-cursor').classList.remove('is-down')")
    page.locator(selector).first.click()


def record_gif(conversation: dict[str, Any]) -> int:
    turns = conversation["turns"]
    held: list[Route] = []

    def reply() -> None:
        route = held.pop()
        said = [m for m in route.request.post_data_json["messages"] if m["role"] == "user"]
        route.fulfill(json=turns[len(said) - 1]["response"])

    with serve() as site, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport=VIEW, device_scale_factor=2, reduced_motion="no-preference").new_page()
        page.route("**/health", lambda route: route.fulfill(json={"status": "ok", "release": conversation["release"]}))
        page.route("**/chat", lambda route: held.append(route))
        page.goto(site)
        page.wait_for_timeout(1200)
        page.add_style_tag(content=CURSOR_CSS)
        page.evaluate("const c = document.createElement('div'); c.id = 'demo-cursor'; c.style.left = '300px'; c.style.top = '600px'; document.body.append(c);")
        frames = Frames(page)
        frames.grab(1800)
        tap(page, frames, ".starter:has-text('Return an item')")
        frames.run(REPLY_DELAY_MS)
        reply()
        page.locator(".confirm").first.wait_for()
        frames.run(600)
        frames.grab(2600)
        tap(page, frames, ".confirm__actions .btn--primary")
        frames.run(REPLY_DELAY_MS)
        reply()
        page.locator(".outcome--done").wait_for()
        frames.run(600)
        frames.grab(2600)
        tap(page, frames, ".msg:has(.outcome--done) .rail")
        page.wait_for_timeout(300)
        page.locator(".msg:has(.outcome--done) .brain").evaluate(SCROLL_TO_TOP)
        frames.run(700)
        frames.grab(3200)
        browser.close()
    frames.save(GIF)
    return len(frames.images)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gif-only", action="store_true")
    args = parser.parse_args()
    if args.gif_only:
        conversation = json.loads(TOUR.read_text())
    else:
        api = API_RE.search((FRONTEND / "index.html").read_text()).group(1).rstrip("/")
        conversation = record(api)
        TOUR.write_text(json.dumps(conversation, indent=1) + "\n")
        print(f"wrote {TOUR}")
    count = record_gif(conversation)
    info = {"release": conversation["release"], "recorded_on": conversation["recorded_on"], "conversation": str(TOUR), "frames": count}
    GIF_INFO.write_text(json.dumps(info, indent=1) + "\n")
    print(f"wrote {GIF} ({count} frames, {GIF.stat().st_size // 1024} KB) and {GIF_INFO}")


if __name__ == "__main__":
    main()
