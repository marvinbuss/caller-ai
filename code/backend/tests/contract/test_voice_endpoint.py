"""Contract of `WS /v1/conversations/voice`: speech only, authenticated by the first message.

The tests run the composed runtime (permit verification, admission, the live adapter and the
model runner) against controlled doubles; nothing reaches a network or a cloud service.
"""

import asyncio
import base64
import json
import struct
import time
from contextlib import ExitStack
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.integration.test_conversation import reviewed_runtime_options as reviewed_runtime_options
from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store
from tests.unit.test_admission import live_case as live_case
from tests.unit.test_admission import signed_test_permit as signed_test_permit
from tests.unit.test_conversation import text_execution_case as text_execution_case

URL = "wss://runtime.fixture.example/v1/conversations/voice"
SUBPROTOCOL = "caller.voice.v1"
WORKLOAD = {"Authorization": "Bearer synthetic-management-workload"}


@pytest.fixture
def endpoint(
    reviewed_runtime_options,
    signed_test_permit,
    admission_case,
    text_execution_case,
    live_case,
    runtime_revision_v2,
):
    import httpx

    from caller_runtime.composition import ConversationRuntime, RuntimeOptions
    from caller_runtime.main import create_app

    public, sign, clock, assertion = signed_test_permit
    service, repository, container, controlled_clock, permit, cache = admission_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    live_service, live_repository, live_clock, template, opened = live_case
    controls["prepare"] = True
    provider_sockets = []

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(token="synthetic-voice-token", expires_on=time.time() + 900)

        async def close(self):
            pass

    class Identity:
        async def require_runtime(self, request):
            if request.headers.get("authorization") != WORKLOAD["Authorization"]:
                raise HTTPException(401, "synthetic-private-auth")
            return "management-workload-fixture"

    async def configuration(request):
        return httpx.Response(
            200, json=runtime_revision_v2(), headers={"etag": '"current-fixture"'}
        )

    async def connect(url, **options):
        fresh = type(template)()
        provider_sockets.append(fresh)
        return fresh

    async def containers(name):
        return container

    options = RuntimeOptions(
        **(
            reviewed_runtime_options
            | {"manager_issuer": assertion.issuer, "manager_audience": assertion.audience}
        )
    )
    runtime = ConversationRuntime(
        options=options,
        owner_id="runtime-fixture-owner",
        container_factory=containers,
        credential_factory=AsyncMock(return_value=Credentials()),
        workload_identity=Identity(),
        verification_keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        configuration_transport=httpx.MockTransport(configuration),
        text_transport=adapter._transport,
        live_socket_factory=connect,
    )

    async def initialize():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )

    asyncio.run(initialize())

    def authenticate_message(number, actor=None, mode="speech", **fields):
        changes = {
            "actorId": actor or f"manager-{number}",
            "requestId": f"request-{number}",
            "nonce": f"{number:064x}",
            "managementSessionDigest": f"{number:064x}",
            "mode": mode,
        }
        return {
            "type": "authenticate",
            "permit": sign(changes),
            "requestId": f"request-{number}",
        } | fields

    with TestClient(create_app(runtime=runtime)) as client:

        def connect_socket(subprotocols=(SUBPROTOCOL,), headers=WORKLOAD):
            return client.websocket_connect(
                URL, subprotocols=list(subprotocols), headers=dict(headers)
            )

        yield SimpleNamespace(
            client=client,
            connect=connect_socket,
            message=authenticate_message,
            provider_sockets=provider_sockets,
            requests=requests,
            runtime=runtime,
            repository=repository,
            controls=controls,
            release=release,
        )


def refusal(socket):
    """Read until the connection closes and return its `error` messages and close code."""
    errors = []
    with pytest.raises(WebSocketDisconnect) as failure:
        while True:
            event = socket.receive_json()
            if event["type"] == "error":
                errors.append(event)
            else:
                assert event["type"] == "audio.credit"
    return errors, failure.value.code


def next_event(socket, kind):
    while True:
        event = socket.receive_json()
        if event["type"] == kind:
            return event
        assert event["type"] in {"audio.credit", "transcript.delta", "generation.changed"}


