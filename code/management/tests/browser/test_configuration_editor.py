"""The Community and Agent page in a real browser against an owned test host."""

import copy
import ssl
import time

import httpx
import pytest
from playwright.sync_api import Browser, Locator, Page, expect

pytestmark = pytest.mark.browser

EDITORS = ("manager-one", "manager-two", "manager-three")


@pytest.fixture(scope="module")
def editor_host(editor_host_factory):
    from tests.conftest import configuration_graph_document

    with editor_host_factory(
        graph=configuration_graph_document(), identities=EDITORS, mode="local"
    ) as host:
        yield host


def api_client(host: dict, actor: str = "manager-one") -> httpx.Client:
    cookie = host["sessions"][actor][0]
    return httpx.Client(
        base_url=host["management"],
        verify=ssl.create_default_context(cafile=host["certificate"]),
        trust_env=False,
        cookies={cookie["name"]: cookie["value"]},
    )


def publish_graph(host: dict, graph: dict, actor: str = "manager-one") -> dict:
    with api_client(host, actor) as client:
        current = client.get("/v2/configuration")
        current.raise_for_status()
        csrf = client.get("/auth/session").json()["csrf"]
        saved = client.put(
            "/v2/configuration",
            json=graph,
            headers={
                "If-Match": current.headers["ETag"],
                "X-CSRF-Token": csrf,
                "Origin": host["ui"],
            },
        )
        saved.raise_for_status()
        return saved.json()


def set_fault(host: dict, path: str, *, delay: float = 0, fail: bool = False) -> None:
    with api_client(host) as client:
        client.get("/_test/fault", params={"path": path, "delay": delay, "fail": fail})


@pytest.fixture
def configuration_host(editor_host, configuration_graph):
    set_fault(editor_host, "/v2/configuration")
    publish_graph(editor_host, configuration_graph)
    return editor_host


def open_editor(browser: Browser, host: dict, actor: str = "manager-one"):
    context = browser.new_context(ignore_https_errors=True)
    context.add_cookies(host["sessions"][actor])
    page = context.new_page()
    page.goto(host["ui"] + "/communities", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Community and Agent", exact=True)).to_be_visible()
    expect(page.get_by_test_id("graph")).to_be_visible()
    return context, page


def outline(page: Page, label: str) -> Locator:
    return page.get_by_test_id("graph-outline").get_by_role("button", name=label, exact=True)


def node(page: Page, identifier: str) -> Locator:
    return page.locator(f'.react-flow__node[data-id="{identifier}"]')


def draw_link(page: Page, source: str, target: str) -> None:
    node(page, source).locator(".react-flow__handle.source").first.drag_to(
        node(page, target).locator(".react-flow__handle.target").first
    )


def status(page: Page) -> Locator:
    return page.get_by_test_id("editor-status")


def refused(page: Page, text: str) -> None:
    expect(page.get_by_test_id("editor-error")).to_contain_text(text)
    expect(status(page)).not_to_have_text("Unsaved")


def publish(page: Page) -> None:
    page.get_by_role("button", name="Publish", exact=True).click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog.get_by_test_id("publish-summary")).to_contain_text("Publishing creates revision")
    dialog.get_by_role("button", name="Publish now", exact=True).click()
    expect(status(page)).to_contain_text("Published revision")


