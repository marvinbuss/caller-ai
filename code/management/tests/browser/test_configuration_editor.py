"""Real-browser editor contracts; identities are issued only by an owned test host."""

import ssl
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Locator, Page, expect

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def editor_host(editor_host_factory):
    from tests.conftest import configuration_graph_document

    with editor_host_factory(
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
        current = client.get("/v2/configuration")
        current.raise_for_status()
        csrf = client.get("/auth/session").json()["csrf"]
        restored = client.put(
            "/v2/configuration",
            json=configuration_graph,
            headers={
                "If-Match": current.headers["ETag"],
                "X-CSRF-Token": csrf,
                "Origin": editor_host["ui"],
            },
        )
        restored.raise_for_status()
    return editor_host


def show_global_settings(page: Page) -> None:
    settings = page.get_by_test_id("global-settings")
    expect(settings).to_be_visible()
    if not settings.evaluate("element => element.open"):
        settings.locator(":scope > summary").click()


def open_manager(
    browser: Browser,
    host: dict,
    actor: str,
    diagnostics: dict | None = None,
    *,
    global_settings: bool = True,
    fresh_session: bool = False,
):
    context = browser.new_context(ignore_https_errors=True)
    if fresh_session:
        assert context.request.get(host["management"] + "/_test/login/" + actor).ok
    else:
        context.add_cookies(host["sessions"][actor])
    assert context.request.get(host["management"] + "/v2/configuration").status == 200
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(type(error).__name__))
    if diagnostics is not None:

        def watch(socket):
            def sent(payload):
                if isinstance(payload, str):
                    for event in (
                        "save",
                        "move_community",
                        "set_community_coordinate",
                        "add_community_connection",
                        "enter_view",
                    ):
                        if "." + event in payload:
                            diagnostics["sent"][event] = diagnostics["sent"].get(event, 0) + 1

            def received(payload):
                if isinstance(payload, str):
                    for status in ("Saving", "Saved", "Unsaved"):
                        if status in payload:
                            diagnostics["received"][status] = (
                                diagnostics["received"].get(status, 0) + 1
                            )

            socket.on("framesent", sent)
            socket.on("framereceived", received)

        page.on("websocket", watch)
        page.on(
            "pageerror",
            lambda error: diagnostics["page_errors"].append(type(error).__name__),
        )
    page.goto(host["ui"] + "/configuration", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Configuration", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Save configuration", exact=True)).to_be_visible()
    if global_settings:
        show_global_settings(page)
    assert not errors
    return context, page


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_console_entry_is_explicit_accessible_and_never_requests_media_or_ticket(
    browser: Browser, configuration_host, width: int
) -> None:
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    requested = []
    page.on(
        "request",
        lambda request: (
            requested.append(request.url) if request.url.endswith("/v1/test-tickets") else None
        ),
    )
    permission_script = """
        window.callerTestCaptureCount = 0;
        if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => {
            window.callerTestCaptureCount++;
            return Promise.reject(new DOMException("Synthetic denied permission",
                "NotAllowedError"));
        };
        void 0;
    """
    page.add_init_script(permission_script)
    page.evaluate(permission_script)
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.get_by_role("link", name="Test published configuration", exact=True).click()
        expect(page.get_by_role("heading", name="Test Console", exact=True)).to_be_visible()
        start = page.get_by_role("button", name="Start published test", exact=True)
        expect(start).to_be_visible()
        expect(start).to_be_enabled()
        expect(page.get_by_role("button", name="Stop test", exact=True)).to_be_visible()
        expect(page.get_by_label("Mode", exact=True)).to_have_value("text")
        expect(page.get_by_label("Message", exact=True)).to_be_visible()
        keyboard_reach(page, start)
        assert page.evaluate("window.callerTestCaptureCount || 0") == 0 and not requested
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        destination = Path("test-results")
        destination.mkdir(exist_ok=True)
        page.screenshot(path=str(destination / f"test-console-{width}.png"), full_page=True)
        page.reload(wait_until="networkidle")
        expect(page.get_by_role("heading", name="Test Console", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Start published test", exact=True)).to_be_enabled()
        assert page.evaluate("window.callerTestCaptureCount || 0") == 0 and not requested
        send = page.get_by_role("button", name="Send text", exact=True)
        send.scroll_into_view_if_needed()
        assert send.evaluate("""element => {
            const bounds = element.getBoundingClientRect();
            return document.elementFromPoint(bounds.right - 3, bounds.bottom - 3)
                ?.closest('#test-send') === element;
        }""")
    finally:
        context.close()


@pytest.mark.parametrize("width", [360, 1440])
def test_console_denied_permission_is_visible_and_precedes_ticket(
    browser: Browser, configuration_host, width: int
) -> None:
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    requested = []
    page.on(
        "request",
        lambda request: (
            requested.append(request.url) if request.url.endswith("/v1/test-tickets") else None
        ),
    )
    page.evaluate("""
        window.callerTestCaptureCount = 0;
        navigator.mediaDevices.getUserMedia = () => {
            window.callerTestCaptureCount++;
            return Promise.reject(new DOMException("Synthetic denied permission",
                "NotAllowedError"));
        };
        void 0;
    """)
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.get_by_role("link", name="Test published configuration", exact=True).click()
        start = page.get_by_role("button", name="Start published test", exact=True)
        expect(start).to_be_enabled()
        page.get_by_label("Mode", exact=True).select_option("speech")
        expect(page.get_by_label("Microphone", exact=True)).to_be_visible()
        assert page.evaluate("window.callerTestCaptureCount") == 0 and not requested
        start.click()
        expect(page.locator("#test-error")).to_have_text("Permission denied")
        expect(start).to_be_enabled()
        expect(page.locator("#test-capture")).to_have_text("Off")
        assert page.evaluate("window.callerTestCaptureCount") == 1 and not requested
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_console_stop_disposes_late_permission_without_ticket(browser: Browser, configuration_host):
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url) if request.url.endswith("/v1/test-tickets") else None
        ),
    )
    page.evaluate("""() => {
        window.lateMedia = {requests: 0, stopped: 0};
        navigator.mediaDevices.getUserMedia = () => {
            window.lateMedia.requests++;
            return new Promise(resolve => window.resolveLateMedia = resolve);
        };
    }""")
    try:
        page.get_by_role("link", name="Test published configuration", exact=True).click()
        expect(page.get_by_role("button", name="Start published test", exact=True)).to_be_enabled()
        page.get_by_label("Mode", exact=True).select_option("speech")
        page.get_by_role("button", name="Start published test", exact=True).click()
        expect(page.locator("#test-status")).to_have_text("permission")
        page.get_by_role("button", name="Stop test", exact=True).click()
        expect(page.locator("#test-status")).to_have_text("ended")
        page.evaluate("""() => window.resolveLateMedia({getTracks: () => [{
            stop: () => window.lateMedia.stopped++
        }]})""")
        page.wait_for_function("window.lateMedia.stopped === 1")
        assert page.evaluate("window.lateMedia.requests") == 1 and not requests
        expect(page.locator("#test-capture")).to_have_text("Off")
        expect(page.get_by_role("button", name="Start published test", exact=True)).to_be_enabled()
    finally:
        context.close()


