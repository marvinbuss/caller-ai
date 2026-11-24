"""Handover load and failure behavior at the declared capacity (feature 007, T078, SC-005).

Five voice sessions occupy the five slots at once. In each, the first delegation is condensed
first, then handed from Routing to beta's entry agent, which tries a handover it may not make
(refused) and answers; the second delegation arrives before its transcript has caught up and is
handed on to a colleague. Nothing is refused at admission, every answer after a handover starts
within the work budget, each session keeps its one Live attempt, and every slot is released.
"""

import json
import struct
import time
from contextlib import ExitStack

from tests.contract.test_voice_endpoint import admission_case as admission_case
from tests.contract.test_voice_endpoint import authority_slots, wait_for
from tests.contract.test_voice_endpoint import authority_store as authority_store
from tests.contract.test_voice_endpoint import endpoint as endpoint
from tests.contract.test_voice_endpoint import live_case as live_case
from tests.contract.test_voice_endpoint import (
    reviewed_runtime_options as reviewed_runtime_options,
)
from tests.contract.test_voice_endpoint import signed_test_permit as signed_test_permit
from tests.contract.test_voice_endpoint import text_execution_case as text_execution_case
from tests.load.test_greeting_capacity import SLOTS, open_voice_sessions, provider_event, stop
from tests.unit.test_conversation import handoff_to

REPLY = "Synthetic shared streamed reply"
WORK_BUDGET_SECONDS = 30


def route(body):
    """The model double's choice for one request, by agent and by what came last."""
    instructions, items = body["instructions"], body["input"]
    last = items[-1] if items else {}
    if instructions.endswith("Synthetic routing prompt"):
        return handoff_to(body, "Synthetic beta routing"), {}
    if instructions.endswith("Synthetic prompt for b-entry"):
        if last.get("output") == "applied":
            # An argument a colleague handover does not take: refused_graph.
            return handoff_to(body, "b-peer"), {"community_id": "alpha"}
        if last.get("role") == "user":
            return handoff_to(body, "b-peer"), {}
    return None


def transcript(text, start_ms, end_ms, event_id):
    return {
        "type": "session.input_transcript.delta",
        "delta": text,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "event_id": event_id,
    }


def delegation(identifier, offset_ms):
    return {
        "type": "session.delegation.created",
        "delegation": {"id": identifier, "type": "delegation", "target": "client"},
        "offset_ms": offset_ms,
    }


def answered(provider, delegation_id):
    return [
        event["content"]
        for event in provider.sent
        if event["type"] == "session.commentary.append" and event["delegation_id"] == delegation_id
    ]


def test_five_voice_sessions_hand_over_refuse_condense_and_release_their_slots(
    endpoint, monkeypatch
):
    from caller_runtime.services import condensing

    # The first caller turn alone is above the lowered threshold, so each session condenses once.
    monkeypatch.setattr(condensing, "CONDENSE_ABOVE", 2048)
    monkeypatch.setattr(condensing, "CONDENSE_BELOW", 1536)
    monkeypatch.setattr(condensing, "SUMMARY_BYTES", 64)
    endpoint.controls["clarity"] = True
    endpoint.controls["route"] = route
    telemetry = endpoint.client.app.state.telemetry
    with ExitStack() as stack:
        sessions = open_voice_sessions(endpoint, stack)
        endpoint.controls["prepare"] = False
        providers = endpoint.provider_sockets
        assert len(providers) == SLOTS
        for socket, ready in sessions:
            socket.send_bytes(struct.pack(">BII", 1, ready["generation"], 1) + b"\x05\0" * 480)
        for index, provider in enumerate(providers):
            wait_for(
                lambda provider=provider: [
                    e for e in provider.sent if e["type"] == "session.input_audio.append"
                ]
            )
            provider_event(
                endpoint, index, transcript("First request " + "x" * 3000, 0, 40, "event_first")
            )
            provider_event(endpoint, index, delegation(f"delegation-first-{index}", 40))
        for index, provider in enumerate(providers):
            wait_for(
                lambda p=provider, i=index: REPLY in answered(p, f"delegation-first-{i}"),
                timeout=10,
            )
        # The second delegation comes before the rest of its transcript (a slow transcript).
        for index in range(SLOTS):
            provider_event(endpoint, index, transcript("Second", 2000, 2050, "event_second"))
            provider_event(endpoint, index, delegation(f"delegation-second-{index}", 2100))
        time.sleep(0.2)
        for index in range(SLOTS):
            provider_event(endpoint, index, transcript(" request", 2050, 2100, "event_rest"))
        for index, provider in enumerate(providers):
            wait_for(
                lambda p=provider, i=index: REPLY in answered(p, f"delegation-second-{i}"),
                timeout=10,
            )
        # One Live attempt per session, across both agent changes.
        assert all(
            [e["type"] for e in provider.sent].count("session.start") == 1 for provider in providers
        )
        for socket, ready in sessions:
            stop(socket, ready)
    # Every slot is released and its Live attempt settled.
    assert not authority_slots(endpoint)
    events = telemetry.events
    handovers = [e for e in events if e["event"] == "test.handover"]
    applied = [(e["path"]) for e in handovers if e["outcome"] == "success"]
    assert sorted(applied) == sorted(["lifecycle", "routing", "peer"] * SLOTS)
    refused = [e["reason_code"] for e in handovers if e["outcome"] == "failure"]
    assert refused == ["refused_graph"] * SLOTS
    condensed = [e for e in events if e["event"] == "test.history" and e["reason_code"] != "ready"]
    assert [e["reason_code"] for e in condensed] == ["condensed"] * SLOTS
    # SC-005 target: no answer after a handover outside the work budget.
    delays = [float(e["value"]) for e in events if e.get("measurement") == "handover_seconds"]
    assert len(delays) == 2 * SLOTS and all(delay <= WORK_BUDGET_SECONDS for delay in delays)
    assert not [e for e in events if e.get("reason_code") == "refused_budget"]
    assert not [e for e in events if e["event"] == "test.work" and e["outcome"] == "failure"]
    admissions = [e for e in events if e["event"] == "test.admission"]
    assert admissions and all(e["outcome"] == "success" for e in admissions)
    delegations = [e for e in events if e["event"] == "test.delegation"]
    assert len(delegations) == 2 * SLOTS
    assert sorted(e["handed_on"] for e in delegations) == ["true"] * (2 * SLOTS)
    assert "First request" not in json.dumps(events)
