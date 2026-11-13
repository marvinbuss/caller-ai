"""Greeting load and failure behavior at the declared capacity (Constitution VI, quality gate 3).

Five occupied slots is the declared limit (one active test per manager). The Welcome agent's
latency is drawn from a fixed list up to 3 s. Voice: every session's first greeting audio is
measured within 5 s of `ready`. Text: every open request streams its greeting within 5 s. A slow
or failing Welcome agent cancels the voice greeting (nothing is spoken late) and gives the text
fallback; slots, admission and cleanup behave as without greetings.
"""

import base64
import json
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import pytest

from tests.contract.test_voice_endpoint import WORKLOAD, authority_slots, wait_for
from tests.contract.test_voice_endpoint import admission_case as admission_case
from tests.contract.test_voice_endpoint import authority_store as authority_store
from tests.contract.test_voice_endpoint import endpoint as endpoint
from tests.contract.test_voice_endpoint import live_case as live_case
from tests.contract.test_voice_endpoint import (
    reviewed_runtime_options as reviewed_runtime_options,
)
from tests.contract.test_voice_endpoint import signed_test_permit as signed_test_permit
from tests.contract.test_voice_endpoint import text_execution_case as text_execution_case
from tests.doubles import parse_sse

SLOTS = 5
LATENCIES = [0.3, 0.9, 1.5, 2.2, 3.0]
TARGET_SECONDS = 5.0
TEXT_PATH = "https://runtime.fixture.example/v1/conversations/text"


def greeting_events(endpoint, kind):
    return [
        event
        for event in endpoint.client.app.state.telemetry.events
        if event["event"] == "test.greeting" and event["kind"] == kind
    ]


def general_commentary(provider):
    return [
        event
        for event in provider.sent
        if event["type"] == "session.commentary.append" and event["delegation_id"] is None
    ]


def provider_event(endpoint, index, event):
    provider = endpoint.provider_sockets[index]

    async def put():
        await provider.events.put(json.dumps(event))

    endpoint.client.portal.call(put)


def release_model(endpoint):
    async def release():
        endpoint.release.set()

    endpoint.client.portal.call(release)


def stop(socket, ready, sequence=1):
    socket.send_json({"type": "stop", "sequence": sequence, "sessionId": ready["sessionId"]})
    while True:
        event = socket.receive_json()
        if event["type"] == "ended":
            assert event["cleanupStatus"] == "complete"
            return


def open_text(endpoint, number):
    message = endpoint.message(number, mode="text")
    started = time.monotonic()
    response = endpoint.client.post(
        TEXT_PATH,
        headers=WORKLOAD,
        json={"permit": message["permit"], "requestId": message["requestId"]},
    )
    return response, time.monotonic() - started


def open_voice_sessions(endpoint, stack):
    sessions = []
    for number in range(1, SLOTS + 1):
        socket = stack.enter_context(endpoint.connect())
        socket.send_json(endpoint.message(number))
        ready = socket.receive_json()
        assert ready["type"] == "ready", ready
        sessions.append((socket, ready))
    return sessions


def test_five_voice_sessions_hear_their_greeting_within_five_seconds(endpoint):
    endpoint.controls["greeting"] = "Hello and welcome to the service. How can I help you today?"
    endpoint.controls["greeting_delays"] = list(LATENCIES)
    with ExitStack() as stack:
        sessions = open_voice_sessions(endpoint, stack)
        # Every session became ready while greetings were still being written: the greeting
        # call runs outside the admission budget and never delays or refuses admission.
        assert endpoint.controls.get("greeting_in_flight", 0) >= 1
        for index, provider in enumerate(endpoint.provider_sockets):
            wait_for(lambda provider=provider: general_commentary(provider), timeout=6)
            provider_event(
                endpoint,
                index,
                {
                    "type": "session.output_audio.delta",
                    "delta": base64.b64encode(b"\x07\0" * 480).decode(),
                },
            )
        for socket, _ready in sessions:
            while socket.receive().get("bytes") is None:
                pass
        wait_for(lambda: len(greeting_events(endpoint, "voice")) == SLOTS, timeout=6)
        events = greeting_events(endpoint, "voice")
        assert all((e["outcome"], e["reason_code"]) == ("success", "ready") for e in events)
        assert all(0 <= float(e["value"]) <= TARGET_SECONDS for e in events)
        # At most one greeting request per session, never more in flight than sessions.
        assert len(endpoint.controls["greeting_requests"]) == SLOTS
        assert endpoint.controls["greeting_peak"] <= SLOTS
        for socket, ready in sessions:
            stop(socket, ready)
    assert endpoint.controls["greeting_in_flight"] == 0
    assert not authority_slots(endpoint)


