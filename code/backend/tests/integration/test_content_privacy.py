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
        segments = SpeechSegments(
            current=segment,
            instructions_for=lambda current: "synthetic-private-live-prompt",
            emit=emit,
        )
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