@pytest.mark.parametrize("width", [360, 1440])
def test_default_and_custom_nodes_differ_beyond_color(
    browser: Browser, configuration_host, width: int
):
    context, page = open_editor(browser, configuration_host)
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        for identifier in ("c-default-welcome", "c-default-routing", "c-default-goodbye"):
            expect(node(page, identifier).locator("[data-badge='Default']")).to_be_visible()
        expect(node(page, "c-community-alpha").locator("[data-badge='Custom']")).to_be_visible()
        expect(node(page, "a-agent-alpha").locator("[data-badge='Entry']")).to_be_visible()
        expect(node(page, "a-agent-welcome").locator("[data-badge='Default']")).to_be_visible()
        expect(node(page, "c-default-goodbye")).to_contain_text("Reachable from every community")
        # A grayscale render still tells them apart by border style, icon and label.
        page.add_style_tag(content="html { filter: grayscale(1); }")
        styles = page.evaluate("""() => Object.fromEntries(
            [...document.querySelectorAll('.caller-node')].map(element => [
                element.closest('.react-flow__node').dataset.id,
                [getComputedStyle(element).borderStyle, element.textContent]
            ]))""")
        assert styles["c-default-welcome"][0] == "dashed"
        assert styles["c-community-alpha"][0] == "solid"
        assert "\U0001f512" in styles["c-default-welcome"][1]
        assert "\u270e" in styles["c-community-alpha"][1]
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_selection_opens_the_matching_settings_panel(browser: Browser, configuration_host):
    context, page = open_editor(browser, configuration_host)
    try:
        for test_id in ("agent-settings", "community-settings", "default-settings"):
            expect(page.get_by_test_id(test_id)).to_have_count(0)
        outline(page, "agent-alpha (Custom, Entry)").click()
        panel = page.get_by_test_id("agent-settings")
        expect(panel.get_by_label("Enabled", exact=True)).to_be_checked()
        expect(panel.get_by_test_id("agent-last-changed")).to_contain_text("Revision ")
        node(page, "c-community-alpha").locator("strong").click()
        expect(page.get_by_test_id("community-settings")).to_be_visible()
        expect(page.get_by_role("group", name="Community alpha", exact=True)).to_be_visible()
        outline(page, "Routing (Default)").click()
        expect(page.get_by_test_id("default-settings")).to_contain_text("fixed")
        assert page.get_by_test_id("default-settings").locator("input").count() == 0
    finally:
        context.close()


def test_each_forbidden_action_names_its_rule_and_changes_nothing(
    browser: Browser, configuration_host
):
    context, page = open_editor(browser, configuration_host)
    try:
        draw_link(page, "c-default-welcome", "c-default-goodbye")
        refused(page, "default community Welcome")
        draw_link(page, "a-agent-alpha", "a-agent-beta")
        refused(page, "different communities")
        outline(page, "agent-alpha (Custom, Entry)").click()
        panel = page.get_by_test_id("agent-settings")
        panel.get_by_label("Enabled", exact=True).click()
        refused(page, "agent-alpha is the entry agent of alpha")
        expect(panel.get_by_label("Enabled", exact=True)).to_be_checked()
        panel.get_by_label("Community", exact=True).select_option(label="beta")
        refused(page, "agent-alpha is the entry agent of alpha")
        panel.get_by_role("button", name="Delete agent", exact=True).click()
        page.get_by_role("alertdialog").get_by_role(
            "button", name="Delete agent", exact=True
        ).click()
        refused(page, "Choose another entry agent first")
        expect(outline(page, "agent-alpha (Custom, Entry)")).to_be_visible()
        outline(page, "agent-welcome (Default, Entry)").click()
        panel = page.get_by_test_id("agent-settings")
        expect(panel).to_contain_text("Pre-defined agent")
        expect(panel.get_by_label("Enabled", exact=True)).to_be_disabled()
        expect(panel).to_contain_text("Pre-defined agents cannot receive tools.")
        expect(panel).to_contain_text("Default communities have no handovers.")
        page.get_by_text("Add a community", exact=True).click()
        form = page.get_by_role("form", name="New community", exact=True)
        for label, value in (
            ("Community name", "Routing"),
            ("Routing description", "Synthetic"),
            ("Entry agent name", "Synthetic entry"),
            ("Entry agent prompt", "Synthetic prompt"),
        ):
            form.get_by_label(label, exact=True).fill(value)
        form.get_by_role("button", name="Add community", exact=True).click()
        refused(page, "Routing is the name of a default community")
        with api_client(configuration_host) as client:
            current = client.get("/v2/configuration").json()
        assert current["revisionNumber"] >= 1 and len(current["communities"]) == 2
    finally:
        context.close()


