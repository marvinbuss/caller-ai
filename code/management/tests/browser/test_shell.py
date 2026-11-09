"""Actual Reflex shell, responsive layout, keyboard access and event transport."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_responsive_connected_shell(page: Page, shell_url: str, width: int) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(shell_url, wait_until="networkidle")
    expect(page.get_by_role("heading", name="Caller AI", exact=True)).to_be_visible()
    expect(page.get_by_role("main")).to_be_visible()
    # The exported start page serves both modes, so it must not claim one.
    assert page.get_by_text("Local", exact=True).count() == 0
    expect(page.get_by_role("link", name="Open configuration")).to_have_attribute(
        "href", "/configuration/"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    heading = page.get_by_role("heading", name="Caller AI", exact=True).bounding_box()
    banner = page.get_by_role("banner").bounding_box()
    assert heading is not None and banner is not None
    assert heading["y"] >= banner["y"] + banner["height"]
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.tagName !== 'BODY'")
    status = page.get_by_test_id("connection-status")
    expect(status).to_have_text("Ready")
    page.get_by_role("button", name="Refresh status").click()
    expect(status).to_have_text("Updated", timeout=15000)
    assert page.get_by_text("An error occurred.", exact=True).count() == 0
    assert page.get_by_text("ReflexRuntimeError", exact=False).count() == 0
    assert not errors