@pytest.mark.parametrize("entry", ["overview", "community"])
def test_console_published_text_uses_owned_runtime_without_microphone(
    browser: Browser, configuration_host, entry: str
):
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    wire = []

    def observe(socket):
        if socket.url.endswith("/v1/test-sessions"):

            def receive(raw):
                import json

                if isinstance(raw, str):
                    value = json.loads(raw)
                    wire.append(
                        {
                            key: value[key]
                            for key in ("type", "code", "cleanupStatus")
                            if key in value
                        }
                    )

            socket.on("framereceived", receive)

    page.on("websocket", observe)
    page.evaluate("""() => {
        window.callerTestCaptureCount = 0;
        window.callerTestCompleted = 0;
        window.callerTestSockets = [];
        const NativeSocket = window.WebSocket;
        window.WebSocket = class extends NativeSocket {
            constructor(...arguments_) {
                super(...arguments_);
                if (String(arguments_[0]).endsWith('/v1/test-sessions')) {
                    window.callerTestSockets.push(this);
                    this.addEventListener('message', event => {
                        if (typeof event.data !== 'string') return;
                        const value = JSON.parse(event.data);
                        if (value.type === 'text.completed') window.callerTestCompleted++;
                    });
                }
            }
        };
        navigator.mediaDevices.getUserMedia = () => {
            window.callerTestCaptureCount++;
            return Promise.reject(new DOMException("Synthetic denied permission",
                "NotAllowedError"));
        };
    }""")
    try:
        current = context.request.get(configuration_host["management"] + "/v2/configuration").json()
        page.get_by_role("group", name="Community alpha", exact=True).get_by_label(
            "Name", exact=True
        ).fill("Synthetic unsaved alpha")
        expect(page.get_by_role("status")).to_have_text("Unsaved")
        if entry == "community":
            page.locator("a[href='/configuration/communities/community-alpha']").first.click()
            expect(
                page.get_by_role("heading", name="Synthetic unsaved alpha", exact=True)
            ).to_be_visible()
        page.get_by_role("link", name="Test published configuration", exact=True).click()
        page.get_by_role("button", name="Start published test", exact=True).click()
        try:
            expect(page.locator("#test-status")).to_have_text("ready")
        except AssertionError:
            trust = ssl.create_default_context(cafile=configuration_host["certificate"])
            with httpx.Client(verify=trust, trust_env=False, timeout=1) as diagnostic:
                counts = diagnostic.get(
                    configuration_host["runtime"] + "/_test/runtime-status"
                ).json()
            print({"owned_runtime_counts": counts, "wire": wire})
            raise
        expect(page.locator("#test-revision")).to_contain_text(current["id"])
        expect(page.locator("#test-agent")).to_have_text("agent-welcome")
        page.get_by_label("Message", exact=True).fill("Synthetic published question")
        page.get_by_role("button", name="Send text", exact=True).click()
        expect(page.get_by_role("log", name="Conversation")).to_contain_text(
            "Synthetic shared reply"
        )
        page.wait_for_function("window.callerTestCompleted === 1", timeout=5000)
        expect(page.locator("#test-agent")).to_have_text("agent-alpha")
        assert page.evaluate("window.callerTestCaptureCount") == 0
        page.get_by_role("button", name="Stop test", exact=True).click()
        expect(page.locator("#test-status")).to_have_text("ended")
        expect(page.get_by_role("log", name="Conversation")).to_be_empty()
        expect(page.get_by_role("button", name="Start published test", exact=True)).to_be_enabled()
        page.get_by_role("button", name="Start published test", exact=True).click()
        expect(page.locator("#test-status")).to_have_text("ready")
        expect(page.locator("#test-revision")).to_contain_text(current["id"])
        assert page.evaluate("""() => {
            const old = window.callerTestSockets[0];
            old.onclose(new CloseEvent('close', {code: 1000, reason: 'stopped'}));
            old.onerror(new Event('error'));
            return window.callerTestSockets.length === 2 &&
                window.callerTestSockets[1].readyState === WebSocket.OPEN &&
                document.getElementById('test-status').textContent === 'ready';
        }""")
        page.get_by_label("Message", exact=True).fill("Synthetic replacement question")
        page.get_by_role("button", name="Send text", exact=True).click()
        expect(page.get_by_role("log", name="Conversation")).to_contain_text(
            "Synthetic shared reply"
        )
        page.wait_for_function("window.callerTestCompleted === 2", timeout=5000)
        page.get_by_role("button", name="Stop test", exact=True).click()
        expect(page.locator("#test-status")).to_have_text("ended")
        trust = ssl.create_default_context(cafile=configuration_host["certificate"])
        with httpx.Client(verify=trust, trust_env=False, timeout=1) as diagnostic:
            counts = diagnostic.get(configuration_host["runtime"] + "/_test/runtime-status").json()
        assert counts["occupied"] == 0 and counts["liveConnections"] == 0
        page.get_by_role("link", name="Configuration", exact=True).click()
        expect(page.get_by_role("heading", name="Configuration", exact=True)).to_be_visible()
        expect(page.get_by_role("status")).to_have_text("Unsaved")
        expect(
            page.get_by_role(
                "group", name="Community Synthetic unsaved alpha", exact=True
            ).get_by_label("Name", exact=True)
        ).to_have_value("Synthetic unsaved alpha")
    finally:
        context.close()


