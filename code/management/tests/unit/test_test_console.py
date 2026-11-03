"""Bounded console/relay contracts; no device, provider or credential discovery."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.contract.test_browser_tests import permit_document


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://runtime.fixture.example",
        "https://runtime.fixture.example/override",
        "https://runtime.fixture.example?token=secret",
        "https://foreign.fixture.example",
    ],
)
def test_relay_binding_rejects_noncanonical_or_unapproved_origin_without_credentials(endpoint):
    from management_app.adapters.test_session_client import TestSessionClient

    credentials = SimpleNamespace(get_token=AsyncMock())
    with pytest.raises(ValueError):
        TestSessionClient(
            endpoint=endpoint,
            audience="11111111-2222-4333-8444-555555555555",
            approved_hosts=frozenset({"runtime.fixture.example"}),
            credential=credentials,
            utc_now=lambda: None,
            monotonic=lambda: 0.0,
            max_clock_skew_seconds=0,
        )
    credentials.get_token.assert_not_called()


def test_relay_default_connector_cannot_follow_runtime_redirect():
    from management_app.adapters.test_session_client import NoRedirectConnect

    failure = RuntimeError("synthetic-runtime-redirect")
    assert NoRedirectConnect.process_redirect(object(), failure) is failure


def test_relay_connect_and_first_ready_share_original_signed_deadline():
    from management_app.adapters.test_session_client import TestSessionClient
    from management_app.models.test_session import StartPermit

    permit = StartPermit.model_validate_json(json.dumps(permit_document()))
    clock, ticks, connections = [permit.issued_at], [2.0], []
    credentials = SimpleNamespace(
        get_token=AsyncMock(
            return_value=SimpleNamespace(
                token="synthetic-workload-token", expires_on=permit.auth_expires_at.timestamp()
            )
        )
    )
    runtime = SimpleNamespace(send=AsyncMock(), close=AsyncMock())

    async def connect(url, **options):
        connections.append((url, options))
        clock[0] += timedelta(seconds=5)
        ticks[0] += 5
        return runtime

    async def ready():
        clock[0] += timedelta(seconds=3)
        ticks[0] += 3
        return json.dumps(
            {"type": "ready", "sessionId": "synthetic-test", "sequence": 1, "generation": 1}
        )

    runtime.recv = ready
    browser = SimpleNamespace(
        send_text=AsyncMock(), send_bytes=AsyncMock(), close=AsyncMock(), receive=AsyncMock()
    )
    client = TestSessionClient(
        endpoint="https://runtime.fixture.example",
        audience="11111111-2222-4333-8444-555555555555",
        approved_hosts=frozenset({"runtime.fixture.example"}),
        credential=credentials,
        utc_now=lambda: clock[0],
        monotonic=lambda: ticks[0],
        max_clock_skew_seconds=0,
        socket_factory=connect,
    )

    async def scenario():
        try:
            await client.run(
                browser,
                permit=permit,
                signed_permit="synthetic-signed-permit",
                validate_current=lambda: SimpleNamespace(
                    expires_at=permit.auth_expires_at.timestamp()
                ),
                deadline=asyncio.get_running_loop().time() + 8,
            )
        except PermissionError:
            pass
        browser.send_bytes.assert_not_awaited()
        assert not any(
            '"type":"ready"' in str(call) or '"type": "ready"' in str(call)
            for call in browser.send_text.await_args_list
        )
        browser.close.assert_awaited()
        await client.close()
        runtime.close.assert_awaited()

    asyncio.run(scenario())
    assert len(connections) == 1
    url, options = connections[0]
    assert url == "wss://runtime.fixture.example/v1/internal/test-sessions"
    assert options["proxy"] is None and options["compression"] is None
    assert (
        options["max_size"] == 8192
        and options["max_queue"] == 1
        and options["write_limit"] == 12000
    )
    assert options["additional_headers"]["Authorization"] == "Bearer synthetic-workload-token"


def ready_envelope(permit):
    return {
        "type": "ready",
        "sessionId": "runtime-test-fixture",
        "sequence": 1,
        "generation": 1,
        "mode": permit.mode,
        "revisionId": "published-fixture",
        "revisionNumber": 1,
        "agentId": "welcome",
        "expiresAt": (permit.admission_started_at + timedelta(seconds=600)).isoformat(),
        "idleExpiresAt": (permit.admission_started_at + timedelta(seconds=120)).isoformat(),
        "bounds": {
            "controlBytes": 8192,
            "textBytes": 4096,
            "replyBytes": 65536,
            "captureBytes": 12000,
            "playbackBytes": 24000,
            "inputFrameBytes": 1920,
        },
    }


def build_active_relay_case():
    from management_app.adapters.test_session_client import TestSessionClient
    from management_app.models.test_session import StartPermit

    permit = StartPermit.model_validate_json(json.dumps(permit_document()))
    permit = permit.model_copy(update={"mode": "text"})
    clock, ticks = [permit.issued_at], [2.0]
    credentials = SimpleNamespace(
        get_token=AsyncMock(
            return_value=SimpleNamespace(
                token="synthetic-workload-token", expires_on=permit.auth_expires_at.timestamp()
            )
        )
    )
    sent, displayed = [], []
    turn, stopped, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    runtime_index, browser_index = [0], [0]
    envelope = ready_envelope(permit)

    async def send(data):
        sent.append(data)
        value = json.loads(data)
        if value["type"] == "text.submit":
            turn.set()
        elif value["type"] == "stop":
            stopped.set()

    async def recv():
        index = runtime_index[0]
        runtime_index[0] += 1
        if index == 0:
            return json.dumps(envelope)
        await turn.wait()
        if index == 1:
            return json.dumps(
                {
                    "type": "text.delta",
                    "messageId": "text-fixture",
                    "text": "Synthetic exact reply",
                    "sessionId": envelope["sessionId"],
                    "sequence": 2,
                    "generation": 1,
                }
            )
        if index == 2:
            return json.dumps(
                {
                    "type": "text.completed",
                    "messageId": "text-fixture",
                    "text": "",
                    "sessionId": envelope["sessionId"],
                    "sequence": 3,
                    "generation": 1,
                }
            )
        await stopped.wait()
        return json.dumps(
            {
                "type": "ended",
                "code": "stopped",
                "cleanupStatus": "complete",
                "correlationId": "correlation-fixture",
                "sessionId": envelope["sessionId"],
                "sequence": 4,
                "generation": 1,
            }
        )

    async def receive():
        index = browser_index[0]
        browser_index[0] += 1
        if index == 0:
            clock[0], ticks[0] = permit.admission_started_at + timedelta(seconds=60), 60.0
            value = {
                "type": "text.submit",
                "text": "Synthetic exact user text",
                "sessionId": envelope["sessionId"],
                "sequence": 1,
            }
        else:
            await completed.wait()
            value = {"type": "stop", "sessionId": envelope["sessionId"], "sequence": 2}
        return {"type": "websocket.receive", "text": json.dumps(value)}

    async def display(data):
        displayed.append(json.loads(data))
        if displayed[-1]["type"] == "text.completed":
            completed.set()

    runtime = SimpleNamespace(send=send, recv=recv, close=AsyncMock())
    browser = SimpleNamespace(
        send_text=display, send_bytes=AsyncMock(), receive=receive, close=AsyncMock()
    )

    async def connect(url, **options):
        return runtime

    client = TestSessionClient(
        endpoint="https://runtime.fixture.example",
        audience="11111111-2222-4333-8444-555555555555",
        approved_hosts=frozenset({"runtime.fixture.example"}),
        credential=credentials,
        utc_now=lambda: clock[0],
        monotonic=lambda: ticks[0],
        max_clock_skew_seconds=0,
        socket_factory=connect,
    )
    return client, browser, runtime, permit, credentials, sent, displayed, clock, ticks, envelope


@pytest.fixture
def active_relay_case():
    return build_active_relay_case()


def test_relay_active_text_after_startup_expiry_is_exact_bounded_transport_not_execution(
    active_relay_case,
):
    client, browser, runtime, permit, credentials, sent, displayed, clock, ticks, envelope = (
        active_relay_case
    )

    async def scenario():
        await asyncio.wait_for(
            client.run(
                browser,
                permit=permit,
                signed_permit="synthetic-signed-permit",
                validate_current=lambda: SimpleNamespace(
                    expires_at=permit.auth_expires_at.timestamp()
                ),
                deadline=asyncio.get_running_loop().time() + 8,
            ),
            3,
        )
        await client.close()

    asyncio.run(scenario())
    assert [value["type"] for value in displayed] == [
        "ready",
        "text.delta",
        "text.completed",
        "ended",
    ]
    assert json.loads(sent[1])["text"] == "Synthetic exact user text"
    assert json.loads(sent[0])["permit"] == "synthetic-signed-permit"
    assert browser.send_bytes.await_count == 0 and credentials.get_token.await_count == 1


@pytest.mark.parametrize(
    "fault,code",
    [
        ("mode", 1008),
        ("session", 1008),
        ("unknown-field", 1008),
        ("oversized", 1009),
        ("logout", 1008),
    ],
)
def test_relay_rejects_untrusted_runtime_or_lost_permission_before_forwarding(
    active_relay_case, fault, code
):
    client, browser, runtime, permit, credentials, sent, displayed, clock, ticks, envelope = (
        active_relay_case
    )
    recv, display = runtime.recv, browser.send_text
    valid = [True]

    async def altered_recv():
        raw = await recv()
        value = json.loads(raw)
        if fault == "mode" and value["type"] == "ready":
            value["mode"] = "speech"
        elif value["type"] == "text.delta":
            if fault == "session":
                value["sessionId"] = "foreign-session"
            elif fault == "unknown-field":
                value["provider_body"] = "synthetic-private-body"
            elif fault == "oversized":
                value["text"] = "x" * 8193
        return json.dumps(value)

    async def revoke_after_ready(raw):
        await display(raw)
        if fault == "logout" and json.loads(raw)["type"] == "ready":
            valid[0] = False

    runtime.recv, browser.send_text = altered_recv, revoke_after_ready

    async def scenario():
        await asyncio.wait_for(
            client.run(
                browser,
                permit=permit,
                signed_permit="synthetic-signed-permit",
                validate_current=lambda: (
                    SimpleNamespace(expires_at=permit.auth_expires_at.timestamp())
                    if valid[0]
                    else None
                ),
                deadline=asyncio.get_running_loop().time() + 8,
            ),
            3,
        )
        await client.close()

    asyncio.run(scenario())
    assert browser.close.await_args.kwargs["code"] == code
    assert not any(value["type"] in {"text.delta", "text.completed"} for value in displayed)
    assert "synthetic-private-body" not in json.dumps(displayed)


def test_relay_binary_credit_and_played_accounting_is_generation_bound_and_content_free():
    import struct

    from management_app.adapters.test_session_client import RelayFrames

    frames = RelayFrames(mode="speech", session_id="runtime-fixture", generation=1)
    credit = json.dumps(
        {
            "type": "audio.credit",
            "sessionId": "runtime-fixture",
            "sequence": 2,
            "generation": 1,
            "availableSamples": 6000,
            "acceptedAudioSequence": 0,
        }
    )
    assert frames.from_runtime(credit)
    pcm = b"\0" * 1920
    for sequence in range(1, 7):
        assert frames.from_browser(struct.pack(">BII", 1, 1, sequence) + pcm)
    with pytest.raises(PermissionError, match="capture_credit_exhausted"):
        frames.from_browser(struct.pack(">BII", 1, 1, 7) + b"\0" * 960)
    assert frames.capture_bytes == 11520
    frames.from_runtime(
        json.dumps(
            {
                "type": "audio.credit",
                "sessionId": "runtime-fixture",
                "sequence": 3,
                "generation": 1,
                "availableSamples": 6000,
                "acceptedAudioSequence": 6,
            }
        )
    )
    assert frames.capture_bytes == 0
    for sequence in range(1, 26):
        assert frames.from_runtime(struct.pack(">BII", 1, 1, sequence) + b"\0" * 960)
    assert frames.playback_bytes == 24000
    with pytest.raises(PermissionError, match="playback_credit_exhausted"):
        frames.from_runtime(struct.pack(">BII", 1, 1, 26) + b"\0" * 960)
    played = json.dumps(
        {
            "type": "audio.played",
            "sessionId": "runtime-fixture",
            "sequence": 1,
            "generation": 1,
            "audioSequence": 25,
        }
    )
    with pytest.raises(PermissionError, match="invalid_played_ack"):
        frames.from_browser(played)
    for sequence in range(1, 26):
        frames.sent_audio(sequence, generation=1)
    assert frames.from_browser(played) and frames.playback_bytes == 0
    changed = json.dumps(
        {
            "type": "generation.changed",
            "sessionId": "runtime-fixture",
            "sequence": 4,
            "generation": 2,
            "reason": "segment_retired",
        }
    )
    assert frames.from_runtime(changed)
    assert not frames.from_runtime(struct.pack(">BII", 1, 1, 26) + b"\0" * 960)
    assert frames.generation == 2 and frames.capture_bytes == frames.playback_bytes == 0
    frames.clear()


@pytest.mark.parametrize("fault", ["mode", "odd", "header", "oversized", "generation", "sequence"])
def test_relay_binary_rejects_malformed_or_unauthorized_frames(fault):
    import struct

    from management_app.adapters.test_session_client import RelayFrames

    frames = RelayFrames(
        mode="text" if fault == "mode" else "speech", session_id="runtime-fixture", generation=1
    )
    frame = struct.pack(
        ">BII",
        2 if fault == "header" else 1,
        2 if fault == "generation" else 1,
        2 if fault == "sequence" else 1,
    )
    frame += b"\0" * (1922 if fault == "oversized" else 959 if fault == "odd" else 960)
    with pytest.raises(PermissionError):
        frames.from_browser(frame)
    assert frames.capture_bytes == frames.playback_bytes == 0


def test_relay_speech_binary_uses_same_bounded_writers_and_played_ack():
    import struct

    from management_app.adapters.test_session_client import TestSessionClient
    from management_app.models.test_session import StartPermit

    permit = StartPermit.model_validate_json(json.dumps(permit_document()))
    envelope = ready_envelope(permit) | {
        "audioFormat": {"codec": "pcm16le", "sampleRate": 24000, "channels": 1}
    }
    credited, captured, played, stopped = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    runtime_index, browser_index = [0], [0]
    sent, shown, audio = [], [], []
    pcm = b"\x05\0" * 480
    frame = struct.pack(">BII", 1, 1, 1) + pcm

    async def send(data):
        sent.append(data)
        if isinstance(data, bytes):
            assert data == frame
            captured.set()
        elif json.loads(data)["type"] == "stop":
            stopped.set()

    async def recv():
        index = runtime_index[0]
        runtime_index[0] += 1
        if index == 0:
            return json.dumps(envelope)
        if index == 1:
            return json.dumps(
                {
                    "type": "audio.credit",
                    "sessionId": envelope["sessionId"],
                    "sequence": 2,
                    "generation": 1,
                    "availableSamples": 6000,
                    "acceptedAudioSequence": 0,
                }
            )
        if index == 2:
            await captured.wait()
            return frame
        await stopped.wait()
        return json.dumps(
            {
                "type": "ended",
                "sessionId": envelope["sessionId"],
                "sequence": 3,
                "generation": 1,
                "code": "stopped",
                "cleanupStatus": "complete",
                "correlationId": "correlation-fixture",
            }
        )

    async def receive():
        index = browser_index[0]
        browser_index[0] += 1
        if index == 0:
            await credited.wait()
            return {"type": "websocket.receive", "bytes": frame}
        if index == 1:
            await played.wait()
            value = {
                "type": "audio.played",
                "sessionId": envelope["sessionId"],
                "sequence": 1,
                "generation": 1,
                "audioSequence": 1,
            }
        else:
            value = {"type": "stop", "sessionId": envelope["sessionId"], "sequence": 2}
        return {"type": "websocket.receive", "text": json.dumps(value)}

    async def display(data):
        shown.append(json.loads(data))
        if shown[-1]["type"] == "audio.credit":
            credited.set()

    async def playback(data):
        assert data == frame
        audio.append(data)
        played.set()

    runtime = SimpleNamespace(send=send, recv=recv, close=AsyncMock())
    browser = SimpleNamespace(
        send_text=display, send_bytes=playback, receive=receive, close=AsyncMock()
    )
    credentials = SimpleNamespace(
        get_token=AsyncMock(
            return_value=SimpleNamespace(
                token="synthetic-workload-token", expires_on=permit.auth_expires_at.timestamp()
            )
        )
    )

    async def connect(url, **options):
        return runtime

    client = TestSessionClient(
        endpoint="https://runtime.fixture.example",
        audience="11111111-2222-4333-8444-555555555555",
        approved_hosts=frozenset({"runtime.fixture.example"}),
        credential=credentials,
        utc_now=lambda: permit.issued_at,
        monotonic=lambda: 2.0,
        max_clock_skew_seconds=0,
        socket_factory=connect,
    )

    async def scenario():
        await asyncio.wait_for(
            client.run(
                browser,
                permit=permit,
                signed_permit="synthetic-signed-permit",
                validate_current=lambda: SimpleNamespace(
                    expires_at=permit.auth_expires_at.timestamp()
                ),
                deadline=asyncio.get_running_loop().time() + 8,
            ),
            3,
        )
        await client.close()

    asyncio.run(scenario())
    assert audio == [frame] and frame in sent
    assert [value["type"] for value in shown] == ["ready", "audio.credit", "ended"]
    assert any(
        isinstance(value, str) and json.loads(value)["type"] == "audio.played" for value in sent
    )


def test_relay_control_mute_and_interrupt_fence_old_audio_without_inventing_task_success():
    import struct

    from management_app.adapters.test_session_client import RelayFrames

    frames = RelayFrames(mode="speech", session_id="runtime-fixture", generation=1)
    frames.from_runtime(
        json.dumps(
            {
                "type": "audio.credit",
                "sessionId": "runtime-fixture",
                "sequence": 2,
                "generation": 1,
                "availableSamples": 6000,
                "acceptedAudioSequence": 0,
            }
        )
    )
    muted = json.dumps(
        {"type": "input.mute", "sessionId": "runtime-fixture", "sequence": 1, "muted": True}
    )
    assert frames.from_browser(muted)
    with pytest.raises(PermissionError, match="test_input_muted"):
        frames.from_browser(struct.pack(">BII", 1, 1, 1) + b"\0" * 960)
    assert frames.from_browser(
        json.dumps(
            {"type": "input.mute", "sessionId": "runtime-fixture", "sequence": 2, "muted": False}
        )
    )
    assert frames.from_browser(struct.pack(">BII", 1, 1, 1) + b"\0" * 960)
    interrupt = json.dumps(
        {"type": "output.interrupt", "sessionId": "runtime-fixture", "sequence": 3, "generation": 1}
    )
    assert frames.from_browser(interrupt)
    assert not frames.from_runtime(struct.pack(">BII", 1, 1, 1) + b"\0" * 960)
    assert frames.from_runtime(
        json.dumps(
            {
                "type": "generation.changed",
                "sessionId": "runtime-fixture",
                "sequence": 3,
                "generation": 2,
                "reason": "segment_retired",
            }
        )
    )
    assert frames.from_browser(interrupt)
    assert frames.from_runtime(struct.pack(">BII", 1, 2, 1) + b"\0" * 960)
    assert frames.playback_bytes == 960
    frames.clear()


@pytest.mark.parametrize("fault", ["error-code", "ack", "agent-community"])
def test_relay_server_metadata_values_cannot_be_used_to_leak_provider_payload(
    active_relay_case, fault
):
    client, browser, runtime, permit, credentials, sent, displayed, clock, ticks, envelope = (
        active_relay_case
    )
    recv = runtime.recv

    async def altered():
        raw = await recv()
        value = json.loads(raw)
        if value["type"] == "text.delta":
            base = {"sessionId": envelope["sessionId"], "sequence": 2, "generation": 1}
            if fault == "error-code":
                value = base | {
                    "type": "error",
                    "code": "synthetic-private-provider-body",
                    "cleanupStatus": "pending",
                    "correlationId": "correlation-fixture",
                }
            elif fault == "ack":
                value = base | {"type": "command.ack", "acceptedSequence": 0}
            else:
                value = base | {
                    "type": "agent.changed",
                    "agentId": "entry",
                    "communityId": {"payload": "synthetic-private-provider-body"},
                    "generation": 2,
                }
        return json.dumps(value)

    runtime.recv = altered

    async def scenario():
        await asyncio.wait_for(
            client.run(
                browser,
                permit=permit,
                signed_permit="synthetic-signed-permit",
                validate_current=lambda: SimpleNamespace(
                    expires_at=permit.auth_expires_at.timestamp()
                ),
                deadline=asyncio.get_running_loop().time() + 8,
            ),
            3,
        )
        await client.close()

    asyncio.run(scenario())
    assert browser.close.await_args.kwargs["code"] == 1008
    assert [value["type"] for value in displayed] == ["ready"]
    assert "synthetic-private" not in json.dumps(displayed)


def test_relay_initial_clock_cannot_predate_verified_signed_issuance(active_relay_case):
    client, browser, runtime, permit, credentials, sent, displayed, clock, ticks, envelope = (
        active_relay_case
    )
    clock[0] = permit.issued_at - timedelta(seconds=1)
    ticks[0] = 1.0

    async def scenario():
        await asyncio.wait_for(
            client.run(
                browser,
                permit=permit,
                signed_permit="synthetic-signed-permit",
                validate_current=lambda: SimpleNamespace(
                    expires_at=permit.auth_expires_at.timestamp()
                ),
                deadline=asyncio.get_running_loop().time() + 8,
            ),
            3,
        )
        await client.close()

    asyncio.run(scenario())
    credentials.get_token.assert_not_awaited()
    assert not displayed and not sent


def test_relay_accepts_documented_bounded_work_error_message_id(active_relay_case):
    client, browser, runtime, permit, credentials, sent, displayed, clock, ticks, envelope = (
        active_relay_case
    )
    original = runtime.recv

    async def receive():
        value = await original()
        parsed = json.loads(value)
        if parsed["type"] == "text.delta":
            parsed = {key: parsed[key] for key in ("sessionId", "sequence", "generation")}
            parsed.update(
                type="error",
                code="work_busy",
                cleanupStatus="active",
                correlationId="fixture-correlation",
                messageId="fixture-work",
            )
        return json.dumps(parsed)

    runtime.recv = receive

    async def scenario():
        await asyncio.wait_for(
            client.run(
                browser,
                permit=permit,
                signed_permit="synthetic-signed-permit",
                validate_current=lambda: SimpleNamespace(
                    expires_at=permit.auth_expires_at.timestamp()
                ),
                deadline=asyncio.get_running_loop().time() + 8,
            ),
            3,
        )
        await client.close()

    asyncio.run(scenario())
    assert any(
        value.get("messageId") == "fixture-work" and value["type"] == "error" for value in displayed
    )
    assert browser.close.await_args.kwargs["code"] == 1000


def test_browser_writer_pressure_and_unanswered_permission_are_bounded_and_terminal():
    import shutil
    import subprocess

    from management_app.components.live_audio import BUDGET_CODE, ENGINE_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    scenario = """
