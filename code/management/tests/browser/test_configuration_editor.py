"""Real-browser editor contracts; identities are issued only by an owned test host."""

import importlib.util
import ssl
import sys
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Locator, Page, expect

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def editor_host():
    from tests.conftest import configuration_graph_document

    path = Path(__file__).resolve().parents[3] / "tools/scripts/smoke.py"
    specification = importlib.util.spec_from_file_location("caller_editor_smoke", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    with module.running_configuration_editor(
        graph=configuration_graph_document(),
        identities=("manager-one", "manager-two"),
        mode="local",
    ) as host:
        yield host


@pytest.fixture
def configuration_host(editor_host, configuration_graph):
    cookie = editor_host["sessions"]["manager-one"][0]
    context = ssl.create_default_context(cafile=editor_host["certificate"])
    with httpx.Client(
        base_url=editor_host["management"],
        verify=context,
        trust_env=False,
        cookies={cookie["name"]: cookie["value"]},
    ) as client:
        current = client.get("/v1/configuration")
        current.raise_for_status()
        csrf = client.get("/auth/session").json()["csrf"]
        restored = client.put(
            "/v1/configuration",
            json=configuration_graph,
            headers={
                "If-Match": current.headers["ETag"],
                "X-CSRF-Token": csrf,
                "Origin": editor_host["ui"],
            },
        )
        restored.raise_for_status()
    return editor_host


def open_manager(browser: Browser, host: dict, actor: str):
    context = browser.new_context(ignore_https_errors=True)
    context.add_cookies(host["sessions"][actor])
    assert context.request.get(host["management"] + "/v1/configuration").status == 200
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(type(error).__name__))
    page.goto(host["ui"] + "/configuration", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Configuration", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Save configuration", exact=True)).to_be_visible()
    assert not errors
    return context, page


def keyboard_reach(page: Page, control: Locator) -> None:
    expect(control).to_be_visible()
    backwards = control.evaluate(
        "element => Boolean(element.compareDocumentPosition(document.activeElement) & "
        "Node.DOCUMENT_POSITION_FOLLOWING)"
    )
    for _ in range(320):
        if control.evaluate("element => element === document.activeElement"):
            assert control.evaluate("""element => {
                const rect = element.getBoundingClientRect();
                const left = Math.max(0, rect.left);
                const right = Math.min(innerWidth, rect.right);
                const topEdge = Math.max(0, rect.top);
                const bottom = Math.min(innerHeight, rect.bottom);
                const x = (left + right) / 2;
                const y = (topEdge + bottom) / 2;
                const top = document.elementFromPoint(x, y);
                return right > left && bottom > topEdge &&
                    (top === element || element.contains(top));
            }""")
            return
        page.keyboard.press("Shift+Tab" if backwards else "Tab")
    raise AssertionError("Control is not keyboard reachable")