def test_loading_indicator_and_retry_after_a_failed_load(browser: Browser, configuration_host):
    context = browser.new_context(ignore_https_errors=True)
    context.add_cookies(configuration_host["sessions"]["manager-two"])
    page = context.new_page()
    try:
        set_fault(configuration_host, "/v2/configuration", delay=2.5)
        page.goto(configuration_host["ui"] + "/communities")
        expect(page.get_by_test_id("page-loading")).to_be_visible()
        expect(page.get_by_test_id("graph")).to_be_visible(timeout=15000)
        context.close()
        context = browser.new_context(ignore_https_errors=True)
        context.add_cookies(configuration_host["sessions"]["manager-three"])
        page = context.new_page()
        set_fault(configuration_host, "/v2/configuration", fail=True)
        page.goto(configuration_host["ui"] + "/communities", wait_until="networkidle")
        error = page.get_by_test_id("page-error")
        expect(error).to_contain_text("Configuration unavailable")
        set_fault(configuration_host, "/v2/configuration")
        error.get_by_role("button", name="Retry", exact=True).click()
        expect(page.get_by_test_id("graph")).to_be_visible()
        expect(page.get_by_test_id("page-error")).to_have_count(0)
    finally:
        set_fault(configuration_host, "/v2/configuration")
        context.close()


def test_deleting_a_connected_community_names_the_removed_connections(
    browser: Browser, configuration_host
):
    context, page = open_editor(browser, configuration_host)
    try:
        outline(page, "alpha (Custom)").click()
        settings = page.get_by_test_id("community-settings")
        settings.get_by_label("Destination community", exact=True).select_option(label="beta")
        settings.get_by_role("button", name="Add connection", exact=True).click()
        expect(settings.get_by_role("listitem")).to_contain_text("alpha")
        outline(page, "beta (Custom)").click()
        page.get_by_role("group", name="Community beta", exact=True).get_by_role(
            "button", name="Delete community", exact=True
        ).click()
        dialog = page.get_by_role("alertdialog")
        expect(dialog).to_contain_text("alpha to beta")
        dialog.get_by_role("button", name="Delete community", exact=True).click()
        expect(outline(page, "beta (Custom)")).to_have_count(0)
        expect(node(page, "c-community-beta")).to_have_count(0)
        expect(status(page)).to_have_text("Unsaved")
    finally:
        context.close()


def test_create_connect_change_entry_handover_and_publish_flow(
    browser: Browser, configuration_host
):
    started = time.monotonic()
    context, page = open_editor(browser, configuration_host)
    try:
        page.get_by_text("Add a community", exact=True).click()
        form = page.get_by_role("form", name="New community", exact=True)
        for label, value in (
            ("Community name", "gamma"),
            ("Routing description", "Synthetic gamma route"),
            ("Entry agent name", "Gamma entry"),
            ("Entry agent prompt", "Synthetic entry prompt"),
        ):
            form.get_by_label(label, exact=True).fill(value)
        form.get_by_role("button", name="Add community", exact=True).click()
        community = page.get_by_role("group", name="Community gamma", exact=True)
        expect(community).to_be_visible()
        community.get_by_label("Routable from Routing").click()
        expect(community.get_by_label("Routable from Routing")).not_to_be_checked()
        settings = page.get_by_test_id("community-settings")
        settings.get_by_label("New agent name", exact=True).fill("Gamma helper")
        settings.get_by_label("New agent prompt", exact=True).fill("Synthetic helper prompt")
        settings.get_by_role("button", name="Add agent", exact=True).click()
        expect(outline(page, "Gamma helper (Custom)")).to_be_visible()
        gamma = page.evaluate(
            "[...document.querySelectorAll('.react-flow__node')].map(n => n.dataset.id)"
            ".filter(id => id.startsWith('c-community-'))"
        )
        gamma_id = next(
            item for item in gamma if item not in {"c-community-alpha", "c-community-beta"}
        )
        draw_link(page, "c-default-routing", gamma_id)
        outline(page, "gamma (Custom)").click()
        community = page.get_by_role("group", name="Community gamma", exact=True)
        expect(community.get_by_label("Routable from Routing")).to_be_checked()
        community.get_by_label("Entry agent", exact=True).select_option(label="Gamma helper")
        agents = page.evaluate(
            "[...document.querySelectorAll('.react-flow__node')].filter(n => "
            "n.textContent.includes('Gamma')).map(n => [n.dataset.id, n.textContent])"
        )
        entry = next(identifier for identifier, text in agents if "Gamma entry" in text)
        helper = next(identifier for identifier, text in agents if "Gamma helper" in text)
        draw_link(page, helper, entry)
        expect(page.locator('.react-flow__edge[data-id^="h-"]')).to_have_count(1)
        publish(page)
        with api_client(configuration_host) as client:
            published = client.get("/v2/configuration").json()
        expect(status(page)).to_have_text(f"Published revision {published['revisionNumber']}")
        names = {row["name"]: row for row in published["communities"]}
        assert names["gamma"]["routable"] is True
        assert time.monotonic() - started < 300
        outline(page, "gamma (Custom)").click()
        page.get_by_role("group", name="Community gamma", exact=True).get_by_label(
            "Routable from Routing"
        ).click()
        expect(page.get_by_role("group", name="Community gamma", exact=True)).to_contain_text(
            "Unreachable"
        )
        expect(node(page, gamma_id).locator("[data-badge='Unreachable']")).to_be_visible()
        publish(page)
        expect(status(page)).to_contain_text("unreachable: gamma")
    finally:
        context.close()