const document = {getElementById: () => null};
const timers = new Map(); let timerId = 0;
function setTimeout(callback) { timers.set(++timerId, callback); return timerId; }
function clearTimeout(identifier) { timers.delete(identifier); }
class WebSocket { static OPEN = 1; }
const events = {begin: () => {}, control: () => {}};
let closed = 0;
const pressure = new CallerTestEngine(events);
pressure.terminal = false;
pressure.socket = {readyState: 1, bufferedAmount: 32769, close: () => closed++};
pressure.enqueue("synthetic-control");
if (!pressure.terminal || closed !== 1 || pressure.queue.length) throw Error("buffered_overflow");
const queued = new CallerTestEngine(events); queued.terminal = false;
for (let index = 0; index < 64; index++) queued.enqueue("synthetic-control");
let rejected = false;
try { queued.enqueue("synthetic-control"); } catch { rejected = true; }
if (!rejected || queued.queue.length !== 64) throw Error("unbounded_controls");
queued.stop();
let grant, tracksStopped = 0, contextsClosed = 0, requests = 0;
class AudioContext {
    constructor() { this.sampleRate = 24000; this.state = "running"; }
    resume() { return Promise.resolve(); }
    close() { contextsClosed++; return Promise.resolve(); }
}
const window = {AudioContext, isSecureContext: true,
    location: {protocol: "https:", origin: "https://manager.fixture.example"}};