def authority_slots(endpoint):
    async def read():
        return (
            await endpoint.repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots

    return asyncio.run(read())


@pytest.mark.parametrize(
    "subprotocols", [(), ("caller.test.v1",), ("other.v1",), (SUBPROTOCOL, SUBPROTOCOL)]
)
def test_the_voice_subprotocol_is_required_exactly_once(endpoint, subprotocols):
    with pytest.raises(WebSocketDisconnect):
        with endpoint.connect(subprotocols):
            pytest.fail("voice_socket_accepted_without_the_voice_subprotocol")
    assert not endpoint.provider_sockets and not authority_slots(endpoint)


def test_missing_workload_credential_is_refused_before_the_upgrade(endpoint):
    with pytest.raises(WebSocketDisconnect) as failure:
        with endpoint.connect(headers={}):
            pytest.fail("unverified_workload_upgraded")
    assert failure.value.code == 4401 and not authority_slots(endpoint)


def test_first_message_must_authenticate_and_is_bounded(endpoint):
    with endpoint.connect() as socket:
        socket.send_json({"type": "stop", "sequence": 1, "sessionId": "not-yet"})
        errors, code = refusal(socket)
    assert code == 4400 and errors == [
        {"type": "error", "code": "invalid_request", "reason": "invalid_message"}
    ]
    with endpoint.connect() as socket:
        socket.send_text("x" * 8193)
        errors, code = refusal(socket)
    assert code == 4413 and errors[0]["reason"] == "control_limit"
    assert not endpoint.provider_sockets and not authority_slots(endpoint)


def test_invalid_and_replayed_permits_are_unauthorized(endpoint):
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1) | {"permit": "not-a-signed-permit"})
        errors, code = refusal(socket)
    assert code == 4401 and errors[0]["reason"] == "permit_invalid"
    first = endpoint.message(2)
    with endpoint.connect() as socket:
        socket.send_json(first)
        ready = socket.receive_json()
        assert ready["type"] == "ready"
        socket.send_json({"type": "stop", "sequence": 1, "sessionId": ready["sessionId"]})
        next_event(socket, "ended")
    with endpoint.connect() as socket:
        socket.send_json(first)
        errors, code = refusal(socket)
    assert code == 4401 and errors[0]["reason"] == "permit_replayed"


def test_text_permit_is_forbidden_on_the_voice_endpoint(endpoint):
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1, mode="text"))
        errors, code = refusal(socket)
    assert code == 4403 and errors == [
        {"type": "error", "code": "forbidden", "reason": "mode_mismatch"}
    ]
    assert not endpoint.provider_sockets and not endpoint.requests


def test_nonempty_settings_are_not_supported_and_empty_settings_are_accepted(endpoint):
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1, settings={"voice": "other"}))
        errors, code = refusal(socket)
    assert code == 4422 and errors == [{"type": "error", "code": "settings_not_supported"}]
    assert not endpoint.provider_sockets and not authority_slots(endpoint)
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(2, settings={}))
        ready = socket.receive_json()
        assert ready["type"] == "ready" and ready["mode"] == "speech"
        socket.send_json({"type": "stop", "sequence": 1, "sessionId": ready["sessionId"]})
        next_event(socket, "ended")


