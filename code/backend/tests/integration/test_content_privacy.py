"""Owned content is disposed without entering audit/metadata or default/safe telemetry."""

import asyncio
import json

import pytest

from tests.unit.test_conversation import text_execution_case as text_execution_case
from tests.unit.test_test_admission import admission_case as admission_case
from tests.unit.test_test_admission import authority_store as authority_store
from tests.unit.test_test_admission import live_case as live_case

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_browser_text_content_never_enters_authority_or_safe_telemetry(
    text_execution_case, admission_case, caplog, monkeypatch
):
    from caller_runtime.metrics import RuntimeMetrics
    from caller_runtime.observability import Telemetry

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case
    writes = []
    replace = container.replace_item

    async def record_metadata(*args, **kwargs):
        writes.append(json.dumps(kwargs["body"]))
        return await replace(*args, **kwargs)

    monkeypatch.setattr(container, "replace_item", record_metadata)

    def production_call_metric(*args, **kwargs):
        raise AssertionError("browser_test_used_production_call_accounting")

    for name in ("active_calls", "admission", "reservations", "handover", "speech_delay"):
        monkeypatch.setattr(RuntimeMetrics, name, production_call_metric)
    telemetry = Telemetry("caller-runtime", "local")
    controls["telemetry"] = telemetry
    try:
        controller = await setup()
        with telemetry.span("test.work", {"test.mode": "text", "test.phase": "active"}):
            await controller.submit_text("text-privacy", "synthetic-private-browser-input")
            await controller.process_next(emit=emit)
        assert len(requests) == 1 and requests[0]["store"] is False
        assert controller.retained_bytes > 0
        await controller.close()
        assert controller.retained_bytes == 0 and adapter.disposed and writes
        captured = (
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
            assert marker not in captured
        names = {event["event"] for event in telemetry.events}
        assert {"test.session", "test.work", "test.usage", "test.latency", "test.cleanup"} <= names
        assert all(name.startswith("test.") for name in names)
        assert all(event["mode"] == "text" for event in telemetry.events)
    finally:
        telemetry.close()
        await adapter.close()


async def test_browser_speech_audio_transcripts_and_provider_bodies_remain_volatile(
    text_execution_case, live_case, caplog, monkeypatch
):
    import base64

    from caller_runtime.observability import Telemetry
    from caller_runtime.services.conversation import (
        ConversationExecutor,
        ConversationPolicy,
        SpeechSegments,
    )

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


async def test_canceled_terminal_cleanup_still_purges_retained_audio_and_context():
    from caller_runtime.domain.cleanup import terminal_cleanup
    from caller_runtime.domain.session import VolatileCallContext

    raw = bytearray(b"synthetic-private-audio")
    context = VolatileCallContext(
        audio_buffers=[raw],
        conversation=[{"content": "synthetic-private"}],
        request_summary="synthetic-private",
    )
    entered = asyncio.Event()

    async def hangup():
        entered.set()
        await asyncio.Event().wait()
        return False

    async def end(acknowledged):
        pass

    task = asyncio.create_task(
        terminal_cleanup(
            context=context, hangup=hangup, end=end, deadline=asyncio.get_running_loop().time() + 1
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert raw == bytearray(len(raw)) and not context.conversation and not context.audio_buffers
    assert not context.request_summary
