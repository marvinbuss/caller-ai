"""The authenticated frame in a real browser: login gate, roles, navigation, session and profile."""

import ssl
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

pytestmark = pytest.mark.browser

PAGES = {
    "/": "Welcome to Caller AI",
    "/communities": "Community and Agent",
    "/test": "Test",
    "/status": "Status",
    "/settings": "Settings",
}
NAV = {
    "dashboard": "Dashboard",
    "communities": "Community and Agent",
    "test": "Test",
    "status": "Status",
    "settings": "Settings",
}
USERS = ("frame-one", "frame-two", "frame-three")
ROUTE_PAGES = {
    "/": "dashboard",
    "/communities": "communities",
    "/test": "test",
    "/status": "status",
    "/settings": "settings",
}


@pytest.fixture(scope="module")
def frame_host(editor_host_factory):
    from tests.conftest import configuration_graph_document

    with editor_host_factory(
        graph=configuration_graph_document(), identities=USERS, mode="local"
    ) as host:
        yield host


def answer_identity_provider(context: BrowserContext, host: dict, code: str) -> None:
    """Stand in for the identity provider: post the fixture code back to the callback.

    `/auth/login` runs for real; its redirect to the provider is replaced by a form that posts
    the provider's answer to `/auth/callback`. The provider itself is never contacted.
    """

    def respond(route):
        started = route.fetch(max_redirects=0)
        assert started.status == 303
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        route.fulfill(
            status=200,
            headers={
                "content-type": "text/html",
                "set-cookie": started.headers["set-cookie"],
                "cache-control": "no-store",
            },
            body=(
                f"<form method='post' action='{host['ui']}/auth/callback'>"
                f"<input name='code' value='{code}'><input name='state' value='{state}'>"
                "</form><script>document.forms[0].submit()</script>"
            ),
        )

    context.route("https://login.microsoftonline.com/**", lambda route: route.abort())
    context.route("**/auth/login*", respond)


def signed_in(
    browser: Browser,
    host: dict,
    *,
    user: str = "frame-one",
    roles: str = "editor+tester",
    target: str = "/",
    **expiry: int,
) -> tuple[BrowserContext, Page]:
    context = browser.new_context(ignore_https_errors=True)
    query = {"roles": roles, "target": target, **{key: str(value) for key, value in expiry.items()}}
    assert context.request.get(host["management"] + "/_test/login/" + user, params=query).ok
    page = context.new_page()
    page.goto(host["ui"] + target, wait_until="networkidle")
    return context, page


def wait_for_login(page: Page, query: str = "", timeout: float = 30000) -> None:
    """The login page, with or without the exported trailing slash."""
    page.wait_for_url(
        lambda url: urlsplit(url).path.rstrip("/") == "/login" and urlsplit(url).query == query,
        timeout=timeout,
    )


def visible_nav(page: Page) -> list[str]:
    navigation = page.get_by_role("navigation", name="Main")
    return [
        label
        for label in NAV.values()
        if navigation.get_by_role("link", name=label, exact=True).count()
    ]


@pytest.mark.parametrize("route", ["/", "/communities", "/test", "/status", "/settings", "/login"])
def test_without_session_every_route_shows_only_login(browser: Browser, frame_host, route):
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    data: list[str] = []
    page.on(
        "request",
        lambda request: (
            data.append(request.url)
            if "/v2/configuration" in request.url or "/v1/status" in request.url
            else None
        ),
    )
    page.goto(frame_host["ui"] + route, wait_until="networkidle")
    expect(page.get_by_role("img", name="Caller AI")).to_be_visible()
    expect(page.get_by_role("link", name="Sign in", exact=True)).to_be_visible()
    assert page.get_by_role("navigation").count() == 0
    assert page.get_by_role("heading", name=PAGES.get(route, "none"), exact=True).count() == 0
    assert data == []
    context.close()


def test_deep_link_returns_to_the_requested_page_after_sign_in(browser: Browser, frame_host):
    context = browser.new_context(ignore_https_errors=True)
    answer_identity_provider(context, frame_host, "frame-two:editor+tester")
    page = context.new_page()
    page.goto(frame_host["ui"] + "/status", wait_until="networkidle")
    sign_in = page.get_by_role("link", name="Sign in", exact=True)
    expect(sign_in).to_have_attribute("href", "/auth/login?returnTo=%2Fstatus")
    sign_in.click()
    expect(page.get_by_role("heading", name="Status", exact=True)).to_be_visible(timeout=15000)
    assert urlsplit(page.url).path.rstrip("/") == "/status", page.url
    context.close()


def test_sign_in_reaches_the_first_page_quickly(browser: Browser, frame_host):
    durations = []
    for _ in range(20):
        context = browser.new_context(ignore_https_errors=True)
        answer_identity_provider(context, frame_host, "frame-three:editor")
        page = context.new_page()
        page.goto(frame_host["ui"] + "/login", wait_until="networkidle")
        with page.expect_request("**/auth/callback"):
            page.get_by_role("link", name="Sign in", exact=True).click()
        started = time.monotonic()
        expect(page.get_by_role("heading", name="Welcome to Caller AI", exact=True)).to_be_visible()
        durations.append(time.monotonic() - started)
        context.close()
    assert max(durations) < 5, durations