def test_keyboard_reachability_helper_uses_real_tab_order_and_detects_occlusion(page: Page) -> None:
    page.set_content(
        '<label>First<input id="first"></label><label>Second<input id="second"></label>'
        '<button id="last">Last</button>'
    )
    keyboard_reach(page, page.locator("#last"))
    keyboard_reach(page, page.locator("#first"))
    assert page.locator("#first").evaluate("element => element === document.activeElement")
    page.evaluate("""() => {
        const rect = document.querySelector('#first').getBoundingClientRect();
        const cover = document.createElement('div');
        Object.assign(cover.style, {position:'fixed', left:rect.x+'px', top:rect.y+'px',
            width:rect.width+'px', height:rect.height+'px', zIndex:999});
        document.body.appendChild(cover);
    }""")
    with pytest.raises(AssertionError):
        keyboard_reach(page, page.locator("#first"))


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_keyboard_only_create_edit_assign_and_save(
    browser: Browser, configuration_host, width: int
) -> None:
    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        form = page.get_by_role("form", name="New community", exact=True)
        for label, value in (
            ("Community name", "Keyboard community"),
            ("Routing description", "Synthetic keyboard routing"),
            ("Entry agent name", "Keyboard entry"),
            ("Entry agent prompt", "Synthetic keyboard entry"),
        ):
            keyboard_reach(page, form.get_by_label(label, exact=True))
            page.keyboard.type(value)
        keyboard_reach(page, form.get_by_role("button", name="Add community", exact=True))
        page.keyboard.press("Enter")
        expect(page.get_by_role("heading", name="Keyboard community", exact=True)).to_be_visible()
        prompt = page.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
            "Prompt", exact=True
        )
        keyboard_reach(page, prompt)
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.type("Synthetic keyboard prompt edit")
        for selector, expected_value, button in (
            ("Reviewed MCP connection", "connection-one", "Add MCP connection"),
            ("Reviewed tool", "tool-public", "Add tool"),
        ):
            select = page.get_by_label(selector, exact=True)
            keyboard_reach(page, select)
            page.keyboard.press("Home")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            expect(select).to_have_value(expected_value)
            keyboard_reach(page, page.get_by_role("button", name=button, exact=True))
            page.keyboard.press("Enter")
            group_name = (
                "MCP connection Reviewed connection"
                if expected_value == "connection-one"
                else "Tool Public lookup"
            )
            enabled = page.get_by_role("group", name=group_name, exact=True).get_by_label(
                "Enabled", exact=True
            )
            keyboard_reach(page, enabled)
            page.keyboard.press("Space")
            expect(enabled).to_be_checked()
        action = page.get_by_role(
            "group", name="Assignments for agent-alpha", exact=True
        ).get_by_label("lookup", exact=True)
        keyboard_reach(page, action)
        page.keyboard.press("Space")
        expect(action).to_be_checked()
        community_name = page.get_by_role(
            "group", name="Community Keyboard community", exact=True
        ).get_by_label("Name", exact=True)
        expect(community_name).to_have_value("Keyboard community")
        expect(page.get_by_role("status")).to_have_text("Unsaved")
        keyboard_reach(page, page.get_by_role("button", name="Save configuration", exact=True))
        page.keyboard.press("Enter")
        expect(page.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        expect(community_name).to_have_value("Keyboard community")
        current = context.request.get(configuration_host["management"] + "/v1/configuration").json()
        assert any(item["name"] == "Keyboard community" for item in current["communities"])
        agent = next(item for item in current["agents"] if item["agentId"] == "agent-alpha")
        assert agent["prompt"] == "Synthetic keyboard prompt edit"
        assert agent["capabilityAssignments"][0]["permittedActionNames"] == ["lookup"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_responsive_keyboard_editing_and_protected_roles(
    browser: Browser, configuration_host, width: int
) -> None:
    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_viewport_size({"width": width, "height": 900})
        expect(page.get_by_role("button", name="Save configuration", exact=True)).to_be_visible()
        for name in ("alpha", "beta"):
            expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
        for name in ("agent-welcome", "agent-routing", "agent-goodbye"):
            protected = page.get_by_role("group", name=name, exact=True)
            expect(protected.get_by_label("Enabled", exact=True)).to_be_checked()
            expect(protected.get_by_label("Enabled", exact=True)).to_be_disabled()
            expect(protected.get_by_role("button", name="Delete agent", exact=True)).to_have_count(
                0
            )
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.tagName !== 'BODY'")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        group = page.get_by_role("group", name="agent-alpha", exact=True)
        group.get_by_label("Prompt", exact=True).fill("Synthetic accessible edit")
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        expect(group.get_by_label("Prompt", exact=True)).to_have_value("Synthetic accessible edit")
        assert page.get_by_text("An error occurred.", exact=True).count() == 0
        assert not errors
        output = Path(__file__).resolve().parents[2] / "test-results"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / f"editor-{width}.png"), full_page=True)
    finally:
        context.close()


def test_two_managers_share_configuration_and_conflicts_preserve_unsaved_edits(
    browser: Browser, configuration_host
) -> None:
    first_context, first = open_manager(browser, configuration_host, "manager-one")
    second_context, second = open_manager(browser, configuration_host, "manager-two")
    try:
        first_prompt = first.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
            "Prompt", exact=True
        )
        second_prompt = second.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
            "Prompt", exact=True
        )
        first_prompt.fill("Synthetic first manager committed edit")
        second_prompt.fill("Synthetic second manager unsaved edit")
        first.get_by_role("button", name="Save configuration", exact=True).click()
        expect(first.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        second.get_by_role("button", name="Save configuration", exact=True).click()
        expect(second.get_by_role("alert")).to_contain_text("conflict", ignore_case=True)
        expect(second_prompt).to_have_value("Synthetic second manager unsaved edit")
        first.reload(wait_until="networkidle")
        expect(
            first.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
                "Prompt", exact=True
            )
        ).to_have_value("Synthetic first manager committed edit")
        second.get_by_role("button", name="Reload configuration", exact=True).click()
        dialog = second.get_by_role("alertdialog")
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        expect(second_prompt).to_have_value("Synthetic second manager unsaved edit")
        second.get_by_role("button", name="Reload configuration", exact=True).click()
        second.get_by_role("alertdialog").get_by_role("button", name="Reload", exact=True).click()
        expect(second_prompt).to_have_value("Synthetic first manager committed edit")
    finally:
        first_context.close()
        second_context.close()


