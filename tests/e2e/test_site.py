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
    expect(card).to_contain_text("Cancel order #1023")
    card.get_by_role("button", name="No, keep it").click()
    expect(card.locator(".sandbox-tag")).to_have_text("You said no")
    expect(page.locator(".outcome--kept")).to_contain_text("Nothing changed")


def test_return_then_confirm_files_the_return(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport=DESKTOP)
    starter(page, "Return an item")
    card = page.locator(".confirm").first
    expect(card).to_contain_text("172.49 USD")
    expect(card.locator(".confirm__rows dt")).to_have_text(["Reason", "Condition", "Fee", "Refund", "Timing"])
    expect(card.locator(".sandbox-tag")).to_have_text("Demo store")
    card.get_by_role("button", name="Yes, go ahead").click()
    expect(card.locator(".sandbox-tag")).to_have_text("You said yes")
    expect(page.locator(".outcome--done")).to_contain_text("A return is requested on order #1022")


def test_a_question_during_confirmation_gets_an_answer_and_the_same_change_again(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport=DESKTOP)
    starter(page, "Cancel an order")
    first = page.locator(".confirm").first
    expect(first).to_contain_text("Cancel order #1023")
    page.fill("#input", "Before I decide, what is your return policy?")
    page.click("#send")
    expect(first.locator(".sandbox-tag")).to_have_text("You asked a question")
    expect(page.locator(".confirm")).to_have_count(2)
    expect(page.locator(".confirm").nth(1)).to_contain_text("Cancel order #1023")
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
    assert page.evaluate("getComputedStyle(document.querySelector('#chat')).opacity") == "1"
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


JARGON = ("gate", "policy", "tool", "trace", "verify", "pending", "intent", "sandbox")


def panel_copy(page: Any) -> str:
    return page.locator(".tour-panel__title").inner_text() + " " + page.locator(".tour-panel__text").inner_text()


def test_the_tour_tells_the_story_in_five_plain_steps_without_sending_anything(browser: Any, site: str) -> None:
    sent: list = []
    page = load(browser, site, sent=sent, viewport=DESKTOP, reduced_motion="reduce")
    page.locator("#welcome .tour-btn").click()
    count, nxt = page.locator(".tour-panel__count"), page.locator(".tour-panel__next")
    expect(page.locator(".tour-label")).to_have_text("Replay of a real conversation in the demo store")
    expect(count).to_have_text("1 of 5")
    expect(page.locator(".tour-panel__title")).to_have_text("A customer wants to return a jacket that's too big.")
    expect(page.locator("#input")).to_be_disabled()
    expect(page.locator("#input")).to_have_attribute("placeholder", "Tour playing")
    copy = [panel_copy(page)]
    nxt.click()
    expect(count).to_have_text("2 of 5")
    expect(page.locator(".confirm")).to_be_visible()
    expect(page.locator(".confirm__actions button:disabled")).to_have_count(0)
    copy.append(panel_copy(page))
    nxt.click()
    expect(count).to_have_text("3 of 5")
    expect(page.locator(".confirm .sandbox-tag")).to_have_text("You said yes")
    expect(page.locator(".msg--user")).to_have_count(2)
    copy.append(panel_copy(page))
    page.locator(".tour-panel__back").click()
    expect(count).to_have_text("2 of 5")
    expect(page.locator(".confirm")).to_have_attribute("data-state", "open")
    expect(page.locator(".msg--user")).to_have_count(1)
    nxt.click()
    nxt.click()
    expect(count).to_have_text("4 of 5")
    expect(page.locator(".outcome--done")).to_contain_text("A return is requested on order #1022")
    copy.append(panel_copy(page))
    nxt.click()
    expect(count).to_have_text("5 of 5")
    h = RESULTS["headline"]
    expect(page.locator(".tour-stat__tile--off strong")).to_have_text(str(h["off"]["unsafe_conversations"]))
    expect(page.locator(".tour-stat__tile--on strong")).to_have_text(str(h["on"]["unsafe_conversations"]))
    expect(page.locator(".tour-panel__text")).to_contain_text(f"{h['off']['unsafe_conversations']} of {h['off']['conversations']} test conversations")
    expect(page.get_by_role("link", name="See how it was tested")).to_have_attribute("href", "./how-it-works.html")
    expect(page.get_by_role("link", name="View the code")).to_have_attribute("href", "https://github.com/akallam04/shopify-support-agent")
    copy.append(panel_copy(page))
    assert not [w for w in JARGON if w in " ".join(copy).lower()]
    assert page.locator("#log .brain, #log .rail, #log .msg__meta, #log .source").count() == 0
    assert sent == []
    page.get_by_role("button", name="Try it yourself").click()
    expect(page.locator(".tour-panel")).to_have_count(0)
    expect(page.locator("#input")).to_be_enabled()
    expect(page.locator("#input")).to_have_attribute("placeholder", "Ask about an order or gear")
    starter(page, "Cancel an order")
    expect(page.locator(".confirm").first).to_contain_text("Cancel order #1023")
    assert len(sent) == 1