def test_failed_sign_in_returns_to_login_with_a_retry_message(browser: Browser, frame_host):
    context = browser.new_context(ignore_https_errors=True)
    answer_identity_provider(context, frame_host, "frame-one:fail")
    page = context.new_page()
    page.goto(frame_host["ui"] + "/", wait_until="networkidle")
    page.get_by_role("link", name="Sign in", exact=True).click()
    wait_for_login(page, "signin=failed")
    expect(page.get_by_role("alert")).to_have_text("Sign-in did not complete. Try again.")
    expect(page.get_by_role("link", name="Sign in", exact=True)).to_be_visible()
    assert not any(cookie["name"] == "__Host-caller-session" for cookie in context.cookies())
    context.close()


@pytest.mark.parametrize(
    "roles,entries",
    [
        ("editor+tester", ["Dashboard", "Community and Agent", "Test", "Status", "Settings"]),
        ("editor", ["Dashboard", "Community and Agent", "Status", "Settings"]),
        ("tester", ["Dashboard", "Status"]),
    ],
)
def test_sidebar_follows_the_roles(browser: Browser, frame_host, roles, entries):
    context, page = signed_in(browser, frame_host, roles=roles)
    expect(page.get_by_role("heading", name="Welcome to Caller AI", exact=True)).to_be_visible()
    assert visible_nav(page) == entries
    for route, heading in PAGES.items():
        page_id = ROUTE_PAGES[route]
        page.goto(frame_host["ui"] + route, wait_until="networkidle")
        if NAV[page_id] in entries:
            expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        else:
            expect(page.get_by_role("heading", name="Role required", exact=True)).to_be_visible()
            assert page.get_by_role("heading", name=heading, exact=True).count() == 0
    context.close()


def test_a_session_without_either_role_sees_no_access_and_sign_out(browser: Browser, frame_host):
    context, page = signed_in(browser, frame_host, roles="none")
    expect(page.get_by_role("heading", name="No access", exact=True)).to_be_visible()
    assert page.get_by_role("navigation").count() == 0
    page.get_by_role("button", name="Sign out", exact=True).click()
    wait_for_login(page)
    expect(page.get_by_role("link", name="Sign in", exact=True)).to_be_visible()
    context.close()


def test_every_page_is_one_selection_away_and_marked_current(browser: Browser, frame_host):
    context, page = signed_in(browser, frame_host)
    navigation = page.get_by_role("navigation", name="Main")
    for page_id, label in NAV.items():
        navigation.get_by_role("link", name=label, exact=True).click()
        heading = "Welcome to Caller AI" if page_id == "dashboard" else label
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        current = navigation.locator("[aria-current='page']")
        expect(current).to_have_count(1)
        expect(current).to_have_text(label)
    context.close()


def test_sign_out_returns_to_login_and_ends_the_session(browser: Browser, frame_host):
    context, page = signed_in(browser, frame_host)
    page.get_by_role("button", name="Sign out", exact=True).click()
    wait_for_login(page)
    expect(page.get_by_role("link", name="Sign in", exact=True)).to_be_visible()
    assert context.request.get(frame_host["management"] + "/auth/session").status == 401
    context.close()


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_responsive_frame(browser: Browser, frame_host, width):
    context, page = signed_in(browser, frame_host)
    page.set_viewport_size({"width": width, "height": 900})
    expect(page.get_by_role("heading", name="Welcome to Caller AI", exact=True)).to_be_visible()
    menu = page.get_by_role("button", name="Menu", exact=True)
    sidebar = page.locator("#caller-sidebar")
    if width < 768:
        expect(menu).to_be_visible()
        expect(sidebar).to_be_hidden()
        menu.click()
        expect(sidebar).to_be_visible()
        expect(menu).to_have_attribute("aria-expanded", "true")
    else:
        expect(menu).to_be_hidden()
        expect(sidebar).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.tagName !== 'BODY'")
    expect(page.get_by_role("link", name="Caller AI", exact=True)).to_have_attribute("href", "/")
    context.close()


def test_session_warning_extends_the_session(browser: Browser, frame_host):
    context, page = signed_in(browser, frame_host, session_seconds=290)
    warning = page.get_by_test_id("session-warning")
    expect(warning).to_be_visible()
    before = page.evaluate("fetch('/auth/session').then(r => r.json()).then(v => v.expiresAt)")
    warning.get_by_role("button", name="Stay signed in").click()
    expect(warning).to_be_hidden()
    after = page.evaluate("fetch('/auth/session').then(r => r.json()).then(v => v.expiresAt)")
    assert after > before
    context.close()


FOCUS_STATE = """() => {
    const element = document.activeElement;
    if (!element || element === document.body) return null;
    const style = getComputedStyle(element);
    const outlined = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0;
    const ringed = style.boxShadow && style.boxShadow !== "none";
    return {
        name: (element.getAttribute("aria-label") || element.textContent || element.tagName)
            .trim().slice(0, 40),
        tag: element.tagName,
        visible: outlined || ringed,
    };
}"""


