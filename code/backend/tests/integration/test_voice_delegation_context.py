"""Voice work items see the whole spoken conversation (feature 007, US2: FR-003, FR-024, A4)."""

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


async def voice(live_case, text_execution_case, **options):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    original, segment = await opened()
    slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
    policy = ConversationPolicy(slot=slot, snapshot=service.configuration.pin(), admission=service)
    controller = ConversationExecutor(
        policy=policy,
        text_adapter=adapter,
        utc_now=clock.utc_now,
        speech_segments=SpeechSegments(current=segment),
        **options,
    )
    return controller, segment, socket


async def say(controller, speaker, text, start_ms, end_ms):
    await controller.accept_transcript(
        {
            "type": "session.input_transcript.delta"
            if speaker == "caller"
            else "session.output_transcript.delta",
            "delta": text,
            "start_ms": start_ms,
            "end_ms": end_ms,
        },
        generation=controller.policy.slot.generation,
        input_audio_accepted=False,
    )


async def delegate(controller, segment, socket, offset_ms, task, **flags):
    await socket.events.put(
        json.dumps(
            {
                "type": "session.delegation.created",
                "delegation": {
                    "id": f"delegation-{int(offset_ms)}",
                    "type": "delegation",
                    "target": "client",
                },
                "offset_ms": offset_ms,
            }
        )
    )
    event = await segment.next_event()
    return await controller.submit_delegation(
        segment,
        event,
        task_text=task,
        unambiguous=flags.get("clear", True),
        **flags.get("extra", {}),
    )


def model_inputs(requests):
    return [request for request in requests if request.get("stream")]


