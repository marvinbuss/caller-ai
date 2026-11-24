"""One Live session per voice test, across agent changes (feature 007, US2: FR-010, FR-013)."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.integration.test_voice_delegation_context import delegate, say, voice
from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store
from tests.unit.test_admission import live_case as live_case
from tests.unit.test_conversation import text_execution_case as text_execution_case

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_two_agent_changes_keep_one_session_start_one_generation_and_no_greeting(
    live_case, text_execution_case, monkeypatch
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controller, segment, socket = await voice(live_case, text_execution_case)

    async def connect(url, **options):
        raise AssertionError("an agent change opens no second Live session")

    monkeypatch.setattr(segment.adapter, "_factory", connect)
    controls["initial_flow"] = True
    await say(controller, "caller", "Billing, please.", 0, 100)
    await delegate(controller, segment, socket, 100, "Billing, please.")
    await controller.process_next(emit=emit)
    # b-entry's sorted destinations: b-peer (handover_0), Goodbye, Routing.
    controls["wire_actions"] = [
        ("report_progress", {"state": "still_working", "message": "Asking a colleague."}),
        ("handover_0", {}),
    ]
    await say(controller, "caller", "A colleague, please.", 2000, 2100)
    await delegate(controller, segment, socket, 2100, "A colleague, please.")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "b-peer"
    assert [event["type"] for event in socket.sent].count("session.start") == 1
    assert controller.policy.slot.generation == 1 and socket.closed == 0
    changes = [m for m in messages if m["type"] == "agent.changed"]
    assert [(m["agentId"], m["generation"]) for m in changes] == [
        ("routing", 1),
        ("b-entry", 1),
        ("b-peer", 1),
    ]
    # No greeting after an agent change: only the two answers go to their delegations.
    assert not controls.get("greeting_requests")
    commentary = [e for e in socket.sent if e["type"] == "session.commentary.append"]
    assert [e["delegation_id"] for e in commentary] == [
        "delegation-100",
        "delegation-2100",
        "delegation-2100",
    ]
    # GPT-Live never receives tool calls, tool results or handovers (FR-013).
    sent = json.dumps(socket.sent)
    for marker in ("handover_", "function_call", "report_progress", "applied", "b-peer"):
        assert marker not in sent
    assert {e["type"] for e in socket.sent} <= {
        "session.start",
        "session.commentary.append",
        "session.thinking.append",
    }
    await segment.close()
    await adapter.close()


async def test_a_tool_call_of_the_replaced_agent_is_refused_by_the_agent_epoch(
    live_case, text_execution_case
):
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.services.conversation import StepActions

    service, repository, clock, socket, opened = live_case
    controller, segment, socket = await voice(live_case, text_execution_case)
    controller.capability_client = SimpleNamespace()
    budget = WorkBudget(clock.utc_now(), clock.utc_now() + timedelta(seconds=10))
    await controller.policy.handover("routing", "lifecycle", budget)
    await controller.policy.handover("b-entry", "routing", budget)
    await say(controller, "caller", "Look it up.", 0, 100)
    await delegate(controller, segment, socket, 100, "Look it up.")
    item = controller.queue.take(now=clock.utc_now())
    controller._current_item = item
    source = controller.policy.slot
    configured = next(
        agent
        for agent in controller.policy.snapshot.for_runtime()["agents"]
        if agent["agentId"] == "b-entry"
    )
    tools = controller._tools(configured, item, source, StepActions(), capabilities=True)
    assert tools
    await controller.policy.handover("b-peer", "peer", budget)
    # The old agent's work cannot act after the change; the epoch fences it (FR-010).
    with pytest.raises(PermissionError, match="stale"):
        await tools[0].on_invoke_tool(SimpleNamespace(tool_call_id="call_old"), '{"inputs":{}}')
    assert controller.policy.slot.generation == source.generation
    controller._current_item = None
    controller.queue.finish(item.receipt)
    await segment.close()


async def test_a_delegation_accepted_before_the_welcome_move_runs_on_routing(
    live_case, text_execution_case
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controller, segment, socket = await voice(live_case, text_execution_case)
    await say(controller, "caller", "Hello?", 0, 100)
    await delegate(controller, segment, socket, 100, "Hello?")
    # The greeting finished meanwhile and Welcome handed over to Routing (FR-030).
    assert await controller.leave_welcome(emit)
    await controller.process_next(emit=emit)
    assert not any(m["type"] == "error" for m in messages), messages
    assert requests[-1]["instructions"].endswith("Synthetic routing prompt")
    await segment.close()
    await adapter.close()


async def test_capture_frames_keep_flowing_while_an_agent_change_is_stored(live_case, monkeypatch):
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, clock, socket, opened = live_case
    slot, segment = await opened()
    slot = await service.check_output(slot.session_id, slot.owner_epoch, generation=1)
    policy = ConversationPolicy(slot=slot, snapshot=service.configuration.pin(), admission=service)
    entered, release = asyncio.Event(), asyncio.Event()
    transition = service.transition

    async def slow(*arguments, **keywords):
        entered.set()
        await release.wait()
        return await transition(*arguments, **keywords)

    monkeypatch.setattr(service, "transition", slow)
    change = asyncio.create_task(
        policy.handover(
            "routing",
            "lifecycle",
            WorkBudget(clock.utc_now(), clock.utc_now() + timedelta(seconds=10)),
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    # Incidents T095/T097 of feature 006: capture in flight during an agent change is kept.
    for _ in range(3):
        await segment.send_audio(b"\0" * 960)
    release.set()
    await change
    await segment.send_audio(b"\0" * 960)
    appended = [e for e in socket.sent if e["type"] == "session.input_audio.append"]
    assert len(appended) == 4 and policy.slot.generation == slot.generation
    await segment.close()
