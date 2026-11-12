"""The exported frontend without a session: the login page at every width, nothing else."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_responsive_login_without_session(page: Page, shell_url: str, width: int) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    data: list[str] = []
    page.on(
        "request",
        lambda request: (
            data.append(request.url)
            if "/v2/configuration" in request.url or "/v1/status" in request.url
            else None
        ),
    )
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(shell_url, wait_until="networkidle")
    expect(page.get_by_role("img", name="Caller AI")).to_be_visible()
    sign_in = page.get_by_role("link", name="Sign in", exact=True)
    expect(sign_in).to_be_visible()
    expect(sign_in).to_have_attribute("href", "/auth/login?returnTo=%2F")
    expect(page.get_by_role("main")).to_be_visible()
    # The exported start page serves both modes, so it must not claim one.
    assert page.get_by_text("Local", exact=True).count() == 0
    # No management content before sign-in: no sidebar, no top bar, no page data.
    assert page.get_by_role("navigation").count() == 0
    assert page.get_by_role("banner").count() == 0
    assert data == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.textContent") == "Sign in"
    assert page.get_by_text("ReflexRuntimeError", exact=False).count() == 0
    assert errors == []