def test_keyboard_only_walkthrough_reaches_every_page_with_visible_focus(
    browser: Browser, frame_host
):
    context, page = signed_in(browser, frame_host, user="frame-two")
    try:
        expect(page.get_by_role("heading", name="Welcome to Caller AI", exact=True)).to_be_visible()
        for page_id, label in NAV.items():
            if page_id == "dashboard":
                continue
            # Tab from the top of the page to the sidebar entry and open it with Enter only.
            page.evaluate("document.activeElement && document.activeElement.blur()")
            for _ in range(30):
                page.keyboard.press("Tab")
                if page.evaluate("document.activeElement.textContent.trim()") == label and (
                    page.evaluate("document.activeElement.closest('nav') !== null")
                ):
                    break
            else:
                raise AssertionError(f"sidebar entry {label} is not reachable by keyboard")
            page.keyboard.press("Enter")
            expect(page.get_by_role("heading", name=label, exact=True)).to_be_visible()
            page.wait_for_load_state("networkidle")
            seen = []
            page.evaluate("document.activeElement && document.activeElement.blur()")
            for _ in range(25):
                page.keyboard.press("Tab")
                state = page.evaluate(FOCUS_STATE)
                if state is None:
                    continue
                seen.append(state)
                assert state["visible"], (label, state)
            assert len(seen) >= 3, (label, seen)
    finally:
        context.close()


def draft_outline(page: Page, name: str):
    return page.get_by_test_id("graph-outline").get_by_role(
        "button", name=f"{name} (Custom)", exact=True
    )


def test_signing_in_again_keeps_the_open_draft_for_the_same_user_only(browser: Browser, frame_host):
    context, page = signed_in(
        browser,
        frame_host,
        user="frame-three",
        target="/communities",
        session_seconds=290,
        token_seconds=290,
    )
    answer_identity_provider(context, frame_host, "frame-three:editor+tester")
    try:
        draft_outline(page, "alpha").click()
        page.get_by_role("group", name="Community alpha", exact=True).get_by_label(
            "Name", exact=True
        ).fill("Resign alpha")
        expect(page.get_by_test_id("editor-status")).to_have_text("Unsaved")
        warning = page.get_by_test_id("session-warning")
        expect(warning).to_be_visible()
        with page.expect_request("**/auth/callback"):
            warning.get_by_role("button", name="Stay signed in").click()
        expect(page.get_by_test_id("editor-status")).to_have_text("Unsaved", timeout=20000)
        assert urlsplit(page.url).path.rstrip("/") == "/communities", page.url
        expect(draft_outline(page, "Resign alpha")).to_be_visible()
        expect(page.get_by_test_id("session-warning")).to_have_count(0)
        # Sign-out discards the tab's drafts; the next user starts from the published graph.
        page.get_by_role("button", name="Sign out", exact=True).click()
        wait_for_login(page)
        answer_identity_provider(context, frame_host, "frame-two:editor+tester")
        page.goto(frame_host["ui"] + "/communities", wait_until="networkidle")
        page.get_by_role("link", name="Sign in", exact=True).click()
        expect(draft_outline(page, "alpha")).to_be_visible(timeout=20000)
        expect(draft_outline(page, "Resign alpha")).to_have_count(0)
        expect(page.get_by_test_id("editor-status")).not_to_have_text("Unsaved")
    finally:
        context.close()


def test_an_ended_session_returns_to_login_with_the_discarded_message(browser: Browser, frame_host):
    context, page = signed_in(browser, frame_host, session_seconds=3)
    wait_for_login(page, "expired=1", timeout=15000)
    expect(page.get_by_role("alert")).to_have_text(
        "Your session ended. Unpublished changes were discarded."
    )
    context.close()


def test_profile_and_user_settings_in_the_top_bar(browser: Browser, frame_host):
    context, page = signed_in(browser, frame_host, user="frame-two", roles="tester")
    page.get_by_role("button", name="User Profile").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_test_id("profile-name")).to_have_text("Fixture frame-two")
    expect(dialog.get_by_test_id("profile-account")).to_have_text("frame-two@fixture.example")
    expect(dialog.get_by_test_id("profile-roles")).to_have_text("tester")
    assert dialog.locator("input, select, textarea").count() == 0
    dialog.get_by_role("button", name="Close").click()
    page.get_by_role("button", name="User Settings").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("Coming soon")
    assert dialog.locator("input, select, textarea").count() == 0
    context.close()


def test_session_endpoint_reports_profile_and_expiry(frame_host):
    cookie = frame_host["sessions"]["frame-one"][0]
    trust = ssl.create_default_context(cafile=frame_host["certificate"])
    with httpx.Client(
        base_url=frame_host["management"],
        verify=trust,
        trust_env=False,
        cookies={cookie["name"]: cookie["value"]},
    ) as client:
        body = client.get("/auth/session").json()
    assert body["roles"] == ["editor", "tester"]
    assert body["pages"] == ["dashboard", "communities", "test", "status", "settings"]
    assert body["displayName"] == "Fixture frame-one"
    assert body["expiresAt"] <= body["tokenExpiresAt"]