def test_audio_interruption_and_stop_release_the_slot_only_after_closure_is_confirmed(endpoint):
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        assert ready["type"] == "ready" and ready["revisionNumber"] == 1
        assert ready["audioFormat"] == {"codec": "pcm16le", "sampleRate": 24000, "channels": 1}
        credit = next_event(socket, "audio.credit")
        assert 0 < credit["availableSamples"] <= 6000
        provider = endpoint.provider_sockets[0]
        pcm = b"\x05\0" * 480
        socket.send_bytes(struct.pack(">BII", 1, ready["generation"], 1) + pcm)
        socket.send_json(
            {"type": "input.mute", "sequence": 1, "sessionId": ready["sessionId"], "muted": True}
        )
        assert next_event(socket, "command.ack")["acceptedSequence"] == 1
        appended = [e for e in provider.sent if e["type"] == "session.input_audio.append"]
        assert len(appended) == 1 and base64.b64decode(appended[0]["audio"]) == pcm
        assert len(authority_slots(endpoint)) == 1

        async def provider_audio():
            await provider.events.put(
                json.dumps(
                    {
                        "type": "session.output_audio.delta",
                        "delta": base64.b64encode(b"\x07\0" * 480).decode(),
                    }
                )
            )

        endpoint.client.portal.call(provider_audio)
        while True:
            frame = socket.receive()
            if frame.get("bytes") is not None:
                break
        assert struct.unpack(">BII", frame["bytes"][:9]) == (1, ready["generation"], 1)
        socket.send_json({"type": "stop", "sequence": 2, "sessionId": ready["sessionId"]})
        ended = next_event(socket, "ended")
        assert ended["code"] == "stopped" and ended["cleanupStatus"] == "complete"
        assert not authority_slots(endpoint)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1000
    assert provider.closed == 1 and not endpoint.requests[1:]


def test_saturated_slots_refuse_the_next_session_without_disturbing_active_ones(endpoint):
    with ExitStack() as stack:
        sessions = []
        for number in range(1, 6):
            socket = stack.enter_context(endpoint.connect())
            socket.send_json(endpoint.message(number))
            ready = socket.receive_json()
            assert ready["type"] == "ready"
            sessions.append((socket, ready))
        assert len(authority_slots(endpoint)) == 5
        sixth = stack.enter_context(endpoint.connect())
        sixth.send_json(endpoint.message(6))
        errors, code = refusal(sixth)
        assert code == 4503 and errors == [
            {"type": "error", "code": "capacity_unavailable", "reason": "capacity_unavailable"}
        ]
        assert len(authority_slots(endpoint)) == 5
        for socket, ready in sessions:
            socket.send_json(
                {"type": "ping", "sequence": 1, "sessionId": ready["sessionId"], "nonce": "alive"}
            )
            assert next_event(socket, "pong")["nonce"] == "alive"
        for socket, ready in sessions:
            socket.send_json({"type": "stop", "sequence": 2, "sessionId": ready["sessionId"]})
            assert next_event(socket, "ended")["cleanupStatus"] == "complete"
    assert not authority_slots(endpoint)


def provider_greeting(endpoint, deltas):
    """Queue Live output the way the int endpoint sent it: 100-ms deltas, faster than real time."""
    provider = endpoint.provider_sockets[0]

    async def burst():
        for _ in range(deltas):
            await provider.events.put(
                json.dumps(
                    {
                        "type": "session.output_audio.delta",
                        "delta": base64.b64encode(b"\x07\0" * 2400).decode(),
                    }
                )
            )

    endpoint.client.portal.call(burst)


@pytest.mark.parametrize("steps", range(8))
def test_handler_teardown_keeps_a_server_cancel_that_lands_during_it(endpoint, steps):
    import anyio

    ready, scopes = asyncio.Event(), []
    inbound = [
        {"type": "websocket.connect"},
        {"type": "websocket.receive", "text": json.dumps(endpoint.message(1))},
    ]

    async def receive():
        if inbound:
            return inbound.pop(0)
        await ready.wait()
        # The server cancel lands `steps` loop steps after the client leaves.
        loop = asyncio.get_running_loop()
        cancel = scopes[0].cancel
        for _ in range(steps):
            cancel = partial(loop.call_soon, cancel)
        loop.call_soon(cancel)
        return {"type": "websocket.disconnect", "code": 1001}

    async def send(message):
        if message["type"] == "websocket.send" and '"ready"' in (message.get("text") or ""):
            ready.set()

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "wss",
        "server": ("runtime.fixture.example", 443),
        "path": "/v1/conversations/voice",
        "raw_path": b"/v1/conversations/voice",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"runtime.fixture.example"),
            (b"authorization", WORKLOAD["Authorization"].encode()),
            (b"sec-websocket-protocol", SUBPROTOCOL.encode()),
        ],
        "subprotocols": [SUBPROTOCOL],
        "state": {},
    }

    async def session():
        # A bare CancelledError from teardown escapes the scope and ends the server's task group.
        with anyio.CancelScope() as cancel_scope:
            scopes.append(cancel_scope)
            await endpoint.client.app(scope, receive, send)
            await asyncio.Event().wait()
        return cancel_scope.cancelled_caught

    assert endpoint.client.portal.call(session)


