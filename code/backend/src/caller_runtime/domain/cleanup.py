"""Bounded cancellation and terminal disposal; no content persistence or physical-erasure claim."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from caller_runtime.domain.session import VolatileCallContext


@dataclass(frozen=True, slots=True)
class CleanupResult:
    terminal_confirmed: bool
    hangup_acknowledged: bool


async def interrupt(
    *, cancel: Callable[[], Awaitable[None]], clear: Callable[[], Awaitable[None]]
) -> bool:
    deadline = asyncio.get_running_loop().time() + 1
    try:
        async with asyncio.timeout_at(deadline):
            await cancel()
            await clear()
        return True
    except Exception:
        return False


async def terminal_cleanup(
    *,
    context: VolatileCallContext | None,
    hangup: Callable[[], Awaitable[bool]],
    end: Callable[[bool], Awaitable[None]],
    deadline: float,
) -> CleanupResult:
    acknowledged = confirmed = False
    try:
        remaining = max(0, deadline - asyncio.get_running_loop().time())
        try:
            async with asyncio.timeout_at(deadline - min(0.05, remaining / 2)):
                acknowledged = await hangup() is True
        except Exception:
            pass
        finally:
            if context is not None:
                context.purge()
        try:
            async with asyncio.timeout_at(deadline):
                await end(acknowledged)
                confirmed = True
        except Exception:
            pass
    finally:
        if context is not None:
            context.purge()
    return CleanupResult(confirmed, acknowledged)