const navigator = {mediaDevices: {getUserMedia: () =>
    new Promise(resolve => grant = resolve)}};
function fetch() { requests++; throw Error("permission_not_granted"); }
(async () => {
    const pending = new CallerTestEngine(events);
    const operation = pending.start("speech");
    for (const callback of [...timers.values()]) callback();
    if (!pending.terminal || contextsClosed !== 1 || requests) throw Error("unanswered_start");
    grant({getTracks: () => [{stop: () => tracksStopped++}]});
    await operation;
    if (tracksStopped !== 1 || requests || timers.size) throw Error("late_callback_resource_leak");
    console.log("browser-pressure-pass");
})().catch(() => { process.exitCode = 1; });
"""
    result = subprocess.run(
        [node],
        input=BUDGET_CODE + ENGINE_CODE + scenario,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "browser-pressure-pass" and not result.stderr


@pytest.mark.parametrize(
    "failure,label",
    [
        ("rate", "Unsupported audio sample rate"),
        ("playback", "Playback unavailable"),
        ("sink", "Output unavailable"),
    ],
)
def test_browser_audio_unavailability_is_not_mislabeled_permission(failure, label):
    import shutil
    import subprocess

    from management_app.components.live_audio import BUDGET_CODE, ENGINE_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    scenario = """