def test_a_greeting_burst_waits_for_played_acknowledgements_instead_of_ending(endpoint):
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        provider_greeting(endpoint, 10)
        received, command = 0, 0
        while received < 50:
            frame = socket.receive()
            if frame.get("bytes") is None:
                assert json.loads(frame["text"])["type"] == "audio.credit", frame
                continue
            _protocol, generation, sequence = struct.unpack(">BII", frame["bytes"][:9])
            assert sequence == received + 1 and len(frame["bytes"]) == 969
            received = sequence
            command += 1
            socket.send_json(
                {
                    "type": "audio.played",
                    "sequence": command,
                    "sessionId": ready["sessionId"],
                    "generation": generation,
                    "audioSequence": sequence,
                }
            )
        socket.send_json({"type": "stop", "sequence": command + 1, "sessionId": ready["sessionId"]})
        ended = next_event(socket, "ended")
        assert ended["code"] == "stopped" and ended["cleanupStatus"] == "complete"


def test_playback_that_is_never_acknowledged_still_ends_as_credit_exhausted(endpoint, monkeypatch):
    from caller_runtime.api import voice

    monkeypatch.setattr(voice, "PLAYBACK_WAIT_SECONDS", 0.2)
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        assert socket.receive_json()["type"] == "ready"
        provider_greeting(endpoint, 10)
        frames, errors = 0, []
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                frame = socket.receive()
                if frame.get("bytes") is not None:
                    frames += 1
                elif frame.get("text") is not None:
                    event = json.loads(frame["text"])
                    if event["type"] == "error":
                        errors.append(event)
                else:
                    raise WebSocketDisconnect(frame.get("code", 1000))
    assert frames == 25 and closed.value.code == 4413
    assert errors == [
        {"type": "error", "code": "limit_exceeded", "reason": "audio_credit_exhausted"}
    ]


def test_live_events_behind_queued_speech_are_read_before_that_speech_plays(endpoint):
    # A long Live answer arrives within seconds; user speech behind it must not wait for playback.
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        assert socket.receive_json()["type"] == "ready"
        provider_greeting(endpoint, 10)
        provider = endpoint.provider_sockets[0]

        async def speech():
            await provider.events.put(
                json.dumps(
                    {
                        "type": "session.input_transcript.delta",
                        "delta": "Synthetic caller words",
                        "start_ms": 0,
                        "end_ms": 20,
                        "event_id": "event_input",
                    }
                )
            )

        endpoint.client.portal.call(speech)
        frames = 0
        while True:
            frame = socket.receive()
            if frame.get("bytes") is not None:
                frames += 1
                continue
            event = json.loads(frame["text"])
            if event["type"] == "transcript.delta":
                break
            assert event["type"] == "audio.credit", event
        # Nothing was played back, so at most the 0.5-s window of the queued speech was sent.
        assert event["speaker"] == "user" and event["fragment"] == "Synthetic caller words"
        assert frames <= 25


def test_user_speech_renews_idle_although_capture_forwarded_less_audio_than_live_counted(endpoint):
    # Live's transcript clock ran seconds past the forwarded audio once browser capture had gaps.
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        assert ready["type"] == "ready"
        socket.send_bytes(struct.pack(">BII", 1, ready["generation"], 1) + b"\x05\0" * 480)
        provider = endpoint.provider_sockets[0]
        while not [e for e in provider.sent if e["type"] == "session.input_audio.append"]:
            next_event(socket, "audio.credit")
        assert authority_slots(endpoint)[0].last_user_at is None

        async def speech():
            await provider.events.put(
                json.dumps(
                    {
                        "type": "session.input_transcript.delta",
                        "delta": "Synthetic caller words",
                        "start_ms": 3800,
                        "end_ms": 4000,
                        "event_id": "event_input",
                    }
                )
            )

        endpoint.client.portal.call(speech)
        assert next_event(socket, "transcript.delta")["speaker"] == "user"
        assert authority_slots(endpoint)[0].last_user_at is not None
        socket.send_json({"type": "stop", "sequence": 1, "sessionId": ready["sessionId"]})
        assert next_event(socket, "ended")["cleanupStatus"] == "complete"


