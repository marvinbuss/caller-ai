"""The Settings page in a real browser: voice catalog, ranges, its own draft, runtime down."""

import ssl

import httpx
import pytest
from playwright.sync_api import Browser, Page, expect

from management_app.services.configuration_validation import GRAPH_FIELDS as PUBLISHED_FIELDS

pytestmark = pytest.mark.browser

USERS = ("settings-one", "settings-two", "settings-three", "settings-four")


@pytest.fixture(scope="module")
def settings_host(editor_host_factory):
    from tests.conftest import configuration_graph_document

    with editor_host_factory(
        graph=configuration_graph_document(), identities=USERS, mode="local"
    ) as host:
        yield host


def client(host: dict, user: str = "settings-one") -> httpx.Client:
    cookie = host["sessions"][user][0]
    return httpx.Client(
        base_url=host["management"],
        verify=ssl.create_default_context(cafile=host["certificate"]),
        trust_env=False,
        cookies={cookie["name"]: cookie["value"]},
    )


@pytest.fixture
def runtime_up(settings_host):
    with client(settings_host) as api:
        api.get("/_test/status/up").raise_for_status()
    yield settings_host
    with client(settings_host) as api:
        api.get("/_test/status/up").raise_for_status()


def open_page(browser: Browser, host: dict, user: str, path: str = "/settings"):
    context = browser.new_context(ignore_https_errors=True)
    context.add_cookies(host["sessions"][user])
    page = context.new_page()
    page.goto(host["ui"] + path, wait_until="networkidle")
    return context, page


def voice(page: Page):
    return page.get_by_label("Voice", exact=True)


def test_voice_drop_down_lists_the_catalog_grouped_by_language(browser: Browser, runtime_up):
    context, page = open_page(browser, runtime_up, "settings-one")
    try:
        expect(voice(page)).to_be_enabled()
        groups = voice(page).locator("optgroup")
        expect(groups).to_have_count(3)
        assert [groups.nth(index).get_attribute("label") for index in range(3)] == [
            "English",
            "German",
            "French",
        ]
        options = voice(page).locator("option")
        expect(options.first).to_have_text("Marin \u2014 English (default)")
        expect(voice(page).locator("option[value='quartz']")).to_have_text(
            "Quartz \u2014 English (Australian), feminine"
        )
        expect(voice(page)).to_have_value("marin")
        expect(page.get_by_text("not to spoken output", exact=False)).to_be_visible()
        expect(page.get_by_test_id("settings-environment")).to_contain_text("gpt-fixture-text")
    finally:
        context.close()


def test_out_of_range_idle_timeout_is_refused_with_its_range(browser: Browser, runtime_up):
    context, page = open_page(browser, runtime_up, "settings-two")
    try:
        with client(runtime_up) as api:
            before = api.get("/v2/configuration").json()["revisionNumber"]
        field = page.get_by_label("Idle timeout (seconds)", exact=True)
        field.fill("20")
        page.get_by_role("button", name="Publish settings", exact=True).click()
        expect(page.get_by_test_id("settings-error-idleTimeoutSeconds")).to_have_text(
            "Integer, 30 to 120."
        )
        page.get_by_label("Reply length limit (tokens)", exact=True).fill("5000")
        page.get_by_role("button", name="Publish settings", exact=True).click()
        expect(page.get_by_test_id("settings-error-maxOutputTokens")).to_have_text(
            "Integer, 1 to 1024."
        )
        with client(runtime_up) as api:
            assert api.get("/v2/configuration").json()["revisionNumber"] == before
    finally:
        context.close()