const failure = FAILURE, expected = EXPECTED;
const error = {textContent: ""}, output = {disabled: false};
const document = {getElementById: name => name === "test-error" ? error :
    name === "test-output-device" ? output : null};
let stopped = 0, closed = 0, requests = 0;
class AudioContext {
    constructor() { this.sampleRate = failure === "rate" ? 48000 : 24000;
        this.state = "running"; this.audioWorklet = {}; }
    resume() { return failure === "playback" ? Promise.reject(Error("synthetic")) :
        Promise.resolve(); }
    close() { closed++; return Promise.resolve(); }
}
const window = {AudioContext, isSecureContext: true,
    location: {protocol: "https:", origin: "https://manager.fixture.example"}};
const navigator = {mediaDevices: {getUserMedia: async () => ({getTracks: () => [{
    stop: () => stopped++
}]})}};
function fetch() { requests++; throw Error("media_must_precede_ticket"); }
(async () => {
    const engine = new CallerTestEngine({begin: () => {}, control: () => {}});
    await engine.start("speech", "", failure === "sink" ? "synthetic-device" : "");
    if (!engine.terminal || requests || closed !== 1 || error.textContent !== expected)
        throw Error("incorrect_audio_failure_state");
    if (!output.disabled) throw Error("unsupported_output_enabled");
    if (failure !== "rate" && stopped !== 1) throw Error("failed_media_not_disposed");
    console.log("truthful-device-pass");
})().catch(() => { process.exitCode = 1; });
""".replace("FAILURE", json.dumps(failure)).replace("EXPECTED", json.dumps(label))
    result = subprocess.run(
        [node],
        input=BUDGET_CODE + ENGINE_CODE + scenario,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "truthful-device-pass" and not result.stderr


def test_browser_reply_and_structural_history_limits_match_declared_context():
    import shutil
    import subprocess

    from management_app.components.live_audio import BUDGET_CODE, ENGINE_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    scenario = """