def test_a_second_session_for_the_same_manager_is_capacity_unavailable(endpoint):
    with endpoint.connect() as first:
        first.send_json(endpoint.message(1, actor="manager-same"))
        ready = first.receive_json()
        assert ready["type"] == "ready"
        with endpoint.connect() as second:
            second.send_json(endpoint.message(2, actor="manager-same"))
            errors, code = refusal(second)
        assert code == 4503 and errors[0]["reason"] == "manager_busy"
        first.send_json({"type": "stop", "sequence": 1, "sessionId": ready["sessionId"]})
        assert next_event(first, "ended")["cleanupStatus"] == "complete"


@pytest.mark.parametrize(
    "fault,close_code,code,reason",
    [
        ("unknown-control", 4400, "invalid_request", "invalid_message"),
        ("oversized-control", 4413, "limit_exceeded", "control_limit"),
        ("odd-audio", 4400, "invalid_request", "invalid_audio"),
        ("oversized-audio", 4413, "limit_exceeded", "frame_limit"),
    ],
)
def test_each_refusal_sends_one_error_message_then_closes_with_its_code(
    endpoint, fault, close_code, code, reason
):
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        if fault == "unknown-control":
            socket.send_json({"type": "provider.override", "sequence": 1, "sessionId": "x"})
        elif fault == "oversized-control":
            socket.send_text("x" * 8193)
        else:
            size = 1922 if fault == "oversized-audio" else 959
            socket.send_bytes(struct.pack(">BII", 1, ready["generation"], 1) + b"\0" * size)
        errors, closed = refusal(socket)
    assert closed == close_code
    assert errors == [{"type": "error", "code": code, "reason": reason}]
    deadline = time.monotonic() + 5
    while authority_slots(endpoint) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert endpoint.provider_sockets[0].closed == 1


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition_not_reached"
        time.sleep(0.01)


def general_commentary(provider):
    return [
        event
        for event in provider.sent
        if event["type"] == "session.commentary.append" and event["delegation_id"] is None
    ]


def greeting_events(endpoint):
    return [
        event
        for event in endpoint.client.app.state.telemetry.events
        if event["event"] == "test.greeting"
    ]


def provider_event(endpoint, event):
    provider = endpoint.provider_sockets[0]

    async def put():
        await provider.events.put(json.dumps(event))

    endpoint.client.portal.call(put)


def release_model(endpoint):
    async def release():
        endpoint.release.set()

    endpoint.client.portal.call(release)


def stop(socket, ready, sequence=1):
    socket.send_json({"type": "stop", "sequence": sequence, "sessionId": ready["sessionId"]})
    assert next_event(socket, "ended")["cleanupStatus"] == "complete"


@pytest.mark.parametrize("long", [False, True])
def test_voice_session_greets_after_ready_through_general_commentary(endpoint, long):
    from caller_runtime.services.greeting import VOICE_GREETING_INPUT

    greeting = (
        "This greeting sentence introduces the service in a friendly way. " * 20
        if long
        else "Hello and welcome to the service. How can I help you today?"
    ).strip()
    endpoint.controls["greeting"] = greeting
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        assert ready["type"] == "ready"
        provider = endpoint.provider_sockets[0]
        # The start event and its validator are unchanged: no greeting rides in session.input.
        assert "input" not in provider.start["session"]
        wait_for(lambda: general_commentary(provider))
        if long:
            wait_for(
                lambda: "".join(e["content"] for e in general_commentary(provider)) == greeting
            )
        parts = general_commentary(provider)
        assert "".join(part["content"] for part in parts) == greeting
        assert len(parts) == (3 if long else 1)
        assert all(len(part["content"].encode("utf-8")) <= 500 for part in parts)
        asked = [
            r
            for r in endpoint.controls["greeting_requests"]
            if r.get("input") == VOICE_GREETING_INPUT
        ]
        assert len(asked) == 1 and not asked[0].get("tools")
        assert not greeting_events(endpoint)
        provider_event(
            endpoint,
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(b"\x07\0" * 480).decode(),
            },
        )
        while socket.receive().get("bytes") is None:
            pass
        events = greeting_events(endpoint)
        assert [(e["outcome"], e["reason_code"], e["kind"]) for e in events] == [
            ("success", "ready", "voice")
        ]
        assert events[0]["measurement"] == "greeting_seconds"
        assert 0 <= float(events[0]["value"]) <= 5
        assert greeting not in json.dumps(events)
        stop(socket, ready)
    assert len(greeting_events(endpoint)) == 1