def test_settings_publish_alone_and_keep_the_graph_draft(browser: Browser, runtime_up):
    context, page = open_page(browser, runtime_up, "settings-three", "/communities")
    try:
        page.get_by_test_id("graph-outline").get_by_role(
            "button", name="alpha (Custom)", exact=True
        ).click()
        page.get_by_role("group", name="Community alpha", exact=True).get_by_label(
            "Name", exact=True
        ).fill("Draft alpha")
        expect(page.get_by_test_id("editor-status")).to_have_text("Unsaved")
        page.get_by_role("navigation", name="Main").get_by_role(
            "link", name="Settings", exact=True
        ).click()
        expect(voice(page)).to_be_enabled()
        voice(page).select_option("quartz")
        page.get_by_label("Idle timeout (seconds)", exact=True).fill("60")
        page.get_by_role("button", name="Publish settings", exact=True).click()
        expect(page.get_by_test_id("settings-status")).to_contain_text("Published revision")
        expect(page.get_by_test_id("settings-status")).to_contain_text(
            "apply to conversations started from now on"
        )
        with client(runtime_up) as api:
            active = api.get("/v2/configuration").json()
        assert active["conversationSettings"] == {"voice": "quartz", "idleTimeoutSeconds": 60}
        assert "Draft alpha" not in [row["name"] for row in active["communities"]]
        page.get_by_role("navigation", name="Main").get_by_role(
            "link", name="Community and Agent", exact=True
        ).click()
        expect(page.get_by_test_id("editor-status")).to_have_text("Unsaved")
        expect(
            page.get_by_test_id("graph-outline").get_by_role(
                "button", name="Draft alpha (Custom)", exact=True
            )
        ).to_be_visible()
    finally:
        context.close()


def test_another_publication_shows_a_notice_and_keeps_the_settings(browser: Browser, runtime_up):
    context, page = open_page(browser, runtime_up, "settings-two")
    try:
        expect(voice(page)).to_be_enabled()
        page.get_by_label("Idle timeout (seconds)", exact=True).fill("45")
        with client(runtime_up, "settings-one") as api:
            current = api.get("/v2/configuration")
            graph = current.json()
            graph["communities"][0]["routingDescription"] += " Published elsewhere."
            csrf = api.get("/auth/session").json()["csrf"]
            saved = api.put(
                "/v2/configuration",
                json={key: graph[key] for key in graph if key in PUBLISHED_FIELDS},
                headers={
                    "If-Match": current.headers["ETag"],
                    "X-CSRF-Token": csrf,
                    "Origin": runtime_up["ui"],
                },
            )
            saved.raise_for_status()
            number = saved.json()["revisionNumber"]
        notice = page.get_by_test_id("settings-revision-notice")
        expect(notice).to_contain_text(f"revision {number}", timeout=30000)
        expect(page.get_by_label("Idle timeout (seconds)", exact=True)).to_have_value("45")
        notice.get_by_role("button", name="Reload", exact=True).click()
        expect(page.get_by_test_id("settings-revision-notice")).to_have_count(0)
        expect(page.get_by_label("Idle timeout (seconds)", exact=True)).to_have_value("45")
        page.get_by_role("button", name="Publish settings", exact=True).click()
        expect(page.get_by_test_id("settings-status")).to_contain_text(
            f"Published revision {number + 1}"
        )
        with client(runtime_up) as api:
            active = api.get("/v2/configuration").json()
        assert active["conversationSettings"]["idleTimeoutSeconds"] == 45
        assert active["communities"][0]["routingDescription"].endswith("Published elsewhere.")
    finally:
        context.close()


def test_runtime_down_makes_the_settings_read_only(browser: Browser, runtime_up):
    with client(runtime_up) as api:
        api.get("/_test/status/down").raise_for_status()
    context, page = open_page(browser, runtime_up, "settings-four")
    try:
        expect(page.get_by_test_id("settings-read-only")).to_contain_text("read-only")
        expect(voice(page)).to_be_disabled()
        expect(page.get_by_role("button", name="Publish settings", exact=True)).to_be_disabled()
        expect(page.get_by_label("Idle timeout (seconds)", exact=True)).to_have_attribute(
            "readonly", ""
        )
    finally:
        context.close()