const messages = {children: [], append(row) {this.children.push(row);},
    replaceChildren() {this.children = [];}, scrollHeight: 0};
const document = {getElementById: name => name === 'test-messages' ? messages : null,
    createElement: () => ({textContent: '', dataset: {}, style: {}})};
class WebSocket { static OPEN = 1; }
const engine = new CallerTestEngine({begin: () => {}, control: () => {}});
engine.terminal = false; engine.mode = 'text'; engine.epoch = 1;
engine.snapshot = {status: 'ready', mode: 'text', sessionId: 'fixture-session', generation: 1};
engine.socket = {readyState: 1, bufferedAmount: 0, send: () => {}, close: () => {}};
engine.submit('q'.repeat(4096));
let sequence = 1;
function receive(type, messageId, text) {
    engine.message(JSON.stringify({type, sequence: ++sequence, generation: 1,
        sessionId: 'fixture-session', messageId, text}), 1);
}
for (let index = 0; index < 16; index++) receive('text.delta', 'maximum-reply', 'a'.repeat(4096));
if (engine.terminal || engine.contentBytes < 69632) throw Error('valid_reply_rejected');
let rejected = false;
try { receive('text.delta', 'maximum-reply', 'x'); } catch { rejected = true; }
if (!rejected) throw Error('oversized_reply_accepted');
receive('text.completed', 'maximum-reply', '');
let bounded = false;
try {
    for (let index = 0; index < 200; index++) receive('text.completed', 'short-' + index, 'x');
} catch { bounded = true; engine.stop('protocol_error'); }
if (!bounded || !engine.terminal || messages.children.length || engine.contentBytes)
    throw Error('unbounded_structural_history');
console.log('browser-history-pass');
"""
    result = subprocess.run(
        [node],
        input=BUDGET_CODE + ENGINE_CODE + scenario,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "browser-history-pass" and not result.stderr


def test_relay_admission_counts_owned_unfinished_disposal(active_relay_case):
    client, browser, runtime, permit, credentials, sent, displayed, clock, ticks, envelope = (
        active_relay_case
    )

    async def scenario():
        pending = [asyncio.create_task(asyncio.Event().wait()) for _ in range(5)]
        for task in pending:
            client._cleanup.add(task)
            task.add_done_callback(client._cleanup.discard)
        try:
            await asyncio.wait_for(
                client.run(
                    browser,
                    permit=permit,
                    signed_permit="synthetic-signed-permit",
                    validate_current=lambda: SimpleNamespace(
                        expires_at=permit.auth_expires_at.timestamp()
                    ),
                    deadline=asyncio.get_running_loop().time() + 8,
                ),
                3,
            )
            credentials.get_token.assert_not_awaited()
            assert not displayed and not sent and client._active == 0
            assert browser.close.await_args.kwargs["code"] == 1013
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await client.close()

    asyncio.run(scenario())


def test_console_page_has_accessible_native_controls_and_no_content_state_binding():
    from management_app.pages.test_console import test_console_page

    tree = json.dumps(test_console_page().render(), default=str)
    for identifier in (
        "test-start",
        "test-stop",
        "test-mode",
        "test-input-device",
        "test-output-device",
        "test-form",
        "test-text",
        "test-send",
        "test-mute",
        "test-interrupt",
        "test-messages",
    ):
        assert identifier in tree
    assert "Start published test" in tree and "Stop test" in tree and "Send text" in tree
    assert "update_control" in tree and "test_console_state" in tree
    assert "local_storage" not in tree and "set_text" not in tree


def test_live_audio_is_a_narrow_reflex_owned_metadata_component():
    import reflex as rx

    from management_app.components.live_audio import LiveAudio

    component = LiveAudio.create(authorized=False)
    assert isinstance(component, rx.Component)
    assert set(component.add_imports()) == {"react"}
    assert set(component.get_event_triggers()) >= {"on_begin", "on_control"}
    code = "\n".join(component.add_custom_code())
    assert "visibilitychange" in code and "devicechange" in code and "pagehide" in code
    assert (
        "localStorage" not in code and "sessionStorage" not in code and "MediaRecorder" not in code
    )


def test_browser_bridge_pins_ready_and_waits_for_confirmed_end_before_restart():
    import shutil
    import subprocess

    from management_app.components.live_audio import BUDGET_CODE, ENGINE_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    scenario = """
const document = {getElementById: () => null};
class WebSocket { static OPEN = 1; }
const events = [], sent = [], engine = new CallerTestEngine({begin: () => {},
    control: value => events.push(JSON.parse(value))});
engine.terminal = false; engine.epoch = 1; engine.mode = "text";
engine.deadline = performance.now() + 10000;
engine.snapshot = {status: "starting", mode: "text"}; engine.authenticated = true;
engine.socket = {readyState: 1, bufferedAmount: 0,
    send: value => sent.push(JSON.parse(value)), close: () => {}};
const expiresAt = new Date(Date.now() + 599000).toISOString();
const idleExpiresAt = new Date(Date.now() + 119000).toISOString();
engine.message(JSON.stringify({type: "ready", sequence: 1, generation: 1,
    sessionId: "session-fixture", mode: "text", revisionId: "published-fixture",
    revisionNumber: 7, agentId: "welcome", expiresAt, idleExpiresAt,
    bounds: {controlBytes: 8192, textBytes: 4096, replyBytes: 65536,
        captureBytes: 12000, playbackBytes: 24000, inputFrameBytes: 1920}}), 1);
engine.submit("Synthetic question");
if (sent.length !== 1 || sent[0].type !== "text.submit" ||
    sent[0].sessionId !== "session-fixture" || "revisionId" in sent[0])
    throw Error("client_authority_override");
