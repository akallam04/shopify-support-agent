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
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"viewport": {"width": 375, "height": 812}, "is_mobile": True, "has_touch": True}
RESULTS = json.loads(Path("frontend/data/results.json").read_text())


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


def load(browser: Any, site: str, held: list | None = None, sent: list | None = None, **context: Any) -> Any:
    page = browser.new_context(**context).new_page()
    page.route("**/health", lambda route: route.fulfill(json=HEALTH))

    def chat(route: Any) -> None:
        if sent is not None:
            sent.append(route.request.post_data_json)
        if held is not None:
            held.append(route)
            return
        route.fulfill(json=recorded(route.request.post_data_json["messages"]))

    page.route("**/chat", chat)
    page.goto(site)
    return page


def open_page(browser: Any, site: str, held: list | None = None, **context: Any) -> Any:
    page = load(browser, site, held=held, **context)
    expect(page.locator("#welcome")).to_be_visible()
    return page


def starter(page: Any, label: str) -> None:
    page.locator(".starter", has_text=label).click()


def test_cancel_then_decline_keeps_the_order_and_settles_the_card(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport=DESKTOP)
    starter(page, "Cancel an order")
    card = page.locator(".confirm").first
    expect(card).to_contain_text("I will cancel order #1023")
    card.get_by_role("button", name="No, keep it").click()
    expect(card.locator(".sandbox-tag")).to_have_text("You said no")
    expect(page.locator(".outcome--kept")).to_contain_text("Nothing changed")


def test_return_then_confirm_files_the_return(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport=DESKTOP)
    starter(page, "Return an item")
    card = page.locator(".confirm").first
    expect(card).to_contain_text("172.49 USD")
    card.get_by_role("button", name="Yes, go ahead").click()
    expect(card.locator(".sandbox-tag")).to_have_text("You said yes")
    expect(page.locator(".outcome--done")).to_contain_text("A return is requested on order #1022")


def test_a_question_during_confirmation_gets_an_answer_and_the_same_change_again(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport=DESKTOP)
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
    page = open_page(browser, site, held=held, viewport=DESKTOP)
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
    page = open_page(browser, site, **PHONE)
    box = page.locator("#chat").bounding_box()
    assert box["x"] == 0 and box["width"] == 375
    starter(page, "Cancel an order")
    card = page.locator(".confirm").first
    expect(card).to_be_visible()
    card_box = card.bounding_box()
    assert card_box["x"] >= 0 and card_box["x"] + card_box["width"] <= 375
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    assert page.evaluate("document.querySelector('#log').scrollWidth <= document.querySelector('#log').clientWidth")


def test_the_assistant_opens_beside_the_store_on_desktop_without_taking_focus(browser: Any, site: str) -> None:
    sent: list = []
    page = load(browser, site, sent=sent, viewport=DESKTOP)
    expect(page.locator("#chat.is-open")).to_be_visible()
    expect(page.locator("#welcome")).to_contain_text("Show me in 60 seconds")
    expect(page.locator("body")).to_have_class("chat-docked")
    page.wait_for_timeout(600)
    store = page.locator("#main").bounding_box()
    chat = page.locator("#chat").bounding_box()
    assert store["x"] + store["width"] <= chat["x"]
    assert page.evaluate("document.documentElement.scrollWidth") <= DESKTOP["width"]
    assert page.evaluate("document.activeElement === document.body")
    assert sent == []


def test_the_phone_card_shows_the_greeting_and_three_scenarios_and_fills_the_screen_when_tapped(browser: Any, site: str) -> None:
    page = load(browser, site, **PHONE)
    chat = page.locator("#chat.is-open.is-peek")
    expect(chat).to_be_visible()
    expect(page.locator(".starter:visible")).to_have_count(3)
    expect(page.locator(".tour-btn")).to_be_visible()
    page.wait_for_timeout(600)
    assert chat.bounding_box()["height"] <= 812 * 0.6
    assert "chat-locked" not in (page.locator("body").get_attribute("class") or "")
    assert page.evaluate("document.activeElement === document.body")
    page.locator("#welcome h3").tap()
    expect(page.locator("#chat.is-peek")).to_have_count(0)
    expect(page.locator("#input")).to_be_visible()
    page.wait_for_function("document.querySelector('#chat').getBoundingClientRect().height === innerHeight")
    expect(page.locator("body")).to_have_class("chat-locked")