def test_field_errors_are_accessible_and_leave_other_edits_intact(
    browser: Browser, configuration_host
) -> None:
    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        group = page.get_by_role("group", name="agent-alpha", exact=True)
        name = group.get_by_label("Name", exact=True)
        prompt = group.get_by_label("Prompt", exact=True)
        name.fill("")
        prompt.fill("Synthetic retained edit")
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(name).to_have_attribute("aria-invalid", "true")
        assert name.get_attribute("aria-describedby")
        expect(page.get_by_role("alert")).to_be_visible()
        expect(prompt).to_have_value("Synthetic retained edit")
    finally:
        context.close()


def test_anonymous_context_cannot_use_editor_or_configuration_api(
    browser: Browser, configuration_host
) -> None:
    with browser.new_context(ignore_https_errors=True) as context:
        markup = context.request.get(configuration_host["ui"] + "/configuration")
        assert markup.status == 200
        for private_value in (
            "Synthetic approved prompt",
            "secret://fixture-connection",
            "Public lookup",
            "Reviewed connection",
        ):
            assert private_value not in markup.text()
        page: Page = context.new_page()
        page.goto(configuration_host["ui"] + "/configuration", wait_until="networkidle")
        expect(page.get_by_role("button", name="Save configuration", exact=True)).to_have_count(0)
        response = context.request.get(configuration_host["management"] + "/v1/configuration")
        assert response.status == 401
        assert "Synthetic approved prompt" not in response.text()


def test_explicit_local_fixture_login_uses_real_session_and_rejects_unknown_managers(
    browser: Browser, configuration_host
) -> None:
    with browser.new_context(ignore_https_errors=True) as context:
        assert (
            context.request.get(
                configuration_host["management"] + "/_test/login/unregistered"
            ).status
            == 404
        )
        page = context.new_page()
        page.goto(configuration_host["ui"] + "/_test/login/manager-one", wait_until="networkidle")
        expect(page.get_by_role("button", name="Save configuration", exact=True)).to_be_visible()
        cookie = next(item for item in context.cookies() if item["name"] == "__Host-caller-session")
        assert cookie["secure"] and cookie["httpOnly"] and cookie["sameSite"] == "Strict"