engine.message(JSON.stringify({type: "error", sequence: 2, generation: 1,
    sessionId: "session-fixture", code: "work_busy", cleanupStatus: "active",
    correlationId: "fixture-correlation", messageId: "fixture-work"}), 1);
if (engine.pending.size || engine.terminal) throw Error("busy_marked_complete_or_stuck");
engine.stop();
if (!engine.terminal || engine.snapshot.cleanupStatus !== "pending") throw Error("stop_not_fenced");
engine.message(JSON.stringify({type: "ended", sequence: 3, generation: 1,
    sessionId: "session-fixture",
    code: "stopped", cleanupStatus: "complete", correlationId: "correlation-fixture"}), 1);
if (engine.snapshot.status !== "ended" || engine.snapshot.cleanupStatus !== "complete" ||
    engine.contentBytes || engine.socket)
    throw Error("confirmed_cleanup_not_applied");
if (engine.snapshot.revisionId !== "published-fixture" || engine.snapshot.revisionNumber !== 7)
    throw Error("displayed_revision_changed");
const error = {textContent: ""};
engine.element = name => name === "error" ? error : null;
engine.report("ended", "permission_denied", "complete");
if (error.textContent !== "Permission denied") throw Error("hidden_speech_failure");
const previous = events.length;
engine.element = CallerTestEngine.prototype.element;
document.getElementById = () => error;
engine.dispose();
engine.report("ended", "unavailable", "complete");
if (events.length !== previous || engine.element("error") !== null)
    throw Error("detached_engine_mutated_new_view");
console.log("published-end-pass");
"""
    result = subprocess.run(
        [node],
        input=BUDGET_CODE + ENGINE_CODE + scenario,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "published-end-pass" and not result.stderr


@pytest.mark.parametrize("callback", ["close", "error", "message"])
def test_browser_stale_socket_callbacks_cannot_stop_explicit_replacement(callback):
    import shutil
    import subprocess

    from management_app.components.live_audio import BUDGET_CODE, ENGINE_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    scenario = """
const document = {getElementById: () => null};
const window = {location: {protocol: 'https:', origin: 'https://manager.fixture.example'}};
const sockets = [];
class WebSocket {
    static OPEN = 1;
    constructor() {this.readyState = 0; this.bufferedAmount = 0; sockets.push(this);}
    send() {} close() {this.readyState = 3;}
}
async function fetch(path, options) {
    return new Response(JSON.stringify(path === '/auth/session' ? {csrf: 'a'.repeat(64)} : {
        ...JSON.parse(options.body), socketPath: '/v1/test-sessions', permit: 'fixture',
        admissionDeadlineUtc: new Date(Date.now() + 10000).toISOString()}));
}
(async () => {
    const engine = new CallerTestEngine({begin() {}, control() {}});
    await engine.start('text');
    const old = sockets[0]; old.readyState = 1; old.onopen();
    engine.snapshot = {...engine.snapshot, sessionId: 'old-session', generation: 1};
    engine.stop();
    old.onmessage({data: JSON.stringify({type: 'ended', sessionId: 'old-session',
        sequence: 1, generation: 1, code: 'stopped', cleanupStatus: 'complete',
        correlationId: 'fixture-correlation'})});
    if (engine.snapshot.cleanupStatus !== 'complete') throw Error('cleanup_lost');
    await engine.start('text');
    const current = engine.socket, abort = engine.abort;
    try {
        if (CALLBACK === 'close') old.onclose({code: 1000, reason: 'stopped'});
        else if (CALLBACK === 'error') old.onerror({});
        else old.onmessage({data: '{'});
        if (engine.terminal || engine.snapshot.status !== 'starting' ||
            engine.socket !== current || engine.abort !== abort || abort.signal.aborted)
            throw Error('stale_socket_stopped_replacement');
        current.readyState = 1; current.onopen(); current.onerror({});
        if (!engine.terminal || engine.snapshot.code !== 'unavailable')
            throw Error('current_socket_failure_ignored');
    } finally {engine.finish('stopped', 'complete');}
    console.log('stale-socket-pass');
})().catch(error => {console.error(error.message); process.exitCode = 1;});
""".replace("CALLBACK", json.dumps(callback))
    result = subprocess.run(
        [node],
        input=BUDGET_CODE + ENGINE_CODE + scenario,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "stale-socket-pass" and not result.stderr


def test_browser_bridge_cancels_late_permission_and_text_never_acquires_media():
    import shutil
    import subprocess

    from management_app.components.live_audio import BUDGET_CODE, ENGINE_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    harness = """
const document = {getElementById: () => null, visibilityState: "visible"};
let grant, auth, captures = 0, contexts = 0, closed = 0, stopped = 0, requests = 0;
const navigator = {mediaDevices: {getUserMedia: () => {
    captures++; return new Promise(resolve => grant = resolve);
}}};
class AudioContext {
    constructor(options) {
        if (options.sampleRate !== 24000) throw Error("requested_rate");
        contexts++; this.sampleRate = 24000; this.state = "running";
    }
    resume() { return Promise.resolve(); }
    close() { closed++; return Promise.resolve(); }
}
const window = {AudioContext, isSecureContext: true,
    location: {protocol: "https:", origin: "https://manager.fixture.example"}};
function fetch(url, options) {
    requests++; if (options.redirect !== "error") throw Error("redirect_allowed");
    return new Promise(resolve => auth = resolve);
}
"""
    scenario = """
(async () => {
    const controls = [], engine = new CallerTestEngine({begin: () => {},
        control: value => controls.push(JSON.parse(value))});
    const speech = engine.start("speech");
    engine.stop();
    grant({getTracks: () => [{stop: () => stopped++}]});
    await speech;
    if (captures !== 1 || contexts !== 1 || stopped !== 1 || closed !== 1 || requests)
        throw Error("late_permission_resource_leak");
    const text = engine.start("text");
    engine.stop();
    auth(new Response(JSON.stringify({actor_ref: "fixture", csrf: "a".repeat(64)})));
    await text;
    if (captures !== 1 || contexts !== 1 || requests !== 1) throw Error("text_acquired_media");
    if (controls.some(value => Object.keys(value).some(key =>
        ["permit", "text", "audio", "csrf"].includes(key))))
        throw Error("control_content_leak");
    console.log("browser-lifecycle-pass");
})().catch(() => { process.exitCode = 1; });
"""
    result = subprocess.run(
        [node],
        input=harness + BUDGET_CODE + ENGINE_CODE + scenario,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "browser-lifecycle-pass" and not result.stderr


def test_browser_native_pcm_worklet_bounds_capture_playback_and_flushes():
    import shutil
    import subprocess

    from management_app.components.live_audio import WORKLET_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    harness = """