async def test_transcripts_are_recorded_per_turn_and_a_fact_said_earlier_reaches_the_agent(
    live_case, text_execution_case
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controller, segment, socket = await voice(live_case, text_execution_case)
    # A4: the caller's name is said to the voice assistant only, without any delegation.
    await say(controller, "caller", "My name ", 0, 100)
    await say(controller, "caller", "is Ada.", 100, 200)
    await say(controller, "assistant", "Nice to ", 300, 400)
    await say(controller, "assistant", "meet you.", 400, 500)
    await say(controller, "caller", "What is my balance?", 600, 710)
    await delegate(controller, segment, socket, 710, "What is my balance?")
    await controller.process_next(emit=emit)
    sent = model_inputs(requests)[-1]["input"]
    assert sent[:3] == [
        {"role": "user", "content": "[Caller, spoken] My name is Ada."},
        {"role": "assistant", "content": "[Voice assistant, spoken] Nice to meet you."},
        {"role": "user", "content": "[Caller, spoken] What is my balance?"},
    ]
    kinds = [(entry.kind, entry.at_ms) for entry in controller.history.entries]
    assert kinds == [
        ("caller_speech", 0.0),
        ("assistant_speech", 300.0),
        ("caller_speech", 600.0),
        ("delegation_result", 710.0),
    ]
    await segment.close()
    await adapter.close()


async def test_and_the_second_one_is_judged_with_the_whole_history(live_case, text_execution_case):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controls["clarity"] = True
    controller, segment, socket = await voice(live_case, text_execution_case)
    await say(controller, "caller", "Tell me about my first and second invoice.", 0, 100)
    await say(controller, "assistant", "The first invoice is paid.", 200, 300)
    await say(controller, "caller", "And the second one?", 400, 510)
    await delegate(
        controller,
        segment,
        socket,
        510,
        "And the second one?",
        clear=False,
        extra={"requires_clarity_check": True},
    )
    await controller.process_next(emit=emit)
    clarity = requests[0]
    assert not clarity.get("stream")
    rendered = json.dumps(clarity["input"])
    # R8: the clarity check sees the conversation before the fragments.
    assert "[Caller, spoken] Tell me about my first and second invoice." in rendered
    assert "[Voice assistant, spoken] The first invoice is paid." in rendered
    assert "clear in the context of this conversation" in clarity["input"][-1]["content"]
    assert clarity["input"][-1]["content"].endswith("And the second one?")
    assert len(model_inputs(requests)) == 1
    await segment.close()
    await adapter.close()


async def test_the_transcription_wait_ends_early_when_the_transcript_catches_up(
    live_case, text_execution_case
):
    from caller_runtime.observability import Telemetry

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    telemetry = Telemetry("caller-runtime", "local")
    controller, segment, socket = await voice(
        live_case, text_execution_case, transcription_wait_seconds=2.0, telemetry=telemetry
    )
    await say(controller, "caller", "Check my ", 0, 400)
    await delegate(controller, segment, socket, 500, "Check my order")
    loop = asyncio.get_running_loop()
    started = loop.time()
    work = asyncio.create_task(controller.process_next(emit=emit))
    await asyncio.sleep(0.05)
    assert not requests
    await say(controller, "caller", "order.", 400, 520)
    await asyncio.wait_for(work, 5)
    assert loop.time() - started < 1.0
    sent = model_inputs(requests)[-1]["input"]
    assert {"role": "user", "content": "[Caller, spoken] Check my order."} in sent
    entry = controller.history.entries[0]
    assert entry.kind == "caller_speech" and entry.delivery == "transcribed"
    waits = [
        float(event["value"])
        for event in telemetry.events
        if event.get("measurement") == "transcription_wait_seconds"
    ]
    assert len(waits) == 1 and waits[0] < 1.0
    telemetry.close()
    await segment.close()
    await adapter.close()


async def test_after_the_wait_the_work_starts_and_the_pending_speech_is_partial(
    live_case, text_execution_case, monkeypatch
):
    from caller_runtime.services import conversation

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    flagged = []
    original = conversation.ConversationExecutor._await_transcript

    async def observed(self, item):
        await original(self, item)
        flagged.append(item.untranscribed)

    monkeypatch.setattr(conversation.ConversationExecutor, "_await_transcript", observed)
    controller, segment, socket = await voice(
        live_case, text_execution_case, transcription_wait_seconds=0.2
    )
    await say(controller, "caller", "I want to change my", 0, 300)
    await delegate(controller, segment, socket, 900, "I want to change my address")
    loop = asyncio.get_running_loop()
    started = loop.time()
    await controller.process_next(emit=emit)
    assert loop.time() - started >= 0.2
    assert flagged == [True]
    entry = controller.history.entries[0]
    assert entry.kind == "caller_speech" and entry.delivery == "partial"
    sent = model_inputs(requests)[-1]["input"]
    assert {
        "role": "user",
        "content": "[Caller, spoken] I want to change my [rest not transcribed]",
    } in sent
    await segment.close()
    await adapter.close()


async def test_the_voice_greeting_is_a_delegation_result_followed_by_its_speech(
    live_case, text_execution_case
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controller, segment, socket = await voice(live_case, text_execution_case)
    controller.record_greeting("Hello and welcome. How can I help?")
    await say(controller, "assistant", "Hello and welcome. ", 50, 150)
    await say(controller, "assistant", "How can I help?", 150, 250)
    await say(controller, "caller", "Billing, please.", 400, 510)
    await delegate(controller, segment, socket, 510, "Billing, please.")
    await controller.process_next(emit=emit)
    sent = model_inputs(requests)[-1]["input"]
    assert sent[:3] == [
        {
            "role": "assistant",
            "content": "[Result given to the voice assistant] Hello and welcome. How can I help?",
        },
        {
            "role": "assistant",
            "content": "[Voice assistant, spoken] Hello and welcome. How can I help?",
        },
        {"role": "user", "content": "[Caller, spoken] Billing, please."},
    ]
    assert controller.history.entries[0].agent_id == "welcome"
    await segment.close()
    await adapter.close()


@pytest.mark.parametrize("untranscribed", [False, True])
async def test_each_voice_delegation_reports_its_roles_handover_and_transcription(
    live_case, text_execution_case, untranscribed
):
    # Feature 007, FR-019 and R9: one content-free `test.delegation` when its work item ends.
    from caller_runtime.observability import Telemetry

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    telemetry = Telemetry("caller-runtime", "local")
    controller, segment, socket = await voice(
        live_case, text_execution_case, telemetry=telemetry, transcription_wait_seconds=0.1
    )
    # Routing hands over to beta's entry, which answers.
    controls["initial_flow"] = True
    await say(controller, "caller", "Synthetic private billing request", 0, 100)
    await delegate(controller, segment, socket, 300 if untranscribed else 100, "Billing.")
    await controller.process_next(emit=emit)
    # beta's entry answers the next one itself.
    await say(controller, "caller", "Synthetic private follow-up", 2000, 2100)
    await delegate(controller, segment, socket, 2100, "Follow-up.")
    await controller.process_next(emit=emit)
    try:
        events = [e for e in telemetry.events if e["event"] == "test.delegation"]
        assert [
            (
                e["outcome"],
                e["reason_code"],
                e["first_role"],
                e["handled_role"],
                e["handed_on"],
                e["untranscribed"],
            )
            for e in events
        ] == [
            ("success", "ready", "routing", "ordinary", "true", str(untranscribed).lower()),
            ("success", "ready", "ordinary", "ordinary", "false", "false"),
        ]
        assert all(e["kind"] == "voice" for e in events)
        assert len({e["correlation_id"] for e in events}) == 1
        assert "Synthetic private" not in json.dumps(telemetry.events)
        waits = [
            e for e in telemetry.events if e.get("measurement") == "transcription_wait_seconds"
        ]
        assert len(waits) == 2
    finally:
        telemetry.close()
        await segment.close()
        await adapter.close()
