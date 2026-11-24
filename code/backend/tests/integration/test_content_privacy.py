"""Owned content is disposed without entering audit/metadata or default/safe telemetry."""

import asyncio
import json

import pytest

from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store
from tests.unit.test_admission import live_case as live_case
from tests.unit.test_conversation import text_execution_case as text_execution_case

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_text_content_exists_only_in_the_history_container(
    text_execution_case, admission_case, caplog, monkeypatch
):
    """Stored text conversations are the one place conversation text may rest.

    This deliberately narrows the feature 002 guarantee that text content is disposed with the
    session: the history container holds user and assistant text for 30 days after the last turn
    (FR-031), and the text may exist nowhere else: not in logs, metrics, traces, telemetry, error
    bodies, alert payloads, the admission authority record or any other store. Prompts, tokens
    and provider bodies are never stored. Audio, transcripts and provider bodies of voice
    sessions stay volatile (see the speech test below).
    """
    from types import SimpleNamespace

    from caller_runtime.adapters.text_history_repository import TextHistoryRepository
    from caller_runtime.metrics import RuntimeMetrics
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.text_conversation import TextConversationService
    from tests.doubles import ContainerDouble

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case
    writes = []
    replace = container.replace_item

    async def record_metadata(*args, **kwargs):
        writes.append(json.dumps(kwargs["body"]))
        return await replace(*args, **kwargs)

    monkeypatch.setattr(container, "replace_item", record_metadata)

    def production_call_metric(*args, **kwargs):
        raise AssertionError("text_turn_used_production_call_accounting")

    monkeypatch.setattr(RuntimeMetrics, "admission", production_call_metric)
    telemetry = Telemetry("caller-runtime", "local")
    controls["telemetry"] = telemetry
    history = ContainerDouble(partition_field="ownerKey")

    async def start(granted, *, emit):
        return await setup()

    turns = TextConversationService(
        runtime=SimpleNamespace(start=start),
        history=lambda: TextHistoryRepository(
            container=history, ttl_seconds=2_592_000, utc_now=clock.utc_now
        ),
        telemetry=telemetry,
        utc_now=clock.utc_now,
    )
    try:
        with telemetry.span("test.work", {"test.kind": "text", "test.phase": "active"}):
            result = await turns.run(
                permit(1), text="synthetic-private-browser-input", conversation_id=None, emit=emit
            )
        assert result.completed and len(requests) == 1 and requests[0]["store"] is False
        stored = json.dumps(history.records)
        assert "synthetic-private-browser-input" in stored
        assert "Synthetic shared streamed reply" in stored
        elsewhere = (
            json.dumps(telemetry.events)
            + caplog.text
            + "".join(writes)
            + "".join(span.to_json() for span in telemetry.spans)
        )
        for marker in (
            "synthetic-private-browser-input",
            "Synthetic shared streamed reply",
            "Synthetic prompt for b-entry",
            "synthetic-text-token",
        ):
            assert marker not in elsewhere
        for marker in ("Synthetic prompt for b-entry", "synthetic-text-token"):
            assert marker not in stored
        names = {event["event"] for event in telemetry.events}
        assert {"test.session", "test.work", "test.usage", "test.latency", "test.cleanup"} <= names
        assert "history.store" in names
        assert all(event["kind"] == "text" for event in telemetry.events if "kind" in event)

        history.failure = 503
        with pytest.raises(PermissionError, match="^history_unavailable$") as failure:
            await turns.run(
                permit(2, actor="manager-other"),
                text="synthetic-private-second-input",
                conversation_id=result.conversation_id,
                emit=emit,
            )
        assert "synthetic-private" not in repr(failure.value)
        assert "sensitive-store-details" not in repr(failure.value)
        assert "synthetic-private-second-input" not in json.dumps(telemetry.events) + caplog.text
    finally:
        telemetry.close()
        await adapter.close()