const sampleRate = 24000, messages = [];
class AudioWorkletProcessor {
    constructor() { this.port = {postMessage: value => messages.push(value)}; }
}
let Processor;
function registerProcessor(name, value) {
    if (name !== "caller-pcm") throw Error("processor_name"); Processor = value;
}
"""
    scenario = """
const processor = new Processor();
const command = value => processor.port.onmessage({data: value});
const input = new Float32Array(128).fill(0.5), output = new Float32Array(128);
processor.process([[input]], [[output]]);
if (messages.length || processor.capture !== null) throw Error("hidden_capture");
command({type: "grant", generation: 1, captureEpoch: 1});
for (let block = 0; block < 4; block++) processor.process([[input]], [[output]]);
const captured = messages.find(value => value.type === "capture");
if (!captured || captured.captureEpoch !== 1 || captured.pcm.byteLength !== 960 ||
    new DataView(captured.pcm).getInt16(0, true) !== 16384)
    throw Error("pcm_format");
if (processor.capture !== null) throw Error("capture_retained");
for (let sequence = 1; sequence <= 25; sequence++)
    command({type: "play", generation: 1, sequence, pcm: new ArrayBuffer(960)});
if (processor.playbackBytes !== 24000) throw Error("playback_bound");
command({type: "play", generation: 1, sequence: 26, pcm: new ArrayBuffer(960)});
if (!messages.some(value => value.type === "failed") || processor.playbackBytes)
    throw Error("overflow_fence");
const cleared = new Processor();
cleared.port.onmessage({data: {type: "grant", generation: 1, captureEpoch: 1}});
cleared.process([[input]], [[output]]);
cleared.port.onmessage({data: {type: "clear", generation: 2, captureEpoch: 1,
    clearSequence: 1}});
if (cleared.capture !== null || cleared.playbackBytes || cleared.generation !== 2)
    throw Error("flush");
const boundary = new Processor(), before = messages.length;
const apply = value => boundary.port.onmessage({data: {generation: 1, ...value}});
apply({type: 'grant', captureEpoch: 1});
boundary.process([[input]], [[output]]);
apply({type: 'mute', muted: true, captureEpoch: 2});
apply({type: 'mute', muted: false, captureEpoch: 3});
apply({type: 'grant', captureEpoch: 1});
if (boundary.grants || boundary.capture || boundary.captureEpoch !== 3)
    throw Error('stale_grant_after_mute');
apply({type: 'grant', captureEpoch: 3});
for (let block = 0; block < 4; block++) boundary.process([[input]], [[output]]);
apply({type: 'clear', captureEpoch: 4, clearSequence: 2});
apply({type: 'clear', captureEpoch: 3, clearSequence: 1});
const current = messages.slice(before);
if (current.some(value => value.type === 'failed') ||
    !current.some(value => value.type === 'capture' && value.captureEpoch === 3) ||
    !current.some(value => value.type === 'capture.flushed' && value.captureEpoch === 3) ||
    !current.some(value => value.type === 'cleared' && value.clearSequence === 2) ||
    boundary.captureEpoch !== 4 || boundary.clearSequence !== 2)
    throw Error('worklet_flush_identity');
console.log("native-pcm-pass");
"""
    result = subprocess.run(
        [node],
        input=harness + WORKLET_CODE + scenario,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "native-pcm-pass" and not result.stderr


@pytest.mark.parametrize("boundary", ["mute", "interrupt"])
def test_browser_capture_flush_rejects_stale_frames_and_waits_for_current_ack(boundary):
    import shutil
    import subprocess

    from management_app.components.live_audio import BUDGET_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    scenario = """
const budget = new CallerAudioBudget(); budget.credit(6000, 0); budget.grant();
const oldEpoch = budget.captureEpoch;
if (BOUNDARY === 'mute') {budget.mute(true); budget.mute(false);}
else budget.interrupt();
if (budget.grant()) throw Error('capture_granted_before_flush_ack');
if (budget.capture(new ArrayBuffer(960), 1, oldEpoch) !== null)
    throw Error('pre_flush_capture_accepted');
if (budget.flushed(oldEpoch) || budget.grant()) throw Error('stale_flush_ack');
if (!budget.flushed(budget.captureEpoch)) throw Error('current_flush_ack_rejected');
if (BOUNDARY === 'interrupt') {
    if (budget.grant()) throw Error('interrupted_generation_granted');
    budget.reset(2); budget.credit(6000, 0);
}
if (!budget.grant() || !budget.capture(new ArrayBuffer(960), budget.generation,
    budget.captureEpoch) || budget.captureSamples !== 480)
    throw Error('fresh_capture_or_accounting_failed');
console.log('capture-flush-pass');
""".replace("BOUNDARY", json.dumps(boundary))
    result = subprocess.run(
        [node],
        input=BUDGET_CODE + scenario,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "capture-flush-pass" and not result.stderr


def test_browser_audio_budget_bounds_credit_and_generation_without_devices():
    import shutil
    import subprocess

    from management_app.components.live_audio import BUDGET_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    scenario = """
const budget = new CallerAudioBudget();
budget.reset(1);
budget.credit(6000, 0);
let reserved = 0;
while (budget.grant()) {
    reserved++;
    budget.capture(new ArrayBuffer(960), 1, budget.captureEpoch);
}
if (reserved !== 12 || budget.captureSamples !== 5760) throw Error("capture_cap");
budget.credit(6000, 0);
if (budget.grant()) throw Error("duplicate_credit");
budget.credit(6000, 1);
if (!budget.grant()) throw Error("acknowledged_credit");
budget.reset(2);
if (budget.capture(new ArrayBuffer(960), 1, budget.captureEpoch) !== null)
    throw Error("stale_capture");
function frame(sequence, generation = 2) {
    const raw = new ArrayBuffer(969), view = new DataView(raw);
    view.setUint8(0, 1); view.setUint32(1, generation); view.setUint32(5, sequence);
    return raw;
}
for (let sequence = 1; sequence <= 25; sequence++) budget.receive(frame(sequence));
let overflow = false;
try { budget.receive(frame(26)); } catch { overflow = true; }
if (!overflow || budget.playbackBytes !== 24000) throw Error("playback_cap");
budget.played(1, 2);
if (budget.playbackBytes !== 23040) throw Error("played_accounting");
budget.interrupt();
if (budget.receive(frame(26)) !== null || budget.playbackBytes !== 0)
    throw Error("interrupt_fence");