def test_a_greeting_without_text_by_the_deadline_is_cancelled_and_never_spoken(
    endpoint, monkeypatch
):
    from caller_runtime.api import voice

    monkeypatch.setattr(voice, "VOICE_GREETING_SECONDS", 0.3)
    endpoint.controls["greeting"], endpoint.controls["greeting_block"] = "Too late", True
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        provider = endpoint.provider_sockets[0]
        wait_for(lambda: greeting_events(endpoint))
        events = greeting_events(endpoint)
        assert [(e["outcome"], e["reason_code"], e["value"]) for e in events] == [
            ("failure", "deadline", "0.3")
        ]
        release_model(endpoint)
        time.sleep(0.2)
        assert not general_commentary(provider)
        socket.send_json(
            {"type": "ping", "sequence": 1, "sessionId": ready["sessionId"], "nonce": "alive"}
        )
        assert next_event(socket, "pong")["nonce"] == "alive"
        stop(socket, ready, 2)
    assert len(greeting_events(endpoint)) == 1


def test_a_failing_greeting_request_logs_deadline_without_a_fallback(endpoint):
    # A 4xx answer is a definite provider outcome, so the attempt settles and cleanup completes.
    endpoint.controls["greeting"], endpoint.controls["greeting_status"] = "unused", 429
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        provider = endpoint.provider_sockets[0]
        wait_for(lambda: greeting_events(endpoint))
        events = greeting_events(endpoint)
        assert [(e["outcome"], e["reason_code"], e["value"]) for e in events] == [
            ("failure", "deadline", "5.0")
        ]
        assert not general_commentary(provider)
        stop(socket, ready)


def test_live_gets_voice_instructions_a_context_note_and_a_quiet_handover(
    endpoint, runtime_revision_v2
):
    from caller_runtime.services.modality_defaults import VOICE_INSTRUCTIONS
    from caller_runtime.services.speech_parts import QUIET_HANDOVER

    revision = runtime_revision_v2()
    welcome = next(agent for agent in revision["agents"] if agent["role"] == "welcome")
    routable = [
        community
        for community in revision["communities"]
        if community["enabled"] and community.get("routable", True)
    ]
    endpoint.controls["greeting"], endpoint.controls["greeting_block"] = "Unused", True
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        provider = endpoint.provider_sockets[0]
        instructions = provider.start["session"]["instructions"]
        assert instructions.startswith(VOICE_INSTRUCTIONS + "\nDelegate reasoning")
        assert len(instructions.encode("utf-8")) <= 16 * 1024
        prompts = [agent["prompt"] for agent in revision["agents"]]
        assert not any(prompt in instructions for prompt in prompts)
        notes = [
            event
            for event in provider.sent
            if event["type"] == "session.thinking.append" and event["delegation_id"] is None
        ]
        note = "".join(event["content"] for event in notes)
        assert notes and all(len(event["content"].encode("utf-8")) <= 500 for event in notes)
        assert len(note.encode("utf-8")) <= 2000
        assert note.startswith(f"{welcome['name']} (welcome)")
        assert "Communities you can route to:" in note
        assert all(community["name"] + ": " in note for community in routable)
        assert not any(prompt in note for prompt in prompts)
        provider_event(
            endpoint,
            {
                "type": "session.delegation.created",
                "delegation": {"id": "delegation-quiet", "type": "delegation", "target": "client"},
                "offset_ms": 10,
            },
        )

        def quiet():
            return [
                event
                for event in provider.sent
                if event["type"] == "session.thinking.append"
                and event["delegation_id"] == "delegation-quiet"
            ]

        wait_for(quiet)
        assert [event["content"] for event in quiet()] == [QUIET_HANDOVER]
        release_model(endpoint)
        stop(socket, ready)


