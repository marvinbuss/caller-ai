"""Cleanup budgets and volatile disposal remain truthful under dependency failure."""

import asyncio

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("failure", [None, "hangup", "metadata", "hangup-blocked"])
async def test_terminal_cleanup_purges_owned_content_and_reports_confirmations_separately(failure):
    from caller_runtime.domain.cleanup import terminal_cleanup
    from caller_runtime.domain.session import VolatileCallContext

    raw = bytearray(b"synthetic-private-audio")
    message = {"content": "synthetic-private-content"}
    context = VolatileCallContext(
        audio_buffers=[raw], conversation=[message], request_summary="private"
    )
    decisions = []

    async def hangup():
        if failure == "hangup":
            raise RuntimeError("synthetic-private-provider-details")
        if failure == "hangup-blocked":
            await asyncio.Event().wait()
        return True

    async def end(acknowledged):
        decisions.append(acknowledged)
        if failure == "metadata":
            raise RuntimeError("synthetic-private-storage-details")

    deadline = asyncio.get_running_loop().time() + 0.1
    result = await terminal_cleanup(context=context, hangup=hangup, end=end, deadline=deadline)
    assert result.hangup_acknowledged is (failure not in {"hangup", "hangup-blocked"})
    assert result.terminal_confirmed is (failure != "metadata")
    assert decisions == [result.hangup_acknowledged]
    assert not context.conversation and not context.audio_buffers and not context.request_summary
    assert not message and raw == bytearray(len(raw))
    assert "synthetic-private" not in repr(result)


async def test_interrupt_shares_one_budget_and_never_ends_or_discards_conversation():
    from caller_runtime.domain.cleanup import interrupt

    events = []

    async def cancel():
        events.append("cancel")

    async def clear():
        events.append("clear")

    assert await interrupt(cancel=cancel, clear=clear)
    assert events == ["cancel", "clear"]