budget.reset(3);
if (budget.receive(frame(1, 2)) !== null) throw Error("stale_output");
console.log("bounded-media-pass");
"""
    result = subprocess.run(
        [node], input=BUDGET_CODE + scenario, text=True, capture_output=True, timeout=10, check=True
    )
    assert result.stdout.strip() == "bounded-media-pass" and not result.stderr


@pytest.mark.parametrize("state", ["suspended", "interrupted", "closed"])
def test_browser_active_playback_loss_fences_media_and_detaches_old_context(state):
    import shutil
    import subprocess

    from management_app.components.live_audio import BUDGET_CODE, ENGINE_CODE

    node = shutil.which("node")
    assert node is not None, "prepared_node_required"
    scenario = """
const document = {getElementById: () => null};
let closed = 0, stopped = 0;
const track = {enabled: true, stop() {stopped++;}};
const navigator = {mediaDevices: {getUserMedia: async () => ({getTracks: () => [track]})}};
class AudioContext {
    constructor() {this.sampleRate = 24000; this.state = 'running';
        this.audioWorklet = {addModule: async () => {}};}
    async resume() {} async close() {closed++; this.state = 'closed';
        this.onstatechange?.();}
    createMediaStreamSource() {return {connect() {}, disconnect() {}};}
    createGain() {return {gain: {value: 1}, connect() {}, disconnect() {}};}
}
class AudioWorkletNode {
    constructor() {this.port = {postMessage() {}, close() {}};}
    connect() {} disconnect() {}
}
const window = {AudioContext, isSecureContext: true}, CALLER_WORKLET_SOURCE = '';
(async () => {
    const engine = new CallerTestEngine({begin() {}, control() {}}), labels = [];
    engine.terminal = false; engine.epoch = 1; engine.mode = 'speech';
    engine.snapshot = {status: 'ready'}; engine.write = (name, value) => labels.push([name,value]);
    await engine.media(1, '', '');
    const context = engine.context, gain = engine.gain, late = context.onstatechange;
    context.state = STATE; context.onstatechange?.();
    if (!engine.terminal || engine.context || stopped !== 1 || closed !== 1 ||
        gain.gain.value || context.onstatechange ||
        !labels.some(([name,value]) => name === 'error' && value === 'Playback unavailable'))
        throw Error('playback_loss_left_active_media');
    engine.terminal = false; engine.epoch = 2; engine.context = new AudioContext();
    late();
    if (engine.terminal || engine.context.state !== 'running' || stopped !== 1 || closed !== 1)
        throw Error('old_context_changed_replacement');
    engine.finish('stopped', 'complete');
    console.log('playback-state-pass');
})().catch(error => {console.error(error.message); process.exitCode = 1;});
""".replace("STATE", json.dumps(state))
    result = subprocess.run(
        [node],
        input=BUDGET_CODE + ENGINE_CODE + scenario,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "playback-state-pass" and not result.stderr


def test_console_control_repeated_permission_phase_is_safe_without_startup_reset():
    from management_app.state.test_console import ConsoleControl

    control = ConsoleControl()
    control.start("speech")
    control.accept({"status": "permission", "mode": "speech"})
    assert control.snapshot.status == "permission" and control.snapshot.session_id is None
    control.accept({"status": "starting", "mode": "speech"})
    control.accept({"status": "starting", "mode": "speech"})
    assert control.snapshot.status == "starting"
    with pytest.raises(PermissionError):
        control.start("speech")


def test_console_control_explicit_global_owner_clears_previous_community():
    from management_app.state.test_console import ConsoleControl

    control = ConsoleControl()
    control.start("text")
    ready = {
        "status": "ready",
        "mode": "text",
        "sessionId": "fixture-session",
        "revisionId": "fixture-revision",
        "revisionNumber": 1,
        "agentId": "agent-alpha",
        "communityId": "community-alpha",
        "generation": 1,
        "expiresAt": "2026-11-01T12:10:00Z",
        "idleExpiresAt": "2026-11-01T12:02:00Z",
    }
    control.accept(ready)
    control.accept(ready | {"agentId": "agent-routing", "communityId": None, "generation": 2})
    assert control.snapshot.agent_id == "agent-routing" and control.snapshot.community_id is None


def test_console_control_model_requires_explicit_start_and_immutable_published_identity():
    from management_app.state.test_console import ConsoleControl

    control = ConsoleControl()
    assert control.snapshot.status == "idle" and control.snapshot.mode == "text"
    with pytest.raises(PermissionError):
        control.accept({"status": "ready", "mode": "text"})
    control.start("text")
    ready = {
        "status": "ready",
        "mode": "text",
        "sessionId": "test-fixture",
        "revisionId": "published-one",
        "revisionNumber": 1,
        "agentId": "welcome",
        "generation": 1,
        "expiresAt": "2026-10-30T12:10:00Z",
        "idleExpiresAt": "2026-10-30T12:02:00Z",
    }
    control.accept(ready)
    assert control.snapshot.revision_id == "published-one"
    with pytest.raises(PermissionError):
        control.accept(ready | {"revisionId": "draft-two", "revisionNumber": 2})
    control.stop()
    with pytest.raises(PermissionError):
        control.accept(ready)
    control.accept(
        {
            "status": "ended",
            "mode": "text",
            "sessionId": "test-fixture",
            "generation": 1,
            "cleanupStatus": "complete",
            "code": "stopped",
        }
    )
    assert control.snapshot.status == "ended" and control.snapshot.revision_id == "published-one"
    control.start("speech")
    assert control.snapshot.status == "permission" and control.snapshot.session_id is None


@pytest.mark.parametrize(
    "field", ["permit", "messages", "text", "transcript", "audio", "provider_body"]
)
def test_console_control_metadata_never_persists_message_or_secret_fields(field):
    from management_app.state.test_console import ConsoleControl

    control = ConsoleControl()
    control.start("text")
    with pytest.raises(PermissionError):
        control.accept({"status": "starting", "mode": "text", field: "synthetic-private-content"})
    assert "synthetic-private-content" not in control.snapshot.model_dump_json(by_alias=True)


def test_explicit_relay_shutdown_ends_owned_active_sockets_not_only_finished_cleanup(
    active_relay_case,
):
    client, browser, runtime, permit, credentials, sent, displayed, clock, ticks, envelope = (
        active_relay_case
    )
    ready = asyncio.Event()
    original = browser.send_text

    async def display(raw):
        await original(raw)
        if json.loads(raw)["type"] == "ready":
            ready.set()

    browser.send_text = display

    async def pending_input():
        await asyncio.Event().wait()

    browser.receive = pending_input

    async def scenario():
        task = asyncio.create_task(
            client.run(
                browser,
                permit=permit,
                signed_permit="synthetic-signed-permit",
                validate_current=lambda: SimpleNamespace(
                    expires_at=permit.auth_expires_at.timestamp()
                ),
                deadline=asyncio.get_running_loop().time() + 8,
            )
        )
        await asyncio.wait_for(ready.wait(), 1)
        try:
            await client.close()
            assert task.done() and client._active == 0
            runtime.close.assert_awaited()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
