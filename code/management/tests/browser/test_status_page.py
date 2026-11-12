"""The Status page in a real browser against the owned host's runtime status double."""

import ssl

import httpx
import pytest
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.browser

USERS = ("status-one", "status-two", "status-three")


@pytest.fixture(scope="module")
def status_host(editor_host_factory):
    from tests.conftest import configuration_graph_document

    with editor_host_factory(
        graph=configuration_graph_document(), identities=USERS, mode="local"
    ) as host:
        yield host


def fixture_call(host: dict, endpoint: str, **params) -> None:
    cookie = host["sessions"]["status-one"][0]
    with httpx.Client(
        base_url=host["management"],
        verify=ssl.create_default_context(cafile=host["certificate"]),
        trust_env=False,
        cookies={cookie["name"]: cookie["value"]},
    ) as client:
        client.get(endpoint, params=params).raise_for_status()


@pytest.fixture
def runtime_up(status_host):
    fixture_call(status_host, "/_test/fault", path="/v1/status")
    fixture_call(status_host, "/_test/status/up")
    yield status_host
    fixture_call(status_host, "/_test/fault", path="/v1/status")
    fixture_call(status_host, "/_test/status/up")


def open_status(browser: Browser, host: dict, user: str = "status-one"):
    context = browser.new_context(ignore_https_errors=True)
    context.add_cookies(host["sessions"][user])
    page = context.new_page()
    page.goto(host["ui"] + "/status", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Status", exact=True)).to_be_visible()
    return context, page


def card(page: Page, name: str):
    return page.get_by_test_id("status-" + name)


def test_status_shows_the_service_and_model_deployments(browser: Browser, runtime_up):
    context, page = open_status(browser, runtime_up)
    try:
        expect(card(page, "runtime").get_by_test_id("status-state")).to_have_text("● Reachable")
        text = card(page, "text")
        expect(text).to_contain_text("caller-fixture-foundry.openai.azure.com")
        expect(text).to_contain_text("gpt-fixture-text")
        expect(text).to_contain_text("2026-11-11 10:00:00 UTC")
        expect(text).to_contain_text("Succeeded at 2026-11-11 09:58:12 UTC")
        expect(text.get_by_test_id("status-state")).to_have_text("● Reachable")
        voice = card(page, "voice")
        expect(voice).to_contain_text("swedencentral")
        expect(voice).to_contain_text("gpt-live-1")
        expect(voice).to_contain_text("No call yet")
        shown = page.get_by_role("main").inner_text().lower()
        for secret in ("token", "secret", "password", "https://", "bearer"):
            assert secret not in shown
        fixture_call(runtime_up, "/_test/status/voice-down")
        page.get_by_role("button", name="Refresh", exact=True).click()
        expect(card(page, "voice").get_by_test_id("status-state")).to_have_text("▲ Unreachable")
        expect(card(page, "text").get_by_test_id("status-state")).to_have_text("● Reachable")
    finally:
        context.close()


def test_unreachable_runtime_keeps_the_last_success_and_other_pages(browser: Browser, runtime_up):
    context, page = open_status(browser, runtime_up, "status-two")
    try:
        expect(card(page, "text")).to_be_visible()
        fixture_call(runtime_up, "/_test/status/down")
        page.get_by_role("button", name="Refresh", exact=True).click()
        runtime = card(page, "runtime")
        expect(runtime.get_by_test_id("status-state")).to_have_text("▲ Unreachable")
        expect(runtime).to_contain_text("Last success")
        expect(runtime).not_to_contain_text("Never")
        expect(card(page, "text")).to_have_count(0)
        page.get_by_role("navigation", name="Main").get_by_role(
            "link", name="Community and Agent", exact=True
        ).click()
        expect(page.get_by_test_id("graph")).to_be_visible()
    finally:
        context.close()


def test_slow_status_shows_loading_and_failure_offers_retry(browser: Browser, runtime_up):
    fixture_call(runtime_up, "/_test/fault", path="/v1/status", delay=2.5)
    context, page = open_status(browser, runtime_up, "status-three")
    try:
        page.reload()
        expect(page.get_by_test_id("page-loading")).to_be_visible()
        expect(card(page, "text")).to_be_visible(timeout=15000)
        fixture_call(runtime_up, "/_test/fault", path="/v1/status", fail=True)
        page.reload(wait_until="networkidle")
        error = page.get_by_test_id("page-error")
        expect(error).to_contain_text("Status unavailable")
        fixture_call(runtime_up, "/_test/fault", path="/v1/status")
        error.get_by_role("button", name="Retry", exact=True).click()
        expect(card(page, "text")).to_be_visible()
        expect(page.get_by_test_id("page-error")).to_have_count(0)
    finally:
        context.close()