def test_view_is_browser_local_bounded_and_keyboard_operable(browser: Browser, configuration_host):
    context, page = open_editor(browser, configuration_host)
    try:
        boxes = page.evaluate("""() => [...document.querySelectorAll('.react-flow__node')]
            .filter(n => n.dataset.id.startsWith('c-')).map(n => {
                const r = n.getBoundingClientRect(); return [r.x, r.y, r.right, r.bottom];
            })""")
        for index, first in enumerate(boxes):
            for second in boxes[index + 1 :]:
                assert (
                    first[2] <= second[0]
                    or second[2] <= first[0]
                    or first[3] <= second[1]
                    or second[3] <= first[1]
                ), "Automatically placed communities overlap"
        target = node(page, "c-community-beta")
        before = target.bounding_box()
        assert before is not None
        page.mouse.move(before["x"] + 40, before["y"] + 12)
        page.mouse.down()
        page.mouse.move(before["x"] + 140, before["y"] + 92, steps=8)
        page.mouse.up()
        node(page, "c-community-beta").get_by_role("button", name="Collapse beta").click()
        expect(node(page, "c-community-beta")).to_contain_text("1 agent")
        expect(status(page)).not_to_have_text("Unsaved")
        page.reload(wait_until="networkidle")
        expect(node(page, "c-community-beta")).to_contain_text("1 agent")
        stored = page.evaluate(
            "JSON.parse(localStorage.getItem(Object.keys(localStorage)"
            ".find(key => key.startsWith('caller-graph-view:'))))"
        )
        assert "c-community-beta" in stored and stored["c-community-beta"]["collapsed"]
        assert stored["c-community-beta"]["x"] != 0 or stored["c-community-beta"]["y"] != 0
        expect(status(page)).not_to_have_text("Unsaved")
        page.evaluate("""() => {
            const key = Object.keys(localStorage).find(k => k.startsWith('caller-graph-view:'));
            const view = JSON.parse(localStorage.getItem(key));
            for (let i = 0; i < 450; i++) view['a-filler-' + i] = {x: i, y: 0, t: i};
            localStorage.setItem(key, JSON.stringify(view));
        }""")
        node(page, "c-community-beta").get_by_role("button", name="Expand beta").click()
        count = page.evaluate(
            "Object.keys(JSON.parse(localStorage.getItem(Object.keys(localStorage)"
            ".find(key => key.startsWith('caller-graph-view:'))))).length"
        )
        assert count == 400
        button = outline(page, "agent-beta (Custom, Entry)")
        keyboard_reach(page, button)
        page.keyboard.press("Enter")
        expect(page.get_by_test_id("agent-settings")).to_be_visible()
        expect(page.get_by_role("group", name="agent-beta", exact=True)).to_be_visible()
    finally:
        context.close()