def test_the_tour_plays_on_its_own_and_pause_holds_it(browser: Any, site: str) -> None:
    page = browser.new_context(viewport=DESKTOP).new_page()
    page.clock.install()
    page.route("**/health", lambda route: route.fulfill(json=HEALTH))
    page.goto(site)
    page.locator("#welcome .tour-btn").click()
    count = page.locator(".tour-panel__count")
    expect(count).to_have_text("1 of 5")
    page.clock.run_for(4600)
    expect(count).to_have_text("2 of 5")
    page.locator(".tour-panel__play").click()
    expect(page.locator(".tour-panel__play")).to_have_attribute("aria-label", "Play the tour")
    page.clock.run_for(30000)
    expect(count).to_have_text("2 of 5")
    page.locator(".tour-panel__play").click()
    page.clock.run_for(8100)
    expect(count).to_have_text("3 of 5")
    page.clock.run_for(14100)
    expect(count).to_have_text("5 of 5")
    page.clock.run_for(30000)
    expect(count).to_have_text("5 of 5")


def test_the_tour_on_a_phone_fills_the_screen_with_the_panel_below_the_chat(browser: Any, site: str) -> None:
    page = load(browser, site, reduced_motion="reduce", **PHONE)
    page.locator("#welcome .tour-btn").tap()
    expect(page.locator(".tour-panel__count")).to_have_text("1 of 5")
    expect(page.locator("#chat.is-peek")).to_have_count(0)
    page.wait_for_function("document.querySelector('#chat').getBoundingClientRect().height === innerHeight")
    page.locator(".tour-panel__next").tap()
    expect(page.locator(".confirm")).to_be_visible()
    log, panel, composer = (page.locator(s).bounding_box() for s in ("#log", ".tour-panel", ".composer"))
    assert log["y"] + log["height"] <= panel["y"] + 1
    assert panel["y"] + panel["height"] <= composer["y"] + 1
    assert composer["y"] + composer["height"] <= 812 + 1
    for key in (".confirm__actions", ".confirm__hint"):
        box = page.locator(key).bounding_box()
        assert box["y"] >= log["y"] - 1 and box["y"] + box["height"] <= log["y"] + log["height"] + 1, key
    assert page.evaluate("document.documentElement.scrollWidth") <= 375


def test_escape_leaves_the_tour_and_keeps_the_chat_open(browser: Any, site: str) -> None:
    page = load(browser, site, viewport=DESKTOP, reduced_motion="reduce")
    page.locator("#welcome .tour-btn").click()
    expect(page.locator(".tour-panel")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".tour-panel")).to_have_count(0)
    expect(page.locator("#chat.is-open")).to_be_visible()
    expect(page.locator("#welcome")).to_be_visible()


def test_an_unreachable_api_shows_a_retry_that_recovers(browser: Any, site: str) -> None:
    page = browser.new_context(viewport=DESKTOP).new_page()
    up: list = []
    page.route("**/health", lambda route: route.fulfill(json=HEALTH) if up else route.abort())
    page.goto(site)
    expect(page.locator("#statusText")).to_have_text("Can't connect")
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


@pytest.mark.parametrize("width", [320, 375, 390])
def test_every_how_it_works_table_fits_its_box_on_a_phone(browser: Any, site: str, width: int) -> None:
    page = browser.new_context(viewport={"width": width, "height": 800}, is_mobile=True).new_page()
    page.goto(f"{site}/how-it-works.html")
    expect(page.locator(".table-scroll").first).to_be_visible()
    assert page.evaluate("document.fonts.ready.then(() => document.fonts.status)") == "loaded"
    widths = page.evaluate("[...document.querySelectorAll('.table-scroll')].map(s => s.scrollWidth - s.clientWidth)")
    assert widths and max(widths) <= 1
    assert page.evaluate("document.documentElement.scrollWidth") <= width


def expect_greeting(page: Any) -> None:
    expect(page.locator("#chat.is-open #welcome")).to_be_visible()
    expect(page.locator("#welcome .starter").first).to_be_visible()
    expect(page.locator("#welcome .tour-btn")).to_be_visible()


def test_leaving_the_tour_with_exit_shows_the_greeting(browser: Any, site: str) -> None:
    page = load(browser, site, viewport=DESKTOP, reduced_motion="reduce")
    page.locator("#welcome .tour-btn").click()
    page.locator(".tour-panel__exit").click()
    expect_greeting(page)