async def test_browser_speech_audio_transcripts_and_provider_bodies_remain_volatile(
    text_execution_case, live_case, caplog, monkeypatch
):
    import base64

    from caller_runtime.observability import Telemetry
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, clock, socket, opened = live_case
    telemetry = Telemetry("caller-runtime", "local")
    try:
        original, segment = await opened()
        monkeypatch.setattr(segment.adapter, "_telemetry", telemetry)
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        segments = SpeechSegments(current=segment)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        controller = ConversationExecutor(
            policy=policy,
            text_adapter=adapter,
            utc_now=clock.utc_now,
            speech_segments=segments,
            telemetry=telemetry,
        )
        audio = b"\x07\x03" * 480
        await segment.send_audio(audio)
        for seconds in (2, 1):
            await socket.events.put(
                json.dumps({"type": "session.usage.updated", "usage": {"seconds": seconds}})
            )
            await segment.next_event()
        transcript = {
            "type": "session.input_transcript.delta",
            "delta": "synthetic-private-speech-transcript",
            "start_ms": 0,
            "end_ms": 20,
        }
        await socket.events.put(json.dumps(transcript))
        await controller.accept_transcript(
            await segment.next_event(), generation=1, input_audio_accepted=True
        )
        assert controller.retained_bytes > 0
        await controller.close()
        captured = (
            json.dumps(telemetry.events)
            + caplog.text
            + "".join(span.to_json() for span in telemetry.spans)
            + (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.model_dump_json(by_alias=True)
        )
        for marker in (
            transcript["delta"],
            "synthetic-private-live-prompt",
            "synthetic-live-token",
            base64.b64encode(audio).decode(),
        ):
            assert marker not in captured
        usage = [
            event
            for event in telemetry.events
            if event.get("measurement", "").startswith("voice_seconds_")
        ]
        assert [(event["measurement"], event["value"]) for event in usage] == [
            ("voice_seconds_snapshot", "2.0"),
            ("voice_seconds_snapshot", "2.0"),
            ("voice_seconds_final", "3.0"),
        ]
        assert controller.retained_bytes == 0 and socket.closed == 1
        assert not requests
    finally:
        telemetry.close()
        await adapter.close()


# --- the shared history holds conversation content only (feature 007, SC-004) ---

PRIVATE_SETTINGS = {
    "voiceInstructions": "synthetic-private-voice-instructions",
    "voicePlatformRules": "synthetic-private-voice-rules",
    "voiceGlobalPrompt": "synthetic-private-voice-global",
    "textPlatformRules": "synthetic-private-text-rules",
    "textGlobalPrompt": "synthetic-private-text-global",
}


def private_revision(admission_case, runtime_revision_v2):
    """A newer revision whose every layer, addition and instruction text is a marker."""
    from tests.unit.test_agent_composition import layered

    service, repository, container, clock, permit, cache = admission_case
    document = layered(runtime_revision_v2(2), **PRIVATE_SETTINGS)
    cache.install(document, etag='"private-layers"')
    return document


def configuration_markers(document) -> list[str]:
    """Instructions, layers, additions, tool definitions, credentials and configuration."""
    markers = [*PRIVATE_SETTINGS.values(), "Synthetic voice addition for"]
    markers += [agent["prompt"] for agent in document["agents"]]
    markers += [community["routingDescription"] for community in document["communities"]]
    for tool in document["tools"]:
        markers += [tool["name"], tool["description"], tool["approvedDestinationRef"]]
    markers += [
        document["policyHash"],
        document["graphHash"],
        document["id"],
        "Delegation update.",
        "Delegation scope",
        "Backend tools:",
        "Handover rules:",
        "Progress updates:",
        "Routing can reach",
        "Published-policy goodbye",
        "synthetic-text-token",
        "synthetic-live-token",
    ]
    return markers


def model_inputs(requests) -> str:
    return json.dumps([request["input"] for request in requests if request.get("stream")])


async def test_text_history_and_its_stored_document_hold_no_instructions_or_configuration(
    text_execution_case, admission_case, runtime_revision_v2
):
    from types import SimpleNamespace

    from caller_runtime.adapters.text_history_repository import TextHistoryRepository
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy
    from caller_runtime.services.text_conversation import TextConversationService
    from tests.doubles import ContainerDouble

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case
    document = private_revision(admission_case, runtime_revision_v2)
    await repository.initialize(now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1)
    slot = await service.reserve(permit(1))
    slot = await service.mark_ready(slot.session_id, slot.owner_epoch)
    controller = ConversationExecutor(
        policy=ConversationPolicy(slot=slot, snapshot=cache.pin(), admission=service),
        text_adapter=adapter,
        utc_now=clock.utc_now,
    )
    # Routing's sorted destinations: a-entry, b-entry (handover_1), Goodbye; b-entry's: b-peer
    # (handover_0), Goodbye, Routing.
    controls["wire_actions"] = [
        ("handover_1", {}),
        ("report_progress", {"state": "completed", "message": "Order A-100 is shipped."}),
    ]
    await controller.submit_text("text-1", "My order is A-100.")
    await controller.process_next(emit=emit)
    controls["wire_actions"] = [("handover_0", {})]
    await controller.submit_text("text-2", "Can a colleague check it?")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "b-peer"
    assert {"tool_call", "tool_result", "progress", "handover"} <= {
        entry.kind for entry in controller.history.entries
    }
    history = ContainerDouble(partition_field="ownerKey")
    telemetry = Telemetry("caller-runtime", "local")
    turns = TextConversationService(
        runtime=SimpleNamespace(start=None),
        history=lambda: TextHistoryRepository(
            container=history, ttl_seconds=2_592_000, utc_now=clock.utc_now
        ),
        telemetry=telemetry,
        utc_now=clock.utc_now,
    )
    try:
        await turns._save(controller, permit(1).actor_key, "a" * 32, None)
        (stored,) = history.records.values()
        assert stored["historyVersion"] == 2 and "My order is A-100." in json.dumps(stored)
        everything = (
            json.dumps(stored) + json.dumps(controller.history.render()) + model_inputs(requests)
        )
        for marker in configuration_markers(document):
            assert marker not in everything, marker
    finally:
        telemetry.close()
        await adapter.close()


async def test_voice_history_holds_no_instructions_updates_or_configuration(
    text_execution_case, live_case, admission_case, runtime_revision_v2
):
    from tests.integration.test_voice_delegation_context import delegate, say, voice

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    document = private_revision(admission_case, runtime_revision_v2)
    controller, segment, socket = await voice(
        live_case, text_execution_case, delegation_updates=True
    )
    controls["wire_actions"] = [
        ("handover_1", {}),
        ("report_progress", {"state": "completed", "message": "Order A-100 is shipped."}),
    ]
    await say(controller, "caller", "My order is A-100.", 0, 100)
    await say(controller, "assistant", "Let me check.", 200, 300)
    await say(controller, "caller", "Please check it.", 400, 500)
    await delegate(controller, segment, socket, 500, "Check order A-100.")
    await controller.process_next(emit=emit)
    controls["wire_actions"] = [("handover_0", {})]
    await say(controller, "caller", "Can a colleague check it?", 2000, 2100)
    await delegate(controller, segment, socket, 2100, "Ask a colleague.")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "b-peer"
    # The voice assistant did get delegation updates, and they stay out of the history.
    assert [event for event in socket.sent if event["type"] == "session.instructions.append"]
    everything = (
        json.dumps(controller.history.render())
        + json.dumps([entry.model_dump(by_alias=True) for entry in controller.history.entries])
        + model_inputs(requests)
    )
    assert "[Caller, spoken] My order is A-100." in everything
    for marker in configuration_markers(document):
        assert marker not in everything, marker
    await segment.close()
    await adapter.close()


# --- live mode: no token, key or input value in an error, a log event or a health body ---

PRIVATE_VALUE = "synthetic private value <that> must never appear"


def live_values():
    from tests.integration.test_live_composition import environment

    return environment()


@pytest.mark.parametrize("name", [name for name in live_values() if name != "CALLER_MODE"])
def test_a_bad_live_setting_stops_the_start_with_a_code_and_never_echoes_the_value(
    monkeypatch, name
):
    from caller_runtime.settings import REASON_CODES, Settings, StartupError

    for key in live_values():
        monkeypatch.delenv(key, raising=False)
    for key, value in {**live_values(), name: PRIVATE_VALUE}.items():
        monkeypatch.setenv(key, value)
    try:
        Settings().live()
    except StartupError as error:
        assert error.code in REASON_CODES and str(error) == error.code
        assert PRIVATE_VALUE not in repr(error) and error.__cause__ is None
    else:
        raise AssertionError("a bad value was accepted")


def test_live_health_and_logs_never_hold_a_token_or_a_provider_detail(monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from caller_runtime.live import build_live_runtime
    from caller_runtime.main import create_app
    from caller_runtime.settings import Settings
    from tests.integration.test_live_composition import Cosmos, Credential, environment

    for key in environment():
        monkeypatch.delenv(key, raising=False)
    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    settings = Settings()
    composition = build_live_runtime(settings, credential_factory=Credential, cosmos_factory=Cosmos)
    composition.dependencies._credential.fail = True
    with TestClient(create_app(settings, dependencies=composition.dependencies)) as client:
        texts = [client.get("/healthz").text, client.get("/readyz").text]
        texts.append(
            client.get("/v1/unknown", headers={"Authorization": "Bearer " + PRIVATE_VALUE}).text
        )
    everything = "\n".join([*texts, caplog.text, repr(settings), repr(settings.live())])
    assert "synthetic-private-detail" not in everything and PRIVATE_VALUE not in everything