def test_another_publication_shows_a_notice_and_keeps_the_draft(
    browser: Browser, configuration_host, configuration_graph
):
    context, page = open_editor(browser, configuration_host)
    try:
        outline(page, "alpha (Custom)").click()
        page.get_by_role("group", name="Community alpha", exact=True).get_by_label(
            "Name", exact=True
        ).fill("Draft alpha")
        expect(status(page)).to_have_text("Unsaved")
        changed = copy.deepcopy(configuration_graph)
        changed["agents"][3]["prompt"] += " published elsewhere"
        other = publish_graph(configuration_host, changed, "manager-two")
        notice = page.get_by_test_id("revision-notice")
        expect(notice).to_contain_text(f"revision {other['revisionNumber']}", timeout=30000)
        expect(outline(page, "Draft alpha (Custom)")).to_be_visible()
        page.get_by_role("button", name="Publish", exact=True).click()
        page.get_by_role("alertdialog").get_by_role("button", name="Publish now").click()
        expect(page.get_by_test_id("editor-error")).to_contain_text("Publish conflict")
        expect(outline(page, "Draft alpha (Custom)")).to_be_visible()
        notice.get_by_role("button", name="Reload", exact=True).click()
        expect(outline(page, "alpha (Custom)")).to_be_visible()
        expect(page.get_by_test_id("revision-notice")).to_have_count(0)
    finally:
        context.close()


def test_tools_section_assigns_custom_agents_and_names_users_on_removal(
    browser: Browser, configuration_host
):
    context, page = open_editor(browser, configuration_host)
    try:
        tools = page.get_by_test_id("tools-section")
        tools.get_by_label("Reviewed MCP connection", exact=True).select_option(
            label="Reviewed connection"
        )
        tools.get_by_role("button", name="Add MCP connection", exact=True).click()
        connection = tools.get_by_role("group", name="MCP connection Reviewed connection")
        connection.get_by_label("Enabled", exact=True).click()
        expect(connection.get_by_label("Enabled", exact=True)).to_be_checked()
        tools.get_by_label("Reviewed tool", exact=True).select_option(label="Public lookup")
        tools.get_by_role("button", name="Add tool", exact=True).click()
        tool = tools.get_by_role("group", name="Tool Public lookup", exact=True)
        tool.get_by_label("Enabled", exact=True).click()
        expect(tool.get_by_label("Enabled", exact=True)).to_be_checked()
        outline(page, "agent-alpha (Custom, Entry)").click()
        page.get_by_test_id("agent-settings").get_by_label("lookup", exact=True).click()
        expect(
            page.get_by_test_id("agent-settings").get_by_label("lookup", exact=True)
        ).to_be_checked()
        outline(page, "agent-routing (Default, Entry)").click()
        expect(page.get_by_test_id("agent-settings")).to_contain_text(
            "Pre-defined agents cannot receive tools."
        )
        tool.get_by_role("button", name="Remove tool", exact=True).click()
        dialog = page.get_by_role("alertdialog")
        expect(dialog).to_contain_text("These agents lose this tool: agent-alpha")
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        publish(page)
    finally:
        context.close()


