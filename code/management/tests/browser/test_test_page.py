"""The Test page at `/test` inside the frame: every console control, run against the owned host."""

import json
import ssl
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Page, expect

from tests.browser.test_configuration_editor import keyboard_reach
from tests.conftest import EXPECT_TIMEOUT_MS

pytestmark = pytest.mark.browser

SPEECH_EXITS = (
    "controls",
    "devicechange",
    "hidden",
    "navigation",
    "logout",
    "connection_loss",
    "playback_suspended",
    "playback_closed",
)
SPEECH_WIDTHS = (360, 768, 1440)
TEXT_ENTRIES = ("overview", "community")
# Each console case runs as its own manager, so a session left in `cleanup pending` holds only
# that case's slot instead of failing every case after it.
CONSOLE_ACTORS = (
    *(f"console-speech-{event}-{width}" for event in SPEECH_EXITS for width in SPEECH_WIDTHS),
    *(f"console-text-{entry}" for entry in TEXT_ENTRIES),
    "console-roles",
)


@pytest.fixture(scope="module")
def editor_host(editor_host_factory):
    from tests.conftest import configuration_graph_document

    with editor_host_factory(
        graph=configuration_graph_document(),
        identities=("manager-one", "manager-two", *CONSOLE_ACTORS),
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


def open_main(page: Page, label: str) -> None:
    """Select a sidebar entry; below 768 px the sidebar sits behind the menu button."""
    menu = page.get_by_role("button", name="Menu", exact=True)
    if menu.is_visible() and menu.get_attribute("aria-expanded") != "true":
        menu.click()
    page.get_by_role("navigation", name="Main").get_by_role("link", name=label, exact=True).click()


def open_test_page(page: Page) -> None:
    open_main(page, "Test")
    expect(page.get_by_role("heading", name="Test", exact=True)).to_be_visible()


def open_editor_page(page: Page) -> None:
    open_main(page, "Community and Agent")
    expect(page.get_by_role("heading", name="Community and Agent", exact=True)).to_be_visible()


def press_mute_when_no_audio_is_queued(page: Page) -> None:
    # The console ends a session that is muted while audio is still queued on its socket (it
    # cannot recall queued frames), so press the button in the same task that sees an empty queue.
    page.wait_for_function(
        """() => {
            const socket = window.syntheticMedia.socket;
            if (!socket || socket.bufferedAmount !== 0) return false;
            document.getElementById('test-mute').click();
            return true;
        }""",
        timeout=EXPECT_TIMEOUT_MS,
    )


def runtime_events(host: dict, limit: int = 16) -> list[str]:
    # The runtime's own account of a session's end, which the browser assertion cannot show.
    keep = {
        "test.admission",
        "test.relay",
        "test.work",
        "test.session",
        "test.cleanup",
        "test.alert",
        "test.latency",
    }
    rows = []
    for line in Path(host["log"]).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and (
            value.get("event") in keep or str(value.get("event", "")).endswith(".failure")
        ):
            rows.append(
                " ".join(
                    str(value.get(key, ""))
                    for key in (
                        "timestamp",
                        "service",
                        "event",
                        "outcome",
                        "reason_code",
                        "value",
                        "error_class",
                        "error_code",
                    )
                ).strip()
            )
    return rows[-limit:]


def print_failure_context(page: Page, host: dict) -> None:
    # A session that did not end as expected shows its reason here, which the assertion does not.
    try:
        print(
            {
                key: page.locator("#test-" + key).inner_text(timeout=1000)
                for key in ("status", "error", "capture", "playback")
            }
        )
    except Exception:
        pass
    try:
        print({"runtime_events": runtime_events(host)})
    except Exception:
        pass


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
    page.goto(host["ui"] + "/communities", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Community and Agent", exact=True)).to_be_visible()
    expect(page.get_by_test_id("graph")).to_be_visible()
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
            requested.append(request.url) if request.url.endswith("/v1/console/tickets") else None
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
        open_test_page(page)
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
        expect(page.get_by_role("heading", name="Test", exact=True)).to_be_visible()
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
            requested.append(request.url) if request.url.endswith("/v1/console/tickets") else None
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
        open_test_page(page)
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
            requests.append(request.url) if request.url.endswith("/v1/console/tickets") else None
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
        open_test_page(page)
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


@pytest.mark.parametrize("entry", TEXT_ENTRIES)
def test_console_published_text_uses_owned_runtime_without_microphone(
    browser: Browser, configuration_host, entry: str
):
    context, page = open_manager(
        browser, configuration_host, f"console-text-{entry}", global_settings=False
    )
    wire = []

    def observe(socket):
        if socket.url.endswith("/v1/console/sessions"):

            def receive(raw):
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
                if (String(arguments_[0]).endsWith('/v1/console/sessions')) {
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
        page.get_by_role("button", name="alpha (Custom)", exact=True).click()
        page.get_by_role("group", name="Community alpha", exact=True).get_by_label(
            "Name", exact=True
        ).fill("Synthetic unsaved alpha")
        expect(page.get_by_test_id("editor-status")).to_have_text("Unsaved")
        if entry == "community":
            page.get_by_role("button", name="agent-alpha (Custom, Entry)", exact=True).click()
            expect(page.get_by_test_id("agent-settings")).to_be_visible()
        open_test_page(page)
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
        # Each text session opens with the greeting turn, then the reply.
        page.wait_for_function("window.callerTestCompleted === 2", timeout=EXPECT_TIMEOUT_MS)
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
        page.wait_for_function("window.callerTestCompleted === 4", timeout=EXPECT_TIMEOUT_MS)
        page.get_by_role("button", name="Stop test", exact=True).click()
        expect(page.locator("#test-status")).to_have_text("ended")
        trust = ssl.create_default_context(cafile=configuration_host["certificate"])
        with httpx.Client(verify=trust, trust_env=False, timeout=1) as diagnostic:
            counts = diagnostic.get(configuration_host["runtime"] + "/_test/runtime-status").json()
        assert counts["occupied"] == 0 and counts["liveConnections"] == 0
        open_editor_page(page)
        expect(page.get_by_test_id("editor-status")).to_have_text("Unsaved")
        page.get_by_role("button", name="Synthetic unsaved alpha (Custom)", exact=True).click()
        expect(
            page.get_by_role(
                "group", name="Community Synthetic unsaved alpha", exact=True
            ).get_by_label("Name", exact=True)
        ).to_have_value("Synthetic unsaved alpha")
    except AssertionError:
        print_failure_context(page, configuration_host)
        raise
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
            requested.append(request.url) if request.url.endswith("/v1/console/tickets") else None
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
        open_test_page(page)
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


@pytest.mark.parametrize("width", SPEECH_WIDTHS)
@pytest.mark.parametrize("exit_event", SPEECH_EXITS)
def test_console_synthetic_speech_pcm_mute_interrupt_and_confirmed_stop(
    browser: Browser, configuration_host, width: int, exit_event: str
) -> None:
    context, page = open_manager(
        browser,
        configuration_host,
        f"console-speech-{exit_event}-{width}",
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
                if (String(arguments_[0]).endsWith('/v1/console/sessions')) {
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
        open_test_page(page)
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
                open_editor_page(page)
                # Leaving the page stops the test like an explicit stop and says so.
                expect(page.get_by_test_id("test-left-notice")).to_be_visible()
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
        press_mute_when_no_audio_is_queued(page)
        expect(page.locator("#test-capture")).to_have_text("Muted")
        expect(page.get_by_role("button", name="Mute input", exact=True)).to_have_attribute(
            "aria-pressed", "true"
        )
        press_mute_when_no_audio_is_queued(page)
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
    except AssertionError:
        print_failure_context(page, configuration_host)
        raise
    finally:
        context.close()


@pytest.mark.parametrize(
    "roles,switch,allowed",
    [("editor", False, False), ("tester", False, False), ("tester", True, True)],
)
def test_test_page_follows_roles_and_the_tester_only_switch(
    browser: Browser, configuration_host, roles: str, switch: bool, allowed: bool
):
    host = configuration_host
    context = browser.new_context(ignore_https_errors=True)
    try:
        assert context.request.get(host["management"] + f"/_test/tester-only/{switch}").ok
        assert context.request.get(
            host["management"] + "/_test/login/console-roles",
            params={"roles": roles, "target": "/test"},
        ).ok
        page = context.new_page()
        page.goto(host["ui"] + "/test", wait_until="networkidle")
        start = page.get_by_role("button", name="Start published test", exact=True)
        if not allowed:
            # The missing-role explanation, and no start control.
            expect(page.get_by_role("heading", name="Role required", exact=True)).to_be_visible()
            assert start.count() == 0
            return
        expect(start).to_be_enabled()
        csrf = context.request.get(host["management"] + "/auth/session").json()["csrf"]
        ticket = context.request.post(
            host["management"] + "/v1/console/tickets",
            data=json.dumps({"mode": "text", "requestId": "tester-only-ticket"}),
            headers={
                "Origin": host["ui"],
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
            },
        )
        assert ticket.status == 200
        assert context.request.get(host["management"] + "/v2/configuration").status == 403
    finally:
        context.request.get(host["management"] + "/_test/tester-only/false")
        context.close()