@pytest.mark.parametrize("width", [360, 768, 1440])
@pytest.mark.parametrize(
    "failure,label",
    [
        ("rate", "Unsupported audio sample rate"),
        ("playback", "Playback unavailable"),
        ("sink", "Output unavailable"),
    ],
)
def test_console_unavailable_media_is_visible_before_ticket(
    browser: Browser, configuration_host, width: int, failure: str, label: str
):
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    requested = []
    page.on(
        "request",
        lambda request: (
            requested.append(request.url) if request.url.endswith("/v1/test-tickets") else None
        ),
    )
    page.evaluate(
        """failure => {
        window.unavailableMedia = {requests: 0, stopped: 0, closed: 0};
        const NativeContext = window.AudioContext;
        window.AudioContext = class extends NativeContext {
            constructor(options) {
                super(options);
                if (failure === 'sink') this.setSinkId = undefined;
            }
            get sampleRate() { return failure === 'rate' ? 48000 : super.sampleRate; }
            resume() { return failure === 'playback' ? Promise.reject(Error('synthetic')) :
                super.resume(); }
            close() { window.unavailableMedia.closed++; return super.close(); }
        };
        const track = {stop: () => window.unavailableMedia.stopped++};
        navigator.mediaDevices.getUserMedia = async () => {
            window.unavailableMedia.requests++;
            return {getTracks: () => [track]};
        };
    }""",
        failure,
    )
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.get_by_role("link", name="Test published configuration", exact=True).click()
        start = page.get_by_role("button", name="Start published test", exact=True)
        expect(start).to_be_enabled()
        page.get_by_label("Mode", exact=True).select_option("speech")
        if failure == "sink":
            page.get_by_label("Output", exact=True).evaluate("""element => {
                const option = document.createElement('option');
                option.value = 'synthetic-sink'; option.textContent = 'Synthetic output';
                element.append(option); element.value = option.value;
            }""")
        start.click()
        expect(page.locator("#test-error")).to_have_text(label)
        expect(start).to_be_enabled()
        expect(page.locator("#test-capture")).to_have_text("Off")
        assert page.evaluate("window.unavailableMedia.closed") == 1 and not requested
        if failure != "rate":
            assert page.evaluate("window.unavailableMedia.stopped") == 1
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("width", [360, 768, 1440])
@pytest.mark.parametrize(
    "exit_event",
    [
        "controls",
        "devicechange",
        "hidden",
        "navigation",
        "logout",
        "connection_loss",
        "playback_suspended",
        "playback_closed",
    ],
)
def test_console_synthetic_speech_pcm_mute_interrupt_and_confirmed_stop(
    browser: Browser, configuration_host, width: int, exit_event: str
) -> None:
    context, page = open_manager(
        browser,
        configuration_host,
        "manager-one",
        global_settings=False,
        fresh_session=exit_event == "logout",
    )
    page.evaluate("""() => {
        window.syntheticMedia = {requests: 0, stopped: 0, sent: 0, received: 0};
        const NativeNode = window.AudioWorkletNode;
        window.AudioWorkletNode = class extends NativeNode {
            constructor(...arguments_) {
                super(...arguments_); window.syntheticMedia.node = this;
                const post = this.port.postMessage.bind(this.port);
                this.port.postMessage = (value, ...transfer) => {
                    if (value.type === 'grant') window.syntheticMedia.lastGrant = {...value};
                    if (value.type === 'clear') window.syntheticMedia.lastClear = {...value};
                    return post(value, ...transfer);
                };
            }
        };
        const NativeSocket = window.WebSocket;
        window.WebSocket = class extends NativeSocket {
            constructor(...arguments_) {
                super(...arguments_);
                if (String(arguments_[0]).endsWith('/v1/test-sessions')) {
                    window.syntheticMedia.socket = this;
                    this.addEventListener('message', event => {
                        if (event.data instanceof ArrayBuffer) window.syntheticMedia.received++;
                    });
                }
            }
            send(value) {
                if (value instanceof ArrayBuffer) window.syntheticMedia.sent++;
                return super.send(value);
            }
        };
        navigator.mediaDevices.getUserMedia = async () => {
            window.syntheticMedia.requests++;
            const source = new AudioContext({sampleRate: 24000});
            const oscillator = source.createOscillator();
            const destination = source.createMediaStreamDestination();
            oscillator.frequency.value = 440; oscillator.connect(destination); oscillator.start();
            await source.resume();
            const track = destination.stream.getAudioTracks()[0], stop = track.stop.bind(track);
            track.stop = () => {
                window.syntheticMedia.stopped++; stop(); oscillator.stop(); void source.close();
            };
            return destination.stream;
        };
    }""")
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.get_by_role("link", name="Test published configuration", exact=True).click()
        expect(page.get_by_role("button", name="Start published test", exact=True)).to_be_enabled()
        page.get_by_label("Mode", exact=True).select_option("speech")
        assert page.evaluate("window.syntheticMedia.requests") == 0
        page.get_by_role("button", name="Start published test", exact=True).click()
        expect(page.locator("#test-status")).to_have_text("ready")
        expect(page.locator("#test-capture")).to_have_text("Active")
        page.wait_for_function(
            "window.syntheticMedia.sent > 0 && window.syntheticMedia.received > 0"
        )
        if exit_event != "controls":
            if exit_event == "devicechange":
                page.evaluate("navigator.mediaDevices.dispatchEvent(new Event('devicechange'))")
            elif exit_event == "hidden":
                page.evaluate("""() => {
                    Object.defineProperty(document, 'visibilityState',
                        {value: 'hidden', configurable: true});
                    document.dispatchEvent(new Event('visibilitychange'));
                }""")
            elif exit_event == "navigation":
                page.get_by_role("link", name="Configuration", exact=True).click()
                expect(
                    page.get_by_role("heading", name="Configuration", exact=True)
                ).to_be_visible()
            elif exit_event == "logout":
                csrf = context.request.get(
                    configuration_host["management"] + "/auth/session"
                ).json()["csrf"]
                response = context.request.post(
                    configuration_host["management"] + "/auth/logout",
                    headers={"Origin": configuration_host["ui"], "X-CSRF-Token": csrf},
                )
                assert response.status == 204
            elif exit_event in {"playback_suspended", "playback_closed"}:
                page.evaluate(
                    """action => window.syntheticMedia.node.context[action]()""",
                    "suspend" if exit_event == "playback_suspended" else "close",
                )
            else:
                page.evaluate("window.syntheticMedia.socket.close(1000, 'synthetic-loss')")
            page.wait_for_function("window.syntheticMedia.stopped === 1")
            assert page.evaluate("window.syntheticMedia.requests") == 1
            if exit_event not in {"navigation", "logout"}:
                expect(page.locator("#test-capture")).to_have_text("Off")
                expect(page.locator("#test-status")).to_have_text(
                    "cleanup pending" if exit_event == "connection_loss" else "ended"
                )
                if exit_event.startswith("playback_"):
                    expect(page.locator("#test-error")).to_have_text("Playback unavailable")
            return
        page.evaluate("""() => {
            window.syntheticMedia.oldGrant = {...window.syntheticMedia.lastGrant};
            window.syntheticMedia.oldClear = {...window.syntheticMedia.lastClear};
        }""")
        page.get_by_role("button", name="Mute input", exact=True).click()
        expect(page.locator("#test-capture")).to_have_text("Muted")
        expect(page.get_by_role("button", name="Mute input", exact=True)).to_have_attribute(
            "aria-pressed", "true"
        )
        page.get_by_role("button", name="Mute input", exact=True).click()
        expect(page.locator("#test-capture")).to_have_text("Active")
        page.wait_for_function("""window.syntheticMedia.lastGrant.captureEpoch >
            window.syntheticMedia.oldGrant.captureEpoch""")
        assert page.evaluate("""() => {
            const media = window.syntheticMedia, before = media.sent;
            media.node.port.onmessage({data: {...media.oldGrant, type: 'capture',
                pcm: new ArrayBuffer(960)}});
            media.node.port.postMessage(media.oldGrant);
            return media.sent === before &&
                document.getElementById('test-status').textContent === 'ready';
        }""")
        page.get_by_role("button", name="Interrupt output", exact=True).click()
        expect(page.locator("#test-playback")).to_have_text("Interrupted")
        before = page.evaluate("window.syntheticMedia.sent")
        page.evaluate("""() => {
            const media = window.syntheticMedia;
            media.node.port.onmessage({data: {...media.oldClear, type: 'cleared'}});
        }""")
        page.wait_for_function("before => window.syntheticMedia.sent > before", arg=before)
        expect(page.locator("#test-status")).to_have_text("ready")
        page.get_by_role("button", name="Stop test", exact=True).click()
        expect(page.locator("#test-capture")).to_have_text("Off")
        expect(page.locator("#test-status")).to_have_text("ended")
        assert page.evaluate(
            "window.syntheticMedia.requests === 1 && window.syntheticMedia.stopped === 1"
        )
        expect(page.get_by_role("button", name="Start published test", exact=True)).to_be_enabled()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_us1_community_graph_publication_navigation_and_presentation(
    browser: Browser, configuration_host, width: int
) -> None:
    diagnostics = {"sent": {}, "received": {}, "page_errors": []}
    context, page = open_manager(
        browser, configuration_host, "manager-one", diagnostics, global_settings=False
    )
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        graph = page.get_by_role("region", name="Community graph", exact=True)
        expect(graph).to_be_visible()
        expect(graph.locator(".react-flow__node")).to_have_count(2)
        before = context.request.get(configuration_host["management"] + "/v2/configuration").json()
        form = page.get_by_role("form", name="New community connection", exact=True)
        keyboard_reach(page, form.get_by_label("Source community", exact=True))
        form.get_by_label("Source community", exact=True).select_option("community-alpha")
        keyboard_reach(page, form.get_by_label("Destination community", exact=True))
        form.get_by_label("Destination community", exact=True).select_option("community-beta")
        keyboard_reach(page, form.get_by_role("button", name="Add connection", exact=True))
        page.keyboard.press("Enter")
        alpha = page.get_by_role("group", name="Community alpha", exact=True)
        alpha.get_by_label("X position", exact=True).fill("320")
        alpha.get_by_label("X position", exact=True).press("Tab")
        page.get_by_role("button", name="Save configuration", exact=True).click()
        try:
            expect(page.get_by_role("status")).to_have_text("Saved")
        except AssertionError:
            current = context.request.get(configuration_host["management"] + "/v2/configuration")
            print(
                {
                    "us1_save_diagnostics": diagnostics,
                    "snapshot_status": current.status,
                    "revision_advanced": current.status == 200
                    and current.json()["revisionNumber"] > before["revisionNumber"],
                }
            )
            raise
        saved = context.request.get(configuration_host["management"] + "/v2/configuration").json()
        assert len(saved["communityConnections"]) == 1
        edge = saved["communityConnections"][0]
        assert (edge["sourceCommunityId"], edge["targetCommunityId"]) == (
            "community-alpha",
            "community-beta",
        )
        assert (
            next(
                position
                for position in saved["layout"]["communities"]
                if position["nodeId"] == "community-alpha"
            )["x"]
            == 320
        )
        for field in ("agents", "communities", "relationships", "tools", "mcpConnections"):
            assert saved[field] == before[field]
        graph.locator(".react-flow__controls-fitview").click()
        native_node = graph.locator('.react-flow__node[data-id="community-alpha"]')
        expect(native_node).to_be_visible()
        native_node.hover(timeout=5000)
        node_box = native_node.bounding_box()
        assert node_box is not None
        start_x = node_box["x"] + node_box["width"] / 2
        start_y = node_box["y"] + node_box["height"] / 2
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x + 28, start_y + 24, steps=8)
        page.mouse.up()
        expect(page.get_by_role("status")).to_have_text("Unsaved")
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Saved")
        dragged = context.request.get(configuration_host["management"] + "/v2/configuration").json()
        assert dragged["layout"] != saved["layout"]
        for field in (
            "agents",
            "communities",
            "relationships",
            "communityConnections",
            "tools",
            "mcpConnections",
        ):
            assert dragged[field] == saved[field]
        graph.locator(".react-flow__controls-fitview").click()
        page.wait_for_function(
            """element => {
                const frame = element.getBoundingClientRect();
                const nodes = [...element.querySelectorAll('.react-flow__node')];
                return nodes.length === 2 && nodes.every(node => {
                    const box = node.getBoundingClientRect();
                    return box.width > 40 && box.height > 20 &&
                        box.x >= frame.x - 1 && box.y >= frame.y - 1 &&
                        box.right <= frame.right + 1 && box.bottom <= frame.bottom + 1;
                });
            }""",
            arg=graph.element_handle(),
            timeout=5000,
        )
        frame = graph.bounding_box()
        assert frame is not None
        for node in graph.locator(".react-flow__node").all():
            bounds = node.bounding_box()
            assert bounds is not None and bounds["width"] > 40 and bounds["height"] > 20
            assert bounds["x"] >= frame["x"] - 1
            assert bounds["y"] >= frame["y"] - 1
            assert bounds["x"] + bounds["width"] <= frame["x"] + frame["width"] + 1
            assert bounds["y"] + bounds["height"] <= frame["y"] + frame["height"] + 1
        output = Path(__file__).resolve().parents[2] / "test-results"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / f"us1-overview-{width}.png"), full_page=True)
        page.get_by_role("link", name="alpha", exact=True).click()
        expect(page).to_have_url(
            configuration_host["ui"] + "/configuration/communities/community-alpha"
        )
        expect(page.get_by_role("heading", name="alpha", exact=True)).to_be_visible()
        prompt = page.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
            "Prompt", exact=True
        )
        prompt.fill("Synthetic US1 retained route draft")
        prompt.press("Tab")
        page.get_by_role("link", name="Communities", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Unsaved")
        page.go_back(wait_until="networkidle")
        expect(
            page.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
                "Prompt", exact=True
            )
        ).to_have_value("Synthetic US1 retained route draft")
        page.go_forward(wait_until="networkidle")
        expect(page.get_by_role("status")).to_have_text("Unsaved")
        page.get_by_role("link", name="alpha", exact=True).click()
        expect(
            page.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
                "Prompt", exact=True
            )
        ).to_have_value("Synthetic US1 retained route draft")
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Saved")
        page.reload(wait_until="networkidle")
        expect(
            page.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
                "Prompt", exact=True
            )
        ).to_have_value("Synthetic US1 retained route draft")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        output = Path(__file__).resolve().parents[2] / "test-results"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / f"us1-community-{width}.png"), full_page=True)
    finally:
        context.close()