def test_closing_keeps_it_closed_for_the_visit_and_a_new_visit_opens_it_again(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport=DESKTOP)
    page.click("#close")
    expect(page.locator("#chat.is-open")).to_have_count(0)
    expect(page.locator("#launcher")).to_have_attribute("aria-expanded", "false")
    page.reload()
    page.wait_for_timeout(600)
    expect(page.locator("#chat.is-open")).to_have_count(0)
    page.click("#launcher")
    expect(page.locator("#welcome")).to_be_visible()
    fresh = load(browser, site, viewport=DESKTOP)
    expect(fresh.locator("#chat.is-open")).to_be_visible()


def test_reduced_motion_opens_the_assistant_without_the_slide(browser: Any, site: str) -> None:
    page = load(browser, site, viewport=DESKTOP, reduced_motion="reduce")
    expect(page.locator("#chat.is-open")).to_be_visible()
    assert page.evaluate("getComputedStyle(document.querySelector('#chat')).animationName") == "none"


def test_the_tour_replays_a_real_conversation_without_sending_anything_and_ends_with_the_numbers(browser: Any, site: str) -> None:
    sent: list = []
    page = load(browser, site, sent=sent, viewport=DESKTOP, reduced_motion="reduce")
    page.locator(".tour-btn").click()
    bar = page.locator(".tour-bar")
    expect(bar).to_contain_text("Replay of a real conversation")
    expect(bar).to_contain_text("Nothing is sent")
    expect(page.locator(".msg--user")).to_have_count(1)
    steps = [
        (".caption", "Nothing happens until you say yes"),
        (".caption", "the steps it took"),
        (".caption", "Your yes went to the gate"),
        (".caption", "the store's own receipt"),
    ]
    for selector, text in steps:
        bar.get_by_role("button", name="Next").click()
        expect(page.locator(selector)).to_have_count(1)
        expect(page.locator(selector)).to_contain_text(text)
    expect(page.locator(".confirm .sandbox-tag").first).to_have_text("You said yes")
    expect(page.locator(".confirm__actions button:enabled")).to_have_count(0)
    bar.get_by_role("button", name="Next").click()
    end = page.locator(".tour-end")
    h = RESULTS["headline"]
    expect(end).to_contain_text(f"fell from {h['off']['unsafe_conversations']} to {h['on']['unsafe_conversations']}")
    expect(end).to_contain_text("Nothing changes until you say yes")
    expect(end.get_by_role("link", name="How it works")).to_be_visible()
    expect(end).to_contain_text("Now try it yourself")
    expect(page.locator(".caption")).to_have_count(0)
    assert sent == []
    end.locator(".starter", has_text="Cancel an order").click()
    expect(page.locator(".tour-bar")).to_have_count(0)
    expect(page.locator(".confirm").first).to_contain_text("I will cancel order #1023")
    assert len(sent) == 1


def test_the_tour_on_a_phone_opens_the_card_to_full_screen(browser: Any, site: str) -> None:
    page = load(browser, site, reduced_motion="reduce", **PHONE)
    page.locator(".tour-btn").tap()
    expect(page.locator(".tour-bar")).to_be_visible()
    expect(page.locator("#chat.is-peek")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth") <= 375


def test_an_unreachable_api_shows_a_retry_that_recovers(browser: Any, site: str) -> None:
    page = browser.new_context(viewport=DESKTOP).new_page()
    up: list = []
    page.route("**/health", lambda route: route.fulfill(json=HEALTH) if up else route.abort())
    page.goto(site)
    expect(page.locator("#statusText")).to_have_text("Not reachable right now")
    expect(page.locator("#orders table")).to_be_visible()
    up.append(True)
    page.click("#statusRetry")
    expect(page.locator("#statusText")).to_have_text("Ready")
    expect(page.locator("#statusRetry")).to_be_hidden()


def test_how_it_works_offers_a_retry_when_its_data_does_not_load(browser: Any, site: str) -> None:
    page = browser.new_context(viewport=DESKTOP).new_page()
    up: list = []
    page.route("**/data/results.json", lambda route: route.continue_() if up else route.abort())
    page.goto(f"{site}/how-it-works.html")
    expect(page.locator("#passk .load-failed")).to_contain_text("The tables on this page carry the same numbers")
    expect(page.locator("#viewer .load-failed")).to_contain_text("did not load")
    up.append(True)
    page.locator("#passk .load-failed button").click()
    expect(page.locator(".load-failed")).to_have_count(0)