def test_closing_the_chat_during_the_tour_reopens_on_the_greeting(browser: Any, site: str) -> None:
    page = load(browser, site, viewport=DESKTOP, reduced_motion="reduce")
    page.locator("#welcome .tour-btn").click()
    expect(page.locator(".tour-panel")).to_be_visible()
    page.click("#close")
    page.click("#launcher")
    expect_greeting(page)
    expect(page.locator(".tour-panel")).to_have_count(0)


def test_starting_over_shows_the_greeting(browser: Any, site: str) -> None:
    page = open_page(browser, site, viewport=DESKTOP)
    starter(page, "Track an order")
    expect(page.locator(".msg--agent .bubble", has_text="1Z999AA10123456784")).to_be_visible()
    page.click("#restart")
    expect_greeting(page)


def test_closing_and_reopening_keeps_the_greeting(browser: Any, site: str) -> None:
    for context in ({"viewport": DESKTOP}, PHONE):
        page = load(browser, site, **context)
        page.click("#close")
        page.click("#launcher")
        expect_greeting(page)


def test_a_failed_health_check_still_shows_the_greeting(browser: Any, site: str) -> None:
    page = browser.new_context(viewport=DESKTOP).new_page()
    page.route("**/health", lambda route: route.abort())
    page.goto(site)
    expect(page.locator("#statusText")).to_have_text("Can't connect")
    expect_greeting(page)
    page.click("#close")
    page.click("#launcher")
    expect_greeting(page)


def test_closing_while_the_tour_is_still_loading_reopens_on_the_greeting(browser: Any, site: str) -> None:
    held: list = []
    page = browser.new_context(viewport=DESKTOP, reduced_motion="reduce").new_page()
    page.route("**/health", lambda route: route.fulfill(json=HEALTH))
    page.route("**/data/tour.json", lambda route: held.append(route))
    page.goto(site)
    page.locator("#welcome .tour-btn").click()
    page.wait_for_function("document.querySelector('#chat').classList.contains('is-touring')")
    page.click("#close")
    for route in held:
        route.continue_()
    page.wait_for_timeout(300)
    page.click("#launcher")
    expect_greeting(page)
    expect(page.locator(".tour-panel")).to_have_count(0)


def test_a_slow_health_check_never_hides_the_greeting(browser: Any, site: str) -> None:
    held: list = []
    page = browser.new_context(**PHONE).new_page()
    page.route("**/health", lambda route: held.append(route))
    page.goto(site)
    expect(page.locator("#statusText")).to_have_text("Waking up")
    expect(page.locator("#welcome .starter").first).to_be_visible()
    page.locator("#welcome h3").tap()
    expect_greeting(page)


def test_a_scenario_button_during_the_tour_ends_it_and_sends_the_request(browser: Any, site: str) -> None:
    sent: list = []
    page = load(browser, site, sent=sent, viewport=DESKTOP, reduced_motion="reduce")
    page.locator("#welcome .tour-btn").click()
    expect(page.locator(".tour-panel")).to_be_visible()
    page.locator("[data-ask]").first.click()
    expect(page.locator(".tour-panel")).to_have_count(0)
    expect(page.locator("#input")).to_be_enabled()
    expect(page.locator(".msg--user")).to_have_count(1)
    assert len(sent) == 1


FOCUS = [[".msg--user"], [".confirm__actions", ".confirm__hint"], [".msg--user"], [".outcome__head"], [".tour-stat__tiles"]]


@pytest.mark.parametrize("context", [{"viewport": DESKTOP}, PHONE, {"viewport": {"width": 320, "height": 568}, "is_mobile": True, "has_touch": True}], ids=["desktop", "phone", "small-phone"])
def test_each_tour_step_shows_its_focus_fully_above_the_panel(browser: Any, site: str, context: dict) -> None:
    page = load(browser, site, reduced_motion="reduce", **context)
    page.locator("#welcome .tour-btn").click()
    for step, selectors in enumerate(FOCUS):
        expect(page.locator(".tour-panel__count")).to_have_text(f"{step + 1} of 5")
        page.wait_for_timeout(150)
        log = page.locator("#log").bounding_box()
        panel = page.locator(".tour-panel").bounding_box()
        assert log["y"] + log["height"] <= panel["y"] + 1
        for selector in selectors:
            box = page.locator(selector).last.bounding_box()
            assert box["y"] >= log["y"] - 1 and box["y"] + box["height"] <= log["y"] + log["height"] + 1, (step + 1, selector)
        if step < 4:
            page.locator(".tour-panel__next").click()