def test_large_graph_feedback_stays_fast(browser: Browser, configuration_host, configuration_graph):
    graph = copy.deepcopy(configuration_graph)
    graph["agents"] = [agent for agent in graph["agents"] if agent["role"] != "ordinary"]
    graph["communities"] = []
    for community in range(20):
        community_id = f"community-load-{community}"
        for agent in range(5):
            graph["agents"].append(
                {
                    "agentId": f"agent-load-{community}-{agent}",
                    "name": f"Load agent {community}-{agent}",
                    "prompt": "Synthetic load prompt",
                    "enabled": True,
                    "role": "ordinary",
                    "communityId": community_id,
                    "capabilityAssignments": [],
                }
            )
        graph["communities"].append(
            {
                "communityId": community_id,
                "name": f"Load {community}",
                "routingDescription": "Synthetic load route",
                "enabled": True,
                "entryAgentId": f"agent-load-{community}-0",
                "routable": True,
            }
        )
    publish_graph(configuration_host, graph)
    context, page = open_editor(browser, configuration_host)
    try:
        assert page.locator(".react-flow__node").count() == 3 * 2 + 20 + 100
        collapse = page.evaluate("""async () => {
            const button = document.querySelector('[aria-label="Collapse Load 7"]');
            const started = performance.now();
            button.click();
            while (!document.querySelector('[aria-label="Expand Load 7"]')) {
                await new Promise(requestAnimationFrame);
            }
            return performance.now() - started;
        }""")
        assert collapse < 100, collapse
        zoom = page.evaluate("""async () => {
            const viewport = document.querySelector('.react-flow__viewport');
            const before = viewport.style.transform;
            const started = performance.now();
            document.querySelector('.react-flow__controls-zoomin').click();
            while (viewport.style.transform === before) {
                await new Promise(requestAnimationFrame);
            }
            return performance.now() - started;
        }""")
        assert zoom < 100, zoom
        moved = node(page, "c-community-load-3")
        box = moved.bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + 30, box["y"] + 10)
        page.mouse.down()
        started = time.monotonic()
        page.mouse.move(box["x"] + 90, box["y"] + 50)
        page.wait_for_function(
            "() => document.querySelector('.react-flow__node[data-id=\"c-community-load-3\"]')"
            ".classList.contains('dragging')",
            timeout=1000,
        )
        drag = time.monotonic() - started
        page.mouse.up()
        assert drag < 0.1 + 0.05, drag
        started = time.monotonic()
        outline(page, "Load agent 12-3 (Custom)").click()
        expect(page.get_by_role("group", name="Load agent 12-3", exact=True)).to_be_visible(
            timeout=1000
        )
        assert time.monotonic() - started < 1
    finally:
        context.close()


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


def test_test_host_refuses_live_identity_injection(
    configuration_graph, editor_host_factory
) -> None:
    with pytest.raises(ValueError, match="test_identity_live_mode_prohibited"):
        with editor_host_factory(
            graph=configuration_graph, identities=("manager-one",), mode="live"
        ):
            pytest.fail("A test identity must never enable live management access")


def test_api_cannot_self_approve_unreviewed_catalog_action(
    browser: Browser, configuration_host
) -> None:
    from tests.conftest import capability_graph_document

    context, page = open_editor(browser, configuration_host)
    try:
        base = context.request.get(configuration_host["management"] + "/v2/configuration")
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
            configuration_host["management"] + "/v2/configuration",
            data=forged,
            headers={
                "If-Match": base.headers["etag"],
                "Origin": configuration_host["ui"],
                "X-CSRF-Token": csrf,
            },
        )
        assert response.status == 422
        assert "unreviewed-action" not in response.text()
        current = context.request.get(configuration_host["management"] + "/v2/configuration")
        assert current.json()["id"] == base.json()["id"]
        assert current.json()["tools"] == []
    finally:
        context.close()


@pytest.mark.parametrize(
    "mutation", ["foreign-direct", "raw-context", "protected-edge", "routing-nonentry"]
)
def test_api_rejects_forged_relationship_policy_changes(
    browser: Browser, configuration_host, mutation: str
) -> None:
    from tests.conftest import configuration_graph_document

    context, page = open_editor(browser, configuration_host)
    try:
        current = context.request.get(configuration_host["management"] + "/v2/configuration")
        csrf = context.request.get(configuration_host["management"] + "/auth/session").json()[
            "csrf"
        ]
        graph = configuration_graph_document()
        from management_app.state.editor_draft import EditorDraft

        draft = EditorDraft.from_revision(graph, current.headers["etag"])
        peer = draft.add_agent("community-alpha", "Alpha peer", "Synthetic peer prompt")
        draft.add_relationship("agent-alpha", peer)
        graph = draft.graph
        if mutation == "protected-edge":
            graph["relationships"][0]["targetAgentId"] = "agent-goodbye"
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
            graph["relationships"].append(
                {
                    "relationshipId": "forged-path",
                    "sourceAgentId": "agent-routing",
                    "targetAgentId": peer,
                    "allowedContextFields": [],
                }
            )
        response = context.request.put(
            configuration_host["management"] + "/v2/configuration",
            data=graph,
            headers={
                "If-Match": current.headers["etag"],
                "Origin": configuration_host["ui"],
                "X-CSRF-Token": csrf,
            },
        )
        assert response.status == 422
        assert (
            context.request.get(configuration_host["management"] + "/v2/configuration").json()["id"]
            == current.json()["id"]
        )
    finally:
        context.close()
