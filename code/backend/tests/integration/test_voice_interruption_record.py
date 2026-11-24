"""What the voice assistant said stays apart from the agent's reply (feature 007, SC-007)."""

import asyncio
import json

import pytest

from tests.integration.test_voice_delegation_context import delegate, say, voice
from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store
from tests.unit.test_admission import live_case as live_case
from tests.unit.test_conversation import text_execution_case as text_execution_case

pytestmark = pytest.mark.anyio
LONG_REPLY = "This spoken sentence carries one part of a long answer to the caller. " * 30


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_an_interrupted_answer_is_recorded_as_spoken_given_and_not_delivered(
    live_case, text_execution_case
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controls["reply"] = LONG_REPLY
    controller, segment, socket = await voice(live_case, text_execution_case)

    async def read():
        while True:
            await segment.next_event()

    await say(controller, "caller", "Tell me everything about my contract.", 0, 500)
    await delegate(controller, segment, socket, 500, "Tell me everything about my contract.")
    reader = asyncio.create_task(read())
    work = asyncio.create_task(controller.process_next(emit=emit))
    for _ in range(1000):
        if [e for e in socket.sent if e["type"] == "session.commentary.append"]:
            break
        await asyncio.sleep(0.01)
    # The voice assistant speaks the first words of the first part, then the caller cuts in.
    await say(controller, "assistant", "This spoken sentence ", 900, 1000)
    await say(controller, "assistant", "carries", 1000, 1100)
    controller.interrupt_speech()
    await asyncio.wait_for(work, 10)
    await say(controller, "caller", "Stop, that is enough.", 1200, 1400)
    controller.flush_speech()
    reader.cancel()
    await asyncio.wait({reader})
    spoken = [e["content"] for e in socket.sent if e["type"] == "session.commentary.append"]
    rendered = [item["content"] for item in controller.history.render()]
    # The agent's reply is a delegation result: what was given, and what never was (FR-004).
    assert rendered[1] == "[Result given to the voice assistant] " + "".join(spoken)
    assert rendered[2] == "[Not delivered] " + LONG_REPLY[len("".join(spoken)) :]
    # The transcript is what the caller heard: it ends at the last delta before the cut.
    assert rendered[3] == "[Voice assistant, spoken] This spoken sentence carries"
    assert rendered[4] == "[Caller, spoken] Stop, that is enough."
    kinds = [(entry.kind, entry.delivery) for entry in controller.history.entries]
    assert kinds == [
        ("caller_speech", "transcribed"),
        ("delegation_result", None),
        ("delegation_result", "not_delivered"),
        ("assistant_speech", "transcribed"),
        ("caller_speech", "transcribed"),
    ]
    assert "This spoken sentence carries" not in json.dumps(
        [e for e in socket.sent if e["type"] == "session.commentary.append"][1:]
    )
    await segment.close()
    await adapter.close()
