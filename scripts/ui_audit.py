"""Screenshot every page and state of the site at six widths and report layout problems.

    python -m scripts.ui_audit [--out audit-screenshots] [--widths 320 375] [--fail]

Chat replies come from tests/e2e/fixtures.json, so nothing reaches the API. Writes one PNG per state
and width, a contact sheet per state with every width side by side, and report.json with each
problem found: sideways scroll, text outside its box, clipped text, overlapping chat parts, and tables
wider than their box. With --fail it exits with an error when any problem is found, which is how CI runs it.
"""

import argparse
import functools
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PIL import Image
from playwright.sync_api import Browser, Page, sync_playwright

FRONTEND = Path("frontend")
FIXTURES = json.loads(Path("tests/e2e/fixtures.json").read_text())
WIDTHS = (320, 375, 390, 768, 1024, 1440)
HEIGHTS = {320: 568, 375: 812, 390: 844, 768: 1024, 1024: 768, 1440: 900}
HEALTH = {"status": "ok", "release": "audit"}
FAILED = {"error": "internal", "message": "That reply did not come through.", "release": "audit"}

CHECK_JS = """
() => {
  const W = innerWidth, problems = [];
  const name = (n) => n.id ? `#${n.id}` : `${n.tagName.toLowerCase()}.${[...n.classList].join(".")}`;
  const text = (n) => (n.innerText || "").trim().slice(0, 60);
  if (document.documentElement.scrollWidth > W + 1) problems.push({ kind: "sideways scroll", detail: `${document.documentElement.scrollWidth}px wide` });
  const scrollsX = (n) => {
    for (let p = n.parentElement; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/(auto|scroll|hidden|clip)/.test(s.overflowX) && p !== document.body && p !== document.documentElement) return true;
    }
    return false;
  };
  for (const n of document.querySelectorAll("body *")) {
    if (!n.checkVisibility({ opacityProperty: true, visibilityProperty: true })) continue;
    if (n.closest(".sr-only, svg")) continue;
    const r = n.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    const own = [...n.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim());
    if ((r.right > W + 1 || r.left < -1) && !scrollsX(n)) problems.push({ kind: "outside the screen", detail: `${name(n)} ${Math.round(r.left)} to ${Math.round(r.right)}`, text: text(n) });
    if (!own || !n.clientWidth) continue;
    const s = getComputedStyle(n);
    if (n.scrollWidth > n.clientWidth + 1 && s.textOverflow !== "ellipsis" && s.overflowX !== "auto" && s.overflowX !== "scroll") {
      problems.push({ kind: /(hidden|clip)/.test(s.overflowX) ? "clipped text" : "text outside its box", detail: `${name(n)} ${n.scrollWidth} > ${n.clientWidth}`, text: text(n) });
    }
  }
  for (const box of document.querySelectorAll(".table-scroll")) {
    if (box.scrollWidth > box.clientWidth + 1) problems.push({ kind: "table wider than its box", detail: `${box.scrollWidth} > ${box.clientWidth}`, text: text(box) });
  }
  const chat = document.querySelector("#chat.is-open");
  if (chat) {
    const parts = [...chat.children].filter((c) => c.checkVisibility()).map((c) => [name(c), c.getBoundingClientRect()]);
    for (let i = 0; i < parts.length; i++) for (let j = i + 1; j < parts.length; j++) {
      const [a, ra] = parts[i], [b, rb] = parts[j];
      const overlap = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (overlap > 1 && Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left) > 1) problems.push({ kind: "overlap", detail: `${a} and ${b} by ${Math.round(overlap)}px` });
    }
    const launcher = document.querySelector("#launcher");
    if (launcher.checkVisibility()) {
      const l = launcher.getBoundingClientRect(), c = chat.getBoundingClientRect();
      if (l.right > c.left && l.left < c.right && l.bottom > c.top && l.top < c.bottom) problems.push({ kind: "overlap", detail: "#launcher and #chat" });
    }
  }
  return problems;
}
"""


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


def recorded(messages: list[dict[str, str]]) -> dict[str, Any]:
    said = [m["content"] for m in messages if m["role"] == "user"]
    for fixture in FIXTURES:
        flow = [f for f in FIXTURES if f["flow"] == fixture["flow"]]
        if fixture["user_turn"] == len(said) and fixture["last"] == said[-1] and flow[0]["last"] == said[0]:
            return fixture["response"]
    raise KeyError(said[-1])


def open_page(browser: Browser, site: str, width: int, path: str = "/", health: bool = True, chat_ok: bool = True, block: str = "") -> Page:
    phone = width < 768
    page = browser.new_context(viewport={"width": width, "height": HEIGHTS[width]}, is_mobile=phone, has_touch=phone).new_page()
    page.route("**/health", lambda route: route.fulfill(json=HEALTH) if health else route.abort())
    if chat_ok:
        page.route("**/chat", lambda route: route.fulfill(json=recorded(route.request.post_data_json["messages"])))
    else:
        page.route("**/chat", lambda route: route.fulfill(status=500, json=FAILED))
    if block:
        page.route(f"**/{block}", lambda route: route.abort())
    page.goto(site + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    page.wait_for_timeout(900)
    return page


def expand(page: Page) -> None:
    if page.locator("#chat.is-peek").count():
        page.locator("#welcome h3").click()
        page.wait_for_timeout(500)


def starter(page: Page, label: str) -> None:
    expand(page)
    page.locator(".starter", has_text=label).first.click()


def show_in_log(page: Page, selector: str) -> None:
    page.locator(selector).last.evaluate("(n) => { const log = document.querySelector('#log'); log.scrollTop += n.getBoundingClientRect().top - log.getBoundingClientRect().top - 12; }")
    page.wait_for_timeout(400)


class Audit:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.problems: dict[str, dict[str, list[dict[str, str]]]] = {}

    def shot(self, page: Page, state: str, width: int, full: bool = False) -> None:
        folder = self.out / state
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / f"{width}.png"), full_page=full)
        found = page.evaluate(CHECK_JS)
        if found:
            self.problems.setdefault(state, {})[str(width)] = found

    def sheets(self) -> None:
        for folder in sorted(p for p in self.out.iterdir() if p.is_dir()):
            shots = [Image.open(folder / f"{w}.png") for w in WIDTHS if (folder / f"{w}.png").exists()]
            height = 900
            scaled = [s.resize((max(1, round(s.width * height / s.height)), height)) for s in shots]
            sheet = Image.new("RGB", (sum(s.width for s in scaled) + 24 * (len(scaled) + 1), height + 48), "#d9e0e6")
            x = 24
            for s in scaled:
                sheet.paste(s, (x, 24))
                x += s.width + 24
            sheet.save(self.out / f"{folder.name}.png")


