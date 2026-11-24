"""Condensing the oldest history entries at the limit (feature 007, US5: R11, FR-016, SC-006)."""

import asyncio
import json

import pytest

from caller_runtime.domain.shared_history import (
    CONDENSE_ABOVE,
    CONDENSE_BELOW,
    ENTRY_LIMIT,
    HISTORY_LIMIT,
    Handover,
    SharedHistory,
)
from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store
from tests.unit.test_admission import live_case as live_case
from tests.unit.test_conversation import text_execution_case as text_execution_case

FILLER = "x" * 4000


def handover() -> Handover:
    return Handover(
        source_agent_id="b-entry",
        source_role="ordinary",
        destination_agent_id="b-peer",
        destination_role="ordinary",
        target_community_id="beta",
        path="peer",
        outcome="applied",
        reason="applied",
    )


def long_history(turns: int, *, timeline: bool = False) -> SharedHistory:
    """`turns` exchanges of about 8 KiB each; the second asks a question the user confirms."""
    history = SharedHistory(timeline=timeline)
    for turn in range(turns):
        at = {"at_ms": float(turn * 10)} if timeline else {}
        if timeline:
            history.record_caller_speech(f"Request {turn} " + FILLER, at_ms=turn * 10.0)
        else:
            history.record_user_text(f"Request {turn} " + FILLER)
        question = turn == 1
        answer = f"Answer {turn} " + FILLER + ("Shall I cancel order A-100?" if question else ".")
        if timeline:
            history.record_delegation_result(answer, agent_id="b-entry", at_ms=turn * 10.0 + 1)
        else:
            history.record_agent_answer(answer, agent_id="b-entry")
        if turn == 2:
            history.record_handover(
                handover(), "call_h", "handover_0", "{}", agent_id="b-entry", **at
            )
            history.record_progress("waiting_for_input", "Which address?", agent_id="b-peer", **at)
    return history


def test_the_trigger_is_above_192_kib_or_a_step_past_2048_entries():
    from caller_runtime.services.condensing import STEP_ENTRIES, needed

    small = long_history(2)
    assert not needed(small)
    assert needed(small, extra_bytes=CONDENSE_ABOVE)
    assert needed(long_history(26))
    many = SharedHistory()
    for index in range(ENTRY_LIMIT - STEP_ENTRIES + 1):
        many.record_user_text(f"m{index}")
    assert needed(many) and not needed(SharedHistory())
    assert needed(small, extra_entries=ENTRY_LIMIT)


def test_the_oldest_entries_are_condensed_below_128_kib_and_the_key_entries_kept():
    from caller_runtime.services.condensing import condensed, kept_verbatim, plan

    history = long_history(26)
    entries = history.entries
    keep = kept_verbatim(entries)
    kinds = [entry.kind for entry, kept in zip(entries, keep, strict=True) if kept]
    # The handover, the waiting update, and the user's replies to the question and the update.
    assert kinds == ["user_text", "handover", "progress", "user_text"]
    assert entries[keep.index(True)].content.startswith("Request 2 ")
    chosen = plan(entries)
    assert chosen is not None
    assert chosen.condensed[0].content.startswith("Request 0 ")
    result = condensed(history, chosen, "Synthetic account of the earlier requests.")
    assert result.rendered_bytes < CONDENSE_BELOW and len(result.entries) < ENTRY_LIMIT
    assert sum(entry.kind == "condensed" for entry in result.entries) == 1
    assert result.entries[0].kind == "condensed" and result.condensed
    survivors = [entry for entry in result.entries if entry.kind in {"handover", "progress"}]
    assert [entry.kind for entry in survivors] == ["handover", "progress"]
    replies = [entry.content[:10] for entry in result.entries if entry.kind == "user_text"]
    assert "Request 2 " in replies and "Request 3 " in replies
    # The newest entries are untouched.
    assert result.entries[-1] == entries[-1]


def test_voice_histories_condense_on_their_timeline():
    from caller_runtime.services.condensing import condensed, plan

    history = long_history(26, timeline=True)
    chosen = plan(history.entries)
    assert chosen is not None
    result = condensed(history, chosen, "Synthetic account.")
    assert result.timeline and result.rendered_bytes < CONDENSE_BELOW
    assert result.entries[0].kind == "condensed" and result.entries[0].at_ms == 0.0
    assert [entry.at_ms or 0.0 for entry in result.entries] == sorted(
        entry.at_ms or 0.0 for entry in result.entries
    )
    # The caller's reply after the question survives as spoken.
    assert any(
        entry.kind == "caller_speech" and entry.content.startswith("Request 2 ")
        for entry in result.entries
    )


def test_a_tool_call_and_its_result_are_never_split():
    from caller_runtime.services.condensing import plan

    history = SharedHistory()
    for index in range(30):
        history.record_tool_call("lookup", f"call_{index}", "{}", agent_id="b-entry")
        history.record_tool_result("lookup", f"call_{index}", FILLER * 2, agent_id="b-entry")
    chosen = plan(history.entries)
    assert chosen is not None
    calls = {entry.call_id for entry in chosen.condensed if entry.kind == "tool_call"}
    results = {entry.call_id for entry in chosen.condensed if entry.kind == "tool_result"}
    assert calls == results and chosen.rest[0].kind == "tool_call"