def test_us1_direct_missing_and_anonymous_child_links_are_safe(
    browser: Browser, configuration_host
) -> None:
    with browser.new_context(ignore_https_errors=True) as context:
        context.add_cookies(configuration_host["sessions"]["manager-one"])
        page = context.new_page()
        page.goto(
            configuration_host["ui"] + "/configuration/communities/missing",
            wait_until="networkidle",
        )
        expect(page.get_by_text("Community unavailable", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="alpha", exact=True)).to_have_count(0)
        page.goto(
            configuration_host["ui"] + "/configuration/communities/community-alpha",
            wait_until="networkidle",
        )
        expect(page.get_by_role("heading", name="alpha", exact=True)).to_be_visible()
    with browser.new_context(ignore_https_errors=True) as context:
        page = context.new_page()
        response = page.goto(
            configuration_host["ui"] + "/configuration/communities/community-alpha",
            wait_until="networkidle",
        )
        assert response is not None and "Synthetic approved prompt" not in response.text()
        expect(page.get_by_role("button", name="Save configuration", exact=True)).to_have_count(0)
        assert (
            context.request.get(configuration_host["management"] + "/v2/configuration").status
            == 401
        )


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_us2_selected_agent_graph_entry_directed_handover_and_publication(
    browser: Browser, configuration_host, width: int
):
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.get_by_role("link", name="alpha", exact=True).click()
        graph = page.get_by_role("region", name="Agent graph", exact=True)
        expect(graph).to_be_visible()
        expect(graph.locator(".react-flow__node")).to_have_count(1)
        expect(page.get_by_role("group", name="agent-beta", exact=True)).to_have_count(0)
        for name in ("Alpha peer one", "Alpha peer two"):
            form = page.get_by_role("form", name="New agent", exact=True)
            form.get_by_label("New agent name", exact=True).fill(name)
            form.get_by_label("New agent prompt", exact=True).fill("Synthetic US2 agent prompt")
            form.get_by_role("button", name="Add agent", exact=True).click()
            expect(page.get_by_role("group", name=name, exact=True)).to_be_visible()
        expect(graph.locator(".react-flow__node")).to_have_count(3)
        entry = page.get_by_label("Community entry agent", exact=True)
        entry.select_option(label="Alpha peer one")
        page.get_by_label("Source agent", exact=True).select_option(label="agent-alpha")
        page.get_by_label("Destination agent", exact=True).select_option(label="Alpha peer two")
        page.get_by_role("button", name="Add relationship", exact=True).click()
        relationship = page.get_by_role(
            "group", name="Relationship agent-alpha to Alpha peer two", exact=True
        )
        expect(relationship).to_be_visible()
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Saved")
        before = context.request.get(configuration_host["management"] + "/v2/configuration").json()
        selected = next(
            community
            for community in before["communities"]
            if community["communityId"] == "community-alpha"
        )
        assert (
            next(
                agent for agent in before["agents"] if agent["agentId"] == selected["entryAgentId"]
            )["name"]
            == "Alpha peer one"
        )
        assert len(before["relationships"]) == 1
        graph.locator(".react-flow__controls-fitview").click()
        unrelated = graph.locator(
            '.react-flow__node[data-id="' + selected["entryAgentId"] + '"]'
        ).bounding_box()
        assert unrelated is not None
        assert graph.locator(".react-flow__edge-path").evaluate(
            """(path, bounds) => {
            const matrix = path.getScreenCTM();
            const length = path.getTotalLength();
            for (let index = 1; index < 100; index++) {
                const point = path.getPointAtLength(length * index / 100).matrixTransform(matrix);
                if (point.x > bounds.x + 3 && point.x < bounds.x + bounds.width - 3 &&
                    point.y > bounds.y + 3 && point.y < bounds.y + bounds.height - 3) return false;
            }
            return true;
        }""",
            unrelated,
        ), "Directed edge falsely passes through an unrelated agent"
        native_node = graph.locator('.react-flow__node[data-id="agent-alpha"]')
        bounds = native_node.bounding_box()
        assert bounds is not None
        start_x, start_y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x + 24, start_y + 20, steps=8)
        page.mouse.up()
        expect(page.get_by_role("status")).to_have_text("Unsaved")
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Saved")
        moved = context.request.get(configuration_host["management"] + "/v2/configuration").json()
        assert moved["layout"] != before["layout"]
        for field in (
            "agents",
            "communities",
            "relationships",
            "communityConnections",
            "tools",
            "mcpConnections",
        ):
            assert moved[field] == before[field]
        page.reload(wait_until="networkidle")
        expect(graph.locator(".react-flow__node")).to_have_count(3)
        expect(relationship).to_be_visible()
        chosen = page.get_by_role("group", name="Alpha peer one", exact=True)
        chosen.get_by_label("Enabled", exact=True).click()
        expect(chosen.get_by_label("Enabled", exact=True)).not_to_be_checked()
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("Invalid configuration")
        assert (
            context.request.get(configuration_host["management"] + "/v2/configuration").json()["id"]
            == moved["id"]
        )
        chosen.get_by_label("Enabled", exact=True).click()
        expect(chosen.get_by_label("Enabled", exact=True)).to_be_checked()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        output = Path(__file__).resolve().parents[2] / "test-results"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / f"us2-agent-{width}.png"), full_page=True)
    finally:
        context.close()


