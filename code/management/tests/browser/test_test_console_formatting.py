"""Formatted text replies in the Test console: the browser engine renders `text.formatted` trees."""

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.browser

TREE = {
    "type": "root",
    "children": [
        {"type": "h2", "children": ["Options"]},
        {
            "type": "ul",
            "children": [
                {"type": "li", "children": ["first"]},
                {"type": "li", "children": [{"type": "strong", "children": ["second"]}]},
            ],
        },
        {
            "type": "table",
            "children": [
                {
                    "type": "thead",
                    "children": [
                        {"type": "tr", "children": [{"type": "th", "children": ["Plan"]}]}
                    ],
                },
                {
                    "type": "tbody",
                    "children": [
                        {"type": "tr", "children": [{"type": "td", "children": ["Basic"]}]}
                    ],
                },
            ],
        },
        {
            "type": "p",
            "children": [
                {"type": "a", "href": "https://example.com/docs", "children": ["docs"]},
                {"type": "a", "href": "javascript:alert(1)", "children": ["script"]},
                {"type": "a", "href": "data:text/html,<b>x</b>", "children": ["data"]},
                {"type": "a", "href": "http://example.com/", "children": ["plain"]},
                {"type": "img", "src": "https://example.com/chart.png", "alt": "chart"},
                {"type": "img", "src": "data:image/png;base64,AAAA", "alt": "inline"},
                {"type": "img", "src": "http://example.com/i.png", "alt": "insecure"},
                {"type": "img", "src": "javascript:alert(1)", "alt": "scripted"},
            ],
        },
    ],
}


def render(page: Page, tree: dict) -> list[str]:
    from management_app.components.live_audio_js import BUDGET_CODE, ENGINE_CODE

    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.route("https://example.com/**", lambda route: route.abort())
    page.route("http://example.com/**", lambda route: route.abort())
    # The same container attributes as `#test-messages` on the Test page.
    page.set_content(
        '<div id="test-messages" role="log" aria-label="Conversation" aria-live="polite"></div>'
    )
    page.add_script_tag(content=BUDGET_CODE + ENGINE_CODE)
    page.evaluate(
        """tree => {
        const engine = new CallerTestEngine({begin: () => {}, control: () => {}});
        engine.terminal = false; engine.mode = 'text'; engine.epoch = 1;
        engine.snapshot = {status: 'ready', mode: 'text', sessionId: 'fixture', generation: 1};
        engine.socket = {readyState: 1, bufferedAmount: 0, send: () => {}, close: () => {}};
        let sequence = 1;
        const receive = (type, fields) => engine.message(JSON.stringify({type,
            sequence: ++sequence, generation: 1, sessionId: 'fixture', ...fields}), 1);
        receive('text.delta', {messageId: 'reply', text: '## Options'});
        receive('text.completed', {messageId: 'reply', text: ''});
        receive('text.formatted', {messageId: 'reply', tree});
    }""",
        tree,
    )
    return requests


def test_reply_with_a_list_and_a_table_renders_formatted(page: Page):
    render(page, TREE)
    row = page.locator("#test-messages > div[data-formatted='true']")
    assert row.count() == 1
    assert page.locator("#test-messages > p").count() == 0
    assert row.locator("h2").inner_text() == "Options"
    assert row.locator("ul > li").count() == 2
    assert row.locator("ul > li > strong").inner_text() == "second"
    assert row.locator("table thead th").inner_text() == "Plan"
    assert row.locator("table tbody td").inner_text() == "Basic"


def test_unsafe_links_and_images_are_neither_clickable_nor_loaded(page: Page):
    requests = render(page, TREE)
    row = page.locator("#test-messages > div[data-formatted='true']")
    hrefs = row.locator("a").evaluate_all("links => links.map(link => link.href)")
    sources = row.locator("img").evaluate_all("images => images.map(image => image.src)")
    assert hrefs == ["https://example.com/docs"]
    assert sources == ["https://example.com/chart.png"]
    for label in ("script", "data", "plain", "inline", "insecure", "scripted"):
        assert row.get_by_text(label, exact=True).count() == 1
    assert not [url for url in requests if not url.startswith(("https:", "about:"))]


def test_https_links_and_images_carry_the_safe_attributes(page: Page):
    render(page, TREE)
    row = page.locator("#test-messages > div[data-formatted='true']")
    link = row.locator("a")
    assert link.get_attribute("target") == "_blank"
    assert link.get_attribute("rel") == "noopener noreferrer"
    assert link.get_attribute("referrerpolicy") == "no-referrer"
    image = row.locator("img")
    assert image.get_attribute("referrerpolicy") == "no-referrer"
    assert image.get_attribute("loading") == "lazy"
    assert image.get_attribute("alt") == "chart"
    assert image.evaluate("image => image.style.maxWidth") == "100%"


def test_messages_keep_the_log_role_and_polite_announcements(page: Page):
    render(page, TREE)
    messages = page.locator("#test-messages")
    assert messages.get_attribute("role") == "log"
    assert messages.get_attribute("aria-live") == "polite"


def test_an_invalid_tree_keeps_the_plain_text_row(page: Page):
    render(page, {"type": "root", "children": [{"type": "script", "children": ["x"]}]})
    assert page.locator("#test-messages > div").count() == 0
    assert page.locator("#test-messages > p").inner_text() == "Agent: ## Options"


def test_status_rows_look_distinct_stay_in_history_and_use_the_polite_log(page: Page):
    from management_app.components.live_audio_js import BUDGET_CODE, ENGINE_CODE
    from management_app.pages.test_console import STATUS_STYLE

    # The Test page's status row style, as the page applies it to `#test-messages`.
    css = "; ".join(f"{key.replace('_', '-')}: {value}" for key, value in STATUS_STYLE.items())
    page.set_content(
        f"<style>#test-messages [data-kind='status'] {{{css}}}</style>"
        '<div id="test-messages" role="log" aria-label="Conversation" aria-live="polite"></div>'
    )
    page.add_script_tag(content=BUDGET_CODE + ENGINE_CODE)
    page.evaluate(
        """() => {
        const engine = new CallerTestEngine({begin: () => {}, control: () => {}});
        engine.terminal = false; engine.mode = 'text'; engine.epoch = 1;
        engine.snapshot = {status: 'ready', mode: 'text', sessionId: 'fixture', generation: 1};
        engine.socket = {readyState: 1, bufferedAmount: 0, send: () => {}, close: () => {}};
        let sequence = 1;
        const receive = (type, fields) => engine.message(JSON.stringify({type,
            sequence: ++sequence, generation: 1, sessionId: 'fixture', ...fields}), 1);
        receive('progress', {messageId: 'reply', state: 'still_working', text: 'Checking.'});
        receive('text.delta', {messageId: 'reply', text: 'Here it is.'});
        receive('text.completed', {messageId: 'reply', text: ''});
        receive('progress', {messageId: 'reply', state: 'completed', text: 'Done.'});
    }"""
    )
    rows = page.locator("#test-messages > *")
    assert rows.all_inner_texts() == ["Status: Checking.", "Agent: Here it is.", "Status: Done."]
    status, reply = page.locator("[data-kind='status']").first, rows.nth(1)

    def style(locator):
        return locator.evaluate(
            "row => { const s = getComputedStyle(row);"
            " return [s.fontStyle, s.borderLeftStyle, s.backgroundColor]; }"
        )

    assert style(status) != style(reply) and style(status)[0] == "italic"
    messages = page.locator("#test-messages")
    assert messages.get_attribute("role") == "log"
    assert messages.get_attribute("aria-live") == "polite"