def test_progress_is_spoken_with_the_delegation_and_sent_to_the_browser(endpoint):
    # Without a greeting text the greeting request fails at once and holds no model slot.
    endpoint.controls["clarity"] = True
    endpoint.controls["wire_actions"] = [
        ("report_progress", {"state": "still_working", "message": "Checking that for you."})
    ]
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        # Readiness is done; later non-streamed requests are the clarity check.
        endpoint.controls["prepare"] = False
        provider = endpoint.provider_sockets[0]
        socket.send_bytes(struct.pack(">BII", 1, ready["generation"], 1) + b"\x05\0" * 480)
        wait_for(lambda: [e for e in provider.sent if e["type"] == "session.input_audio.append"])
        provider_event(
            endpoint,
            {
                "type": "session.input_transcript.delta",
                "delta": "Synthetic caller request",
                "start_ms": 0,
                "end_ms": 20,
                "event_id": "event_input",
            },
        )
        provider_event(
            endpoint,
            {
                "type": "session.delegation.created",
                "delegation": {
                    "id": "delegation-progress",
                    "type": "delegation",
                    "target": "client",
                },
                "offset_ms": 40,
            },
        )
        progress = next_event_of(socket, "progress")
        assert set(progress) == {"type", "sequence", "generation", "sessionId", "state", "text"}
        assert (progress["state"], progress["text"]) == ("still_working", "Checking that for you.")

        def spoken():
            return [
                e["content"]
                for e in provider.sent
                if e["type"] == "session.commentary.append"
                and e["delegation_id"] == "delegation-progress"
            ]

        wait_for(lambda: len(spoken()) == 2)
        assert spoken()[0] == "Checking that for you."
        assert all(len(part.encode("utf-8")) <= 500 for part in spoken())
        release_model(endpoint)
        stop(socket, ready)


def next_event_of(socket, kind):
    """Read control events until one of `kind`, skipping audio frames and other controls."""
    while True:
        message = socket.receive()
        if message.get("text") is None:
            continue
        event = json.loads(message["text"])
        if event["type"] == kind:
            return event
        assert event["type"] not in {"error", "ended"}, event


@pytest.mark.parametrize("first", ["speech", "delegation"])
def test_caller_speech_or_a_delegation_first_skips_the_greeting(endpoint, first):
    endpoint.controls["greeting"], endpoint.controls["greeting_block"] = "Skipped", True
    with endpoint.connect() as socket:
        socket.send_json(endpoint.message(1))
        ready = socket.receive_json()
        provider = endpoint.provider_sockets[0]
        wait_for(lambda: endpoint.controls.get("greeting_requests"))
        if first == "speech":
            socket.send_bytes(struct.pack(">BII", 1, ready["generation"], 1) + b"\x05\0" * 480)
            wait_for(
                lambda: [e for e in provider.sent if e["type"] == "session.input_audio.append"]
            )
            provider_event(
                endpoint,
                {
                    "type": "session.input_transcript.delta",
                    "delta": "Synthetic caller words",
                    "start_ms": 0,
                    "end_ms": 20,
                    "event_id": "event_input",
                },
            )
        else:
            provider_event(
                endpoint,
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-first",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 10,
                },
            )
        wait_for(lambda: greeting_events(endpoint))
        assert [(e["outcome"], e["reason_code"]) for e in greeting_events(endpoint)] == [
            ("success", "skipped")
        ]
        assert "measurement" not in greeting_events(endpoint)[0]
        release_model(endpoint)
        time.sleep(0.2)
        assert not general_commentary(provider)
        stop(socket, ready)
    assert len(greeting_events(endpoint)) == 1
