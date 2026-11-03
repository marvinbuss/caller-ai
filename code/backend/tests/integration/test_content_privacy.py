"""Owned content is disposed without entering audit/metadata or default/safe telemetry."""

import asyncio

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