def test_five_text_conversations_stream_their_greeting_within_five_seconds(endpoint):
    endpoint.controls["greeting"] = "Hello and welcome to the service. How can I help you today?"
    endpoint.controls["greeting_delays"] = list(LATENCIES)
    with ThreadPoolExecutor(SLOTS) as pool:
        results = list(pool.map(lambda number: open_text(endpoint, number), range(1, SLOTS + 1)))
    for response, elapsed in results:
        assert response.status_code == 200
        events = parse_sse(response.text)
        assert [kind for kind, _ in events] == ["conversation", "text.delta", "text.completed"]
        assert events[1][1]["text"] == endpoint.controls["greeting"]
        assert elapsed <= TARGET_SECONDS
    events = greeting_events(endpoint, "text")
    assert len(events) == SLOTS
    assert all((e["outcome"], e["reason_code"]) == ("success", "ready") for e in events)
    assert len(endpoint.controls["greeting_requests"]) == SLOTS
    assert endpoint.controls["greeting_in_flight"] == 0
    assert not authority_slots(endpoint)


def test_slow_welcome_agent_cancels_every_voice_greeting_and_keeps_sessions_listening(
    endpoint, monkeypatch
):
    from caller_runtime.api import voice

    monkeypatch.setattr(voice, "VOICE_GREETING_SECONDS", 0.3)
    endpoint.controls["greeting"], endpoint.controls["greeting_block"] = "Too late", True
    with ExitStack() as stack:
        sessions = open_voice_sessions(endpoint, stack)
        wait_for(lambda: len(greeting_events(endpoint, "voice")) == SLOTS, timeout=6)
        events = greeting_events(endpoint, "voice")
        assert all((e["outcome"], e["reason_code"]) == ("failure", "deadline") for e in events)
        release_model(endpoint)
        time.sleep(0.2)
        # Nothing is spoken late, and Live silence never blocks the session.
        assert not any(general_commentary(provider) for provider in endpoint.provider_sockets)
        for socket, ready in sessions:
            socket.send_json(
                {"type": "ping", "sequence": 1, "sessionId": ready["sessionId"], "nonce": "alive"}
            )
            while (event := socket.receive_json())["type"] != "pong":
                assert event["type"] in {"audio.credit", "transcript.delta"}
            assert event["nonce"] == "alive"
            socket.send_bytes(struct.pack(">BII", 1, ready["generation"], 1) + b"\x05\0" * 480)
        for socket, ready in sessions:
            stop(socket, ready, 2)
    assert len(endpoint.controls["greeting_requests"]) == SLOTS
    assert not authority_slots(endpoint)


@pytest.mark.parametrize("failure", ["slow", "failing"])
def test_slow_or_failing_text_model_streams_the_fallback_within_five_seconds(
    endpoint, monkeypatch, failure
):
    from caller_runtime.services import conversation
    from caller_runtime.services.greeting import TEXT_FALLBACK_GREETING

    if failure == "slow":
        monkeypatch.setattr(conversation, "TEXT_GREETING_SECONDS", 0.3)
        endpoint.controls["greeting"], endpoint.controls["greeting_block"] = "Too late", True
    else:
        endpoint.controls["greeting"], endpoint.controls["greeting_status"] = "unused", 429
    with ThreadPoolExecutor(SLOTS) as pool:
        results = list(pool.map(lambda number: open_text(endpoint, number), range(1, SLOTS + 1)))
    for response, elapsed in results:
        events = parse_sse(response.text)
        assert events[1][1]["text"] == TEXT_FALLBACK_GREETING
        assert elapsed <= TARGET_SECONDS
    assert len(endpoint.controls["greeting_requests"]) == SLOTS
    release_model(endpoint)
    deadline = time.monotonic() + 5
    while authority_slots(endpoint) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not authority_slots(endpoint)
