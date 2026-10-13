import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

playwright = pytest.importorskip("playwright.sync_api")
expect = playwright.expect

FRONTEND = Path("frontend")
FIXTURES = json.loads(Path("tests/e2e/fixtures.json").read_text())
CANCEL = "Please cancel order #1023. My email is maya.thompson@example.com. I ordered it by mistake."
HEALTH = {"status": "ok", "release": "e2e"}


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(FRONTEND)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def recorded(messages: list[dict[str, str]]) -> dict[str, Any]:
    said = [m["content"] for m in messages if m["role"] == "user"]
    for fixture in FIXTURES:
        flow = [f for f in FIXTURES if f["flow"] == fixture["flow"]]
        if fixture["user_turn"] == len(said) and fixture["last"] == said[-1] and flow[0]["last"] == said[0]:
            return fixture["response"]
    raise AssertionError(f"no recorded response for {said}")


def open_page(browser: Any, site: str, held: list | None = None, **context: Any) -> Any:
    page = browser.new_context(**context).new_page()
    page.route("**/health", lambda route: route.fulfill(json=HEALTH))

    def chat(route: Any) -> None:
        if held is not None:
            held.append(route)
            return
        route.fulfill(json=recorded(route.request.post_data_json["messages"]))

    page.route("**/chat", chat)
    page.goto(site)
    if page.locator("#chat.is-open").count() == 0:
        page.click("#launcher")
    expect(page.locator("#welcome")).to_be_visible()
    return page


def starter(page: Any, label: str) -> None:
    page.locator(".starter", has_text=label).click()


def test_cancel_then_decline_keeps_the_order_and_settles_the_card(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport={"width": 1280, "height": 800})
    starter(page, "Cancel an order")
    card = page.locator(".confirm").first
    expect(card).to_contain_text("I will cancel order #1023")
    card.get_by_role("button", name="No, keep it").click()
    expect(card.locator(".sandbox-tag")).to_have_text("You said no")
    expect(page.locator(".outcome--kept")).to_contain_text("Nothing changed")


def test_return_then_confirm_files_the_return(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport={"width": 1280, "height": 800})
    starter(page, "Return an item")
    card = page.locator(".confirm").first
    expect(card).to_contain_text("172.49 USD")
    card.get_by_role("button", name="Yes, go ahead").click()
    expect(card.locator(".sandbox-tag")).to_have_text("You said yes")
    expect(page.locator(".outcome--done")).to_contain_text("A return is requested on order #1022")


def test_a_question_during_confirmation_gets_an_answer_and_the_same_change_again(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport={"width": 1280, "height": 800})
    starter(page, "Cancel an order")
    first = page.locator(".confirm").first
    expect(first).to_contain_text("I will cancel order #1023")
    page.fill("#input", "Before I decide, what is your return policy?")
    page.click("#send")
    expect(first.locator(".sandbox-tag")).to_have_text("You asked a question")
    expect(page.locator(".confirm")).to_have_count(2)
    expect(page.locator(".confirm").nth(1)).to_contain_text("I will cancel order #1023")
    expect(page.locator(".msg--agent .bubble", has_text="30 days")).to_be_visible()


def test_starting_over_during_a_reply_drops_the_late_answer(browser: Any, site: str) -> None:
    held: list = []
    page = open_page(browser, site, held=held, viewport={"width": 1280, "height": 800})
    starter(page, "Track an order")
    expect(page.locator("#typing")).to_be_visible()
    page.click("#restart")
    expect(page.locator("#welcome")).to_be_visible()
    route = held.pop()
    route.fulfill(json=recorded(route.request.post_data_json["messages"]))
    page.wait_for_timeout(400)
    expect(page.locator("#log")).not_to_contain_text("1Z999AA10123456784")
    expect(page.locator("#input")).to_be_enabled()


def test_the_phone_layout_fills_the_screen_without_sideways_scrolling(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
    box = page.locator("#chat").bounding_box()
    assert box["x"] == 0 and box["width"] == 375
    starter(page, "Cancel an order")
    card = page.locator(".confirm").first
    expect(card).to_be_visible()
    card_box = card.bounding_box()
    assert card_box["x"] >= 0 and card_box["x"] + card_box["width"] <= 375
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    assert page.evaluate("document.querySelector('#log').scrollWidth <= document.querySelector('#log').clientWidth")