def test_when_the_kept_entries_alone_are_too_many_there_is_no_plan():
    from caller_runtime.services.condensing import plan

    history = SharedHistory()
    for index in range(ENTRY_LIMIT - 1):
        history.record_handover(handover(), f"call_{index}", "handover_0", "{}", agent_id="b-entry")
    assert plan(history.entries) is None


def test_a_result_still_over_the_history_limit_ends_with_the_limit(monkeypatch):
    from caller_runtime.services import condensing

    history = long_history(26)
    chosen = condensing.plan(history.entries)
    assert chosen is not None
    # One entry is cut at 64 KiB, so only a lower limit shows the check.
    monkeypatch.setattr(condensing, "HISTORY_LIMIT", CONDENSE_BELOW)
    with pytest.raises(PermissionError, match="conversation_context_limit"):
        condensing.condensed(history, chosen, "y" * HISTORY_LIMIT)


def grown(controller, turns: int) -> None:
    for entry in long_history(turns).entries:
        controller.history.record(entry)


@pytest.fixture
def small_limits(monkeypatch, text_execution_case):
    """The fixture model takes 64 KiB of input, so the thresholds shrink with it."""
    from caller_runtime.services import condensing

    adapter = text_execution_case[-1]
    monkeypatch.setattr(adapter, "max_input_tokens", 65536)
    monkeypatch.setattr(condensing, "CONDENSE_ABOVE", 48 * 1024)
    monkeypatch.setattr(condensing, "CONDENSE_BELOW", 32 * 1024)


def test_condensing_runs_before_the_step_with_no_tools_and_2048_output_tokens(
    text_execution_case, small_limits
):
    from caller_runtime.observability import Telemetry

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    telemetry = Telemetry("caller-runtime", "local")

    async def scenario():
        controls["telemetry"] = telemetry
        controller = await setup()
        grown(controller, 8)
        await controller.submit_text("text-long", "And the next question?")
        await controller.process_next(emit=emit)
        (request,) = controls["condense_requests"]
        assert request["max_output_tokens"] == 2048 and not request.get("tools")
        assert request["store"] is False and request["reasoning"] == {"effort": "low"}
        assert request["input"][-1]["content"].startswith("Condense the conversation above")
        assert "Request 0 " in json.dumps(request["input"])
        types = [message["type"] for message in messages]
        assert types.index("history.condensed") < types.index("text.delta")
        sent = requests[-1]["input"]
        assert sent[0]["content"].startswith("[Condensed earlier conversation, untrusted]\n")
        assert "Synthetic condensed account" in sent[0]["content"]
        assert "Request 0 " not in json.dumps(sent)
        assert controller.history.condensed
        events = [e for e in telemetry.events if e["event"] == "test.history"]
        assert [(e["outcome"], e["reason_code"], e["history_kib"]) for e in events] == [
            ("success", "condensed", "16-64")
        ]
        await adapter.close()

    try:
        asyncio.run(scenario())
    finally:
        telemetry.close()


@pytest.mark.parametrize("failure", ["request", "kept"])
def test_when_condensing_cannot_help_the_conversation_ends_with_the_limit(
    text_execution_case, small_limits, failure
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        controller = await setup()
        if failure == "request":
            controls["condense_status"] = 400
            grown(controller, 8)
        else:
            # Only handovers, which are all kept: nothing can be condensed.
            for index in range(400):
                controller.history.record_handover(
                    handover(), f"call_{index}", "handover_0", "{}", agent_id="b-entry"
                )
            assert controller.history.rendered_bytes > 48 * 1024
        await controller.submit_text("text-limit", "One more question.")
        await controller.process_next(emit=emit)
        errors = [message for message in messages if message["type"] == "error"]
        assert [error["code"] for error in errors] == ["conversation_context_limit"]
        assert not [message for message in messages if message["type"] == "text.delta"]
        assert controller._failed and not requests
        await adapter.close()

    asyncio.run(scenario())


def test_the_limit_message_is_the_documented_text():
    from caller_runtime.services.condensing import LIMIT_MESSAGE, NOTICE

    assert LIMIT_MESSAGE == "This conversation became too long. Please start a new one."
    assert NOTICE == "Older parts of this conversation were condensed."


def test_in_voice_the_caller_hears_why_the_call_ends(live_case, text_execution_case, small_limits):
    from caller_runtime.services.condensing import LIMIT_MESSAGE
    from tests.integration.test_voice_delegation_context import delegate, say, voice

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        controller, segment, socket = await voice(live_case, text_execution_case)
        controls["condense_status"] = 400
        for entry in long_history(8, timeline=True).entries:
            controller.history.record(entry)
        await say(controller, "caller", "One more question.", 100_000, 100_100)
        await delegate(controller, segment, socket, 100_100, "One more question.")
        await controller.process_next(emit=emit)
        told = [e["content"] for e in socket.sent if e["type"] == "session.commentary.append"]
        assert told == [LIMIT_MESSAGE]
        errors = [message for message in messages if message["type"] == "error"]
        assert [error["code"] for error in errors] == ["conversation_context_limit"]
        assert controller._failed
        await segment.close()
        await adapter.close()

    asyncio.run(scenario())