def test_community_and_agent_create_entry_edit_and_confirmed_delete(
    browser: Browser, configuration_host
) -> None:
    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        for label, value in (
            ("Community name", "gamma"),
            ("Routing description", "Synthetic gamma route"),
            ("Entry agent name", "Gamma entry"),
            ("Entry agent prompt", "Synthetic entry prompt"),
        ):
            page.get_by_role("form", name="New community", exact=True).get_by_label(
                label, exact=True
            ).fill(value)
        page.get_by_role("button", name="Add community", exact=True).click()
        expect(page.get_by_role("heading", name="gamma", exact=True)).to_be_visible()
        page.get_by_label("New agent name", exact=True).fill("Gamma helper")
        page.get_by_label("New agent community", exact=True).select_option(label="gamma")
        page.get_by_label("New agent prompt", exact=True).fill("Synthetic helper prompt")
        page.get_by_role("button", name="Add agent", exact=True).click()
        helper = page.get_by_role("group", name="Gamma helper", exact=True)
        expect(helper).to_be_visible()
        community = page.get_by_role("group", name="Community gamma", exact=True)
        community.get_by_label("Entry agent", exact=True).select_option(label="Gamma helper")
        page.get_by_role("group", name="Gamma entry", exact=True).get_by_role(
            "button", name="Delete agent", exact=True
        ).click()
        expect(page.get_by_role("group", name="Gamma entry", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        page.reload(wait_until="networkidle")
        expect(page.get_by_role("group", name="Gamma helper", exact=True)).to_be_visible()
        community.get_by_role("button", name="Delete community", exact=True).click()
        page.get_by_role("alertdialog").get_by_role("button", name="Cancel", exact=True).click()
        expect(community).to_be_visible()
        community.get_by_role("button", name="Delete community", exact=True).click()
        page.get_by_role("alertdialog").get_by_role(
            "button", name="Delete community", exact=True
        ).click()
        expect(page.get_by_role("heading", name="gamma", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        page.reload(wait_until="networkidle")
        expect(page.get_by_role("heading", name="gamma", exact=True)).to_have_count(0)
        expect(page.get_by_role("group", name="agent-welcome", exact=True)).to_be_visible()
    finally:
        context.close()


def test_test_host_refuses_live_identity_injection(configuration_graph) -> None:
    path = Path(__file__).resolve().parents[3] / "tools/scripts/smoke.py"
    specification = importlib.util.spec_from_file_location("caller_editor_smoke_live", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    with pytest.raises(ValueError, match="test_identity_live_mode_prohibited"):
        with module.running_configuration_editor(
            graph=configuration_graph, identities=("manager-one",), mode="live"
        ):
            pytest.fail("A test identity must never enable live management access")


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_reviewed_capability_catalog_assignment_save_and_removal(
    browser: Browser, configuration_host, width: int
) -> None:
    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.get_by_label("Reviewed MCP connection", exact=True).select_option(
            label="Reviewed connection"
        )
        page.get_by_role("button", name="Add MCP connection", exact=True).click()
        connection = page.get_by_role(
            "group", name="MCP connection Reviewed connection", exact=True
        )
        expect(connection.get_by_label("Enabled", exact=True)).not_to_be_checked()
        expect(connection.get_by_label("Credential reference", exact=True)).to_have_value(
            "secret://fixture-connection"
        )
        assert (
            connection.get_by_label("Credential reference", exact=True).evaluate(
                "element => element.tagName"
            )
            == "SELECT"
        )
        connection.get_by_label("Enabled", exact=True).click()
        expect(connection.get_by_label("Enabled", exact=True)).to_be_checked()
        page.get_by_label("Reviewed tool", exact=True).select_option(label="Public lookup")
        page.get_by_role("button", name="Add tool", exact=True).click()
        tool = page.get_by_role("group", name="Tool Public lookup", exact=True)
        expect(tool.get_by_label("Enabled", exact=True)).not_to_be_checked()
        expect(tool.get_by_text("public", exact=True)).to_be_visible()
        expect(tool.get_by_text("read_only", exact=True)).to_be_visible()
        tool.get_by_label("Enabled", exact=True).click()
        expect(tool.get_by_label("Enabled", exact=True)).to_be_checked()
        assigned = page.get_by_role("group", name="Assignments for agent-alpha", exact=True)
        assigned.get_by_label("lookup", exact=True).click()
        expect(assigned.get_by_label("lookup", exact=True)).to_be_checked()
        assigned.get_by_label("Input constraints", exact=True).fill('{"limit":5}')
        assigned.get_by_role("button", name="Apply constraints", exact=True).click()
        for role in ("welcome", "routing", "goodbye"):
            expect(
                page.get_by_role("group", name="Assignments for agent-" + role, exact=True)
            ).to_have_count(0)
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        page.reload(wait_until="networkidle")
        expect(assigned.get_by_label("lookup", exact=True)).to_be_checked()
        expect(assigned.get_by_label("Input constraints", exact=True)).to_have_value('{"limit": 5}')
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("heading", name="Capabilities", exact=True).scroll_into_view_if_needed()
        output = Path(__file__).resolve().parents[2] / "test-results"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / f"capabilities-{width}.png"), full_page=True)
        tool.get_by_role("button", name="Remove tool", exact=True).click()
        page.get_by_role("alertdialog").get_by_role(
            "button", name="Remove tool", exact=True
        ).click()
        expect(tool).to_have_count(0)
        expect(assigned.get_by_label("lookup", exact=True)).to_have_count(0)
        connection.get_by_role("button", name="Remove MCP connection", exact=True).click()
        page.get_by_role("alertdialog").get_by_role(
            "button", name="Remove connection", exact=True
        ).click()
        expect(connection).to_have_count(0)
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        page.reload(wait_until="networkidle")
        expect(page.get_by_role("group", name="Tool Public lookup", exact=True)).to_have_count(0)
        expect(
            page.get_by_role("group", name="MCP connection Reviewed connection", exact=True)
        ).to_have_count(0)
    finally:
        context.close()


def test_api_cannot_self_approve_unreviewed_catalog_action(
    browser: Browser, configuration_host
) -> None:
    from tests.conftest import capability_graph_document

    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        base = context.request.get(configuration_host["management"] + "/v1/configuration")
        csrf = context.request.get(configuration_host["management"] + "/auth/session").json()[
            "csrf"
        ]
        forged = capability_graph_document()
        forged["mcpConnections"][0]["reviewedCapabilityCatalog"][0]["actionName"] = (
            "unreviewed-action"
        )
        forged["agents"][3]["capabilityAssignments"][0]["permittedActionNames"] = [
            "unreviewed-action"
        ]
        response = context.request.put(
            configuration_host["management"] + "/v1/configuration",
            data=forged,
            headers={
                "If-Match": base.headers["etag"],
                "Origin": configuration_host["ui"],
                "X-CSRF-Token": csrf,
            },
        )
        assert response.status == 422
        assert "unreviewed-action" not in response.text()
        current = context.request.get(configuration_host["management"] + "/v1/configuration")
        assert current.json()["id"] == base.json()["id"]
        assert current.json()["tools"] == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_relationship_policy_context_edit_save_and_confirmed_delete(
    browser: Browser, configuration_host, width: int
) -> None:
    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.get_by_label("New agent name", exact=True).fill("Alpha peer")
        page.get_by_label("New agent community", exact=True).select_option(label="alpha")
        page.get_by_label("New agent prompt", exact=True).fill("Synthetic peer prompt")
        page.get_by_role("button", name="Add agent", exact=True).click()
        expect(page.get_by_role("group", name="Alpha peer", exact=True)).to_be_visible()
        source = page.get_by_label("Source agent", exact=True)
        assert "agent-goodbye" not in source.locator("option").all_text_contents()
        source.select_option(label="agent-alpha")
        target = page.get_by_label("Destination agent", exact=True)
        expect(target.locator("option")).to_have_text(["Select destination agent", "Alpha peer"])
        required = page.get_by_role(
            "group", name="Relationship agent-alpha to agent-routing", exact=True
        )
        expect(
            required.get_by_role("button", name="Remove relationship", exact=True)
        ).to_be_disabled()
        target.select_option(label="Alpha peer")
        page.get_by_role("button", name="Add relationship", exact=True).click()
        relationship = page.get_by_role(
            "group", name="Relationship agent-alpha to Alpha peer", exact=True
        )
        expect(relationship).to_be_visible()
        relationship.get_by_label("Transfer reason", exact=True).click()
        expect(relationship.get_by_label("Transfer reason", exact=True)).not_to_be_checked()
        expect(relationship.get_by_label("Request summary", exact=True)).to_be_checked()
        assert relationship.get_by_role("checkbox").count() == 2
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        page.reload(wait_until="networkidle")
        expect(relationship.get_by_label("Transfer reason", exact=True)).not_to_be_checked()
        expect(
            required.get_by_role("button", name="Remove relationship", exact=True)
        ).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("heading", name="Relationships", exact=True).scroll_into_view_if_needed()
        output = Path(__file__).resolve().parents[2] / "test-results"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / f"relationships-{width}.png"), full_page=True)
        relationship.get_by_role("button", name="Remove relationship", exact=True).click()
        page.get_by_role("alertdialog").get_by_role("button", name="Cancel", exact=True).click()
        expect(relationship).to_be_visible()
        relationship.get_by_role("button", name="Remove relationship", exact=True).click()
        page.get_by_role("alertdialog").get_by_role(
            "button", name="Remove relationship", exact=True
        ).click()
        expect(relationship).to_have_count(0)
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        page.reload(wait_until="networkidle")
        expect(relationship).to_have_count(0)
        expect(required).to_be_visible()
    finally:
        context.close()


@pytest.mark.parametrize(
    "mutation", ["foreign-direct", "raw-context", "remove-required", "routing-nonentry"]
)
def test_api_rejects_forged_relationship_policy_changes(
    browser: Browser, configuration_host, mutation: str
) -> None:
    from tests.conftest import configuration_graph_document

    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        current = context.request.get(configuration_host["management"] + "/v1/configuration")
        csrf = context.request.get(configuration_host["management"] + "/auth/session").json()[
            "csrf"
        ]
        graph = configuration_graph_document()
        if mutation == "remove-required":
            graph["relationships"].pop(0)
        elif mutation == "raw-context":
            graph["relationships"][0]["allowedContextFields"] = ["conversation"]
        elif mutation == "foreign-direct":
            graph["relationships"].append(
                {
                    "relationshipId": "forged-path",
                    "sourceAgentId": "agent-alpha",
                    "targetAgentId": "agent-beta",
                    "allowedContextFields": [],
                }
            )
        else:
            from management_app.state.configuration_editor import EditorDraft

            draft = EditorDraft.from_revision(graph, current.headers["etag"])
            peer = draft.add_agent("community-alpha", "Alpha peer", "Synthetic peer prompt")
            graph = draft.graph
            graph["relationships"].append(
                {
                    "relationshipId": "forged-path",
                    "sourceAgentId": "agent-routing",
                    "targetAgentId": peer,
                    "allowedContextFields": [],
                }
            )
        response = context.request.put(
            configuration_host["management"] + "/v1/configuration",
            data=graph,
            headers={
                "If-Match": current.headers["etag"],
                "Origin": configuration_host["ui"],
                "X-CSRF-Token": csrf,
            },
        )
        assert response.status == 422
        assert (
            context.request.get(configuration_host["management"] + "/v1/configuration").json()["id"]
            == current.json()["id"]
        )
    finally:
        context.close()


def test_conflict_retains_relationship_context_and_agent_edits_across_views(
    browser: Browser, configuration_host
) -> None:
    first_context, first = open_manager(browser, configuration_host, "manager-one")
    second_context, second = open_manager(browser, configuration_host, "manager-two")
    try:
        first.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
            "Prompt", exact=True
        ).fill("Synthetic first committed revision")
        second_prompt = second.get_by_role("group", name="agent-beta", exact=True).get_by_label(
            "Prompt", exact=True
        )
        second_prompt.fill("Synthetic second unsaved revision")
        relationship = second.get_by_role(
            "group", name="Relationship agent-beta to agent-routing", exact=True
        )
        relationship.get_by_label("Transfer reason", exact=True).click()
        expect(relationship.get_by_label("Transfer reason", exact=True)).not_to_be_checked()
        first.get_by_role("button", name="Save configuration", exact=True).click()
        expect(first.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        second.get_by_role("button", name="Save configuration", exact=True).click()
        expect(second.get_by_role("alert")).to_contain_text("conflict", ignore_case=True)
        expect(second_prompt).to_have_value("Synthetic second unsaved revision")
        expect(relationship.get_by_label("Transfer reason", exact=True)).not_to_be_checked()
        current = first_context.request.get(
            configuration_host["management"] + "/v1/configuration"
        ).json()
        assert (
            next(agent for agent in current["agents"] if agent["agentId"] == "agent-beta")["prompt"]
            == "Synthetic approved prompt"
        )
        second.get_by_role("button", name="Reload configuration", exact=True).click()
        second.get_by_role("alertdialog").get_by_role("button", name="Reload", exact=True).click()
        expect(second_prompt).to_have_value("Synthetic approved prompt")
        expect(relationship.get_by_label("Transfer reason", exact=True)).to_be_checked()
    finally:
        first_context.close()
        second_context.close()


def test_editor_secret_validation_has_field_only_errors_and_no_partial_save(
    browser: Browser, configuration_host
) -> None:
    from uuid import uuid4

    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        before = context.request.get(configuration_host["management"] + "/v1/configuration").json()
        candidate = uuid4().hex
        prompt = page.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
            "Prompt", exact=True
        )
        prompt.fill("=".join(("Password", candidate)))
        page.get_by_role("button", name="Save configuration", exact=True).click()
        alert = page.get_by_role("alert")
        expect(alert).to_contain_text("agents[3].prompt")
        expect(alert).to_contain_text("credential_literal_prohibited")
        assert candidate not in alert.inner_text()
        expect(prompt).to_have_attribute("aria-invalid", "true")
        after = context.request.get(configuration_host["management"] + "/v1/configuration").json()
        assert after["id"] == before["id"]
        assert candidate not in str(after)
    finally:
        context.close()
