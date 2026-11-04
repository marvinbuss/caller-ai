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
                        "start_ms": 0,
                        "end_ms": 20,
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