def test_us1_connection_removal_requires_confirmation_and_preserves_publication_until_save(
    browser: Browser, configuration_host
):
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    try:
        form = page.get_by_role("form", name="New community connection", exact=True)
        form.get_by_label("Source community", exact=True).select_option("community-alpha")
        form.get_by_label("Destination community", exact=True).select_option("community-beta")
        form.get_by_role("button", name="Add connection", exact=True).click()
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Saved")
        before = context.request.get(configuration_host["management"] + "/v2/configuration").json()
        remove = page.get_by_role("button", name="Delete connection alpha to beta", exact=True)
        remove.click()
        dialog = page.get_by_role("alertdialog")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        expect(remove).to_be_visible()
        remove.click()
        dialog.get_by_role("button", name="Delete connection", exact=True).click()
        expect(remove).to_have_count(0)
        assert (
            context.request.get(configuration_host["management"] + "/v2/configuration").json()
            == before
        )
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Saved")
        saved = context.request.get(configuration_host["management"] + "/v2/configuration").json()
        assert saved["communityConnections"] == []
        assert saved["agents"] == before["agents"] and saved["communities"] == before["communities"]
    finally:
        context.close()


def test_us1_protected_only_overview_is_explicitly_empty_without_model_resources(
    browser: Browser, configuration_host
):
    with browser.new_context(ignore_https_errors=True) as setup:
        setup.add_cookies(configuration_host["sessions"]["manager-one"])
        current = setup.request.get(configuration_host["management"] + "/v2/configuration")
        graph = {
            key: value
            for key, value in current.json().items()
            if key
            in {
                "schemaVersion",
                "agents",
                "communities",
                "relationships",
                "communityConnections",
                "layout",
                "tools",
                "mcpConnections",
            }
        }
        graph["agents"] = [agent for agent in graph["agents"] if agent["role"] != "ordinary"]
        graph.update(
            communities=[],
            relationships=[],
            communityConnections=[],
            tools=[],
            mcpConnections=[],
            layout={"communities": [], "agentsByCommunity": {}},
        )
        csrf = setup.request.get(configuration_host["management"] + "/auth/session").json()["csrf"]
        published = setup.request.put(
            configuration_host["management"] + "/v2/configuration",
            data=graph,
            headers={
                "If-Match": current.headers["etag"],
                "Origin": configuration_host["ui"],
                "X-CSRF-Token": csrf,
            },
        )
        assert published.status == 200
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    try:
        expect(page.get_by_text("No communities", exact=True)).to_be_visible()
        expect(
            page.get_by_role("region", name="Community graph").locator(".react-flow__node")
        ).to_have_count(0)
        expect(page.get_by_role("button", name="Add connection", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Add community", exact=True)).to_be_enabled()
        assert (
            context.request.get(configuration_host["management"] + "/v1/test-tickets").status == 404
        )
        denied = context.request.post(
            configuration_host["management"] + "/v1/unreviewed-test-tickets"
        )
        assert denied.status == 403 and denied.json()["error"] == "unreviewed_ingress_prohibited"
        guarded = context.request.post(configuration_host["management"] + "/v1/test-tickets")
        assert guarded.status == 403 and guarded.json()["code"] == "test_permission_denied"
    finally:
        context.close()


def test_us1_maximum_community_name_fits_mobile_forms_and_graph(
    browser: Browser, configuration_host
):
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    try:
        page.set_viewport_size({"width": 360, "height": 1000})
        name = "L" * 128
        community = page.get_by_role("group", name="Community alpha", exact=True)
        community.get_by_label("Name", exact=True).fill(name)
        community.get_by_label("Name", exact=True).press("Tab")
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Saved")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        graph = page.get_by_role("region", name="Community graph", exact=True)
        graph.locator(".react-flow__controls-fitview").click()
        first = graph.locator('.react-flow__node[data-id="community-alpha"]').bounding_box()
        second = graph.locator('.react-flow__node[data-id="community-beta"]').bounding_box()
        assert first is not None and second is not None
        assert (
            first["x"] + first["width"] <= second["x"]
            or second["x"] + second["width"] <= first["x"]
            or first["y"] + first["height"] <= second["y"]
            or second["y"] + second["height"] <= first["y"]
        ), "Maximum-name nodes overlap in deterministic default layout"
        page.get_by_role("link", name=name, exact=True).click()
        expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        context.close()


def test_us1_dirty_child_reload_and_explicit_discard_require_confirmation(
    browser: Browser, configuration_host
) -> None:
    context, page = open_manager(browser, configuration_host, "manager-one", global_settings=False)
    try:
        page.get_by_role("link", name="alpha", exact=True).click()
        prompt = page.get_by_role("group", name="agent-alpha", exact=True).get_by_label(
            "Prompt", exact=True
        )
        prompt.fill("Synthetic unsaved guarded child edit")
        prompt.press("Tab")
        expect(page.get_by_role("status")).to_have_text("Unsaved")
        dialogs = []

        def cancel_reload(dialog):
            dialogs.append(dialog.type)
            dialog.dismiss()

        page.once("dialog", cancel_reload)
        page.evaluate("window.location.reload()")
        expect(prompt).to_have_value("Synthetic unsaved guarded child edit")
        assert dialogs == ["beforeunload"]
        page.get_by_role("button", name="Reload configuration", exact=True).click()
        page.get_by_role("button", name="Cancel", exact=True).click()
        expect(prompt).to_have_value("Synthetic unsaved guarded child edit")
        page.get_by_role("button", name="Reload configuration", exact=True).click()
        page.get_by_role("button", name="Reload", exact=True).click()
        expect(prompt).to_have_value("Synthetic approved prompt")
        assert page.url.endswith("/configuration/communities/community-alpha")
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
        current = context.request.get(configuration_host["management"] + "/v2/configuration").json()
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
        show_global_settings(first)
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
        response = context.request.get(configuration_host["management"] + "/v2/configuration")
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
        page.get_by_role("alertdialog").get_by_role(
            "button", name="Delete agent", exact=True
        ).click()
        expect(page.get_by_role("group", name="Gamma entry", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Save configuration", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Saved")).to_be_visible()
        page.reload(wait_until="networkidle")
        show_global_settings(page)
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
        show_global_settings(page)
        expect(page.get_by_role("heading", name="gamma", exact=True)).to_have_count(0)
        expect(page.get_by_role("group", name="agent-welcome", exact=True)).to_be_visible()
    finally:
        context.close()


def test_test_host_refuses_live_identity_injection(
    configuration_graph, editor_host_factory
) -> None:
    with pytest.raises(ValueError, match="test_identity_live_mode_prohibited"):
        with editor_host_factory(
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
        show_global_settings(page)
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
        show_global_settings(page)
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
        protected = page.get_by_role(
            "group", name="Relationship agent-alpha to agent-routing", exact=True
        )
        expect(protected).to_have_count(0)
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
        show_global_settings(page)
        expect(relationship.get_by_label("Transfer reason", exact=True)).not_to_be_checked()
        expect(protected).to_have_count(0)
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
        show_global_settings(page)
        expect(relationship).to_have_count(0)
        expect(protected).to_have_count(0)
    finally:
        context.close()


@pytest.mark.parametrize(
    "mutation", ["foreign-direct", "raw-context", "protected-edge", "routing-nonentry"]
)
def test_api_rejects_forged_relationship_policy_changes(
    browser: Browser, configuration_host, mutation: str
) -> None:
    from tests.conftest import configuration_graph_document

    context, page = open_manager(browser, configuration_host, "manager-one")
    try:
        current = context.request.get(configuration_host["management"] + "/v2/configuration")
        csrf = context.request.get(configuration_host["management"] + "/auth/session").json()[
            "csrf"
        ]
        graph = configuration_graph_document()
        from management_app.state.configuration_editor import EditorDraft

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


def test_conflict_retains_relationship_context_and_agent_edits_across_views(
    browser: Browser, configuration_host
) -> None:
    from management_app.state.configuration_editor import EditorDraft

    with browser.new_context(ignore_https_errors=True) as setup:
        setup.add_cookies(configuration_host["sessions"]["manager-one"])
        current = setup.request.get(configuration_host["management"] + "/v2/configuration")
        csrf = setup.request.get(configuration_host["management"] + "/auth/session").json()["csrf"]
        draft = EditorDraft.from_revision(current.json(), current.headers["etag"])
        peer = draft.add_agent("community-beta", "Beta peer", "Synthetic peer prompt")
        draft.add_relationship("agent-beta", peer)
        seeded = setup.request.put(
            configuration_host["management"] + "/v2/configuration",
            data=draft.graph,
            headers={
                "If-Match": current.headers["etag"],
                "Origin": configuration_host["ui"],
                "X-CSRF-Token": csrf,
            },
        )
        assert seeded.status == 200
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
            "group", name="Relationship agent-beta to Beta peer", exact=True
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
            configuration_host["management"] + "/v2/configuration"
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
        before = context.request.get(configuration_host["management"] + "/v2/configuration").json()
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
        after = context.request.get(configuration_host["management"] + "/v2/configuration").json()
        assert after["id"] == before["id"]
        assert candidate not in str(after)
    finally:
        context.close()