def store_states(audit: Audit, browser: Browser, site: str, width: int) -> None:
    page = open_page(browser, site, width)
    audit.shot(page, "01-home-chat-opens", width)
    page.locator("#close").click()
    page.wait_for_timeout(300)
    audit.shot(page, "02-chat-closed", width)
    audit.shot(page, "03-store-full-page", width, full=True)
    if page.locator("#cartToggle").is_visible():
        page.locator("#cartToggle").click()
        page.wait_for_timeout(400)
        audit.shot(page, "04-cart-open", width)


def chat_states(audit: Audit, browser: Browser, site: str, width: int) -> None:
    page = open_page(browser, site, width)
    expand(page)
    audit.shot(page, "05-chat-welcome", width)
    starter(page, "Cancel an order")
    page.locator(".confirm").first.wait_for()
    page.wait_for_timeout(700)
    show_in_log(page, ".msg--card")
    audit.shot(page, "06-card-waiting-for-yes", width)
    page.locator(".rail").last.click()
    page.wait_for_timeout(500)
    show_in_log(page, ".brain")
    audit.shot(page, "07-details-panel", width)
    page = open_page(browser, site, width)
    starter(page, "Return an item")
    page.locator(".confirm").first.wait_for()
    page.locator(".confirm").first.get_by_role("button", name="Yes, go ahead").click()
    page.locator(".outcome--done").wait_for()
    page.wait_for_timeout(800)
    show_in_log(page, ".outcome--done")
    audit.shot(page, "08-change-done", width)


def error_states(audit: Audit, browser: Browser, site: str, width: int) -> None:
    page = open_page(browser, site, width, health=False)
    expand(page)
    page.locator("#statusRetry").wait_for()
    audit.shot(page, "09-api-unreachable", width)
    page = open_page(browser, site, width, chat_ok=False)
    starter(page, "Track an order")
    page.locator("#actions").wait_for(timeout=20000)
    page.wait_for_timeout(500)
    audit.shot(page, "10-reply-failed", width)


def tour_states(audit: Audit, browser: Browser, site: str, width: int) -> None:
    page = open_page(browser, site, width)
    page.locator("#welcome .tour-btn").click()
    page.locator(".tour-panel__play").wait_for()
    page.locator(".tour-panel__play").click()
    page.wait_for_timeout(700)
    audit.shot(page, "11-tour-1", width)
    nxt = page.locator(".tour-panel__next")
    nxt.click()
    page.wait_for_timeout(900)
    audit.shot(page, "12-tour-2", width)
    nxt.click()
    page.wait_for_timeout(800)
    audit.shot(page, "13-tour-3-tapping-yes", width)
    page.wait_for_timeout(1500)
    audit.shot(page, "14-tour-3", width)
    nxt.click()
    page.wait_for_timeout(1000)
    audit.shot(page, "15-tour-4", width)
    nxt.click()
    page.wait_for_timeout(900)
    audit.shot(page, "16-tour-5", width)


def how_states(audit: Audit, browser: Browser, site: str, width: int) -> None:
    page = open_page(browser, site, width, path="/how-it-works.html")
    page.wait_for_timeout(1500)
    audit.shot(page, "17-how-it-works-top", width)
    audit.shot(page, "18-how-it-works-full-page", width, full=True)
    page = open_page(browser, site, width, path="/how-it-works.html", block="data/results.json")
    page.locator("#passk .load-failed").scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    audit.shot(page, "19-how-it-works-data-failed", width)


GROUPS: tuple[Callable[[Audit, Browser, str, int], None], ...] = (store_states, chat_states, error_states, tour_states, how_states)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="audit-screenshots")
    parser.add_argument("--widths", nargs="*", type=int, default=list(WIDTHS))
    parser.add_argument("--fail", action="store_true")
    args = parser.parse_args()
    audit = Audit(Path(args.out))
    with serve() as site, sync_playwright() as p:
        browser = p.chromium.launch()
        for width in args.widths:
            for group in GROUPS:
                group(audit, browser, site, width)
            print(f"{width}px done")
        browser.close()
    audit.sheets()
    (audit.out / "report.json").write_text(json.dumps(audit.problems, indent=1) + "\n")
    total = sum(len(v) for widths in audit.problems.values() for v in widths.values())
    print(f"{total} problems in {len(audit.problems)} states, report in {audit.out / 'report.json'}")
    if args.fail and total:
        raise SystemExit(json.dumps(audit.problems, indent=1))


if __name__ == "__main__":
    main()
