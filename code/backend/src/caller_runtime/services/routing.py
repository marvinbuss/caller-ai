"""Trusted Routing-mediated entry selection and bounded clarification/closure."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

from caller_runtime.domain.visibility import CommunityVisibility

if TYPE_CHECKING:
    from caller_runtime.services.handover import HandoverCoordinator, TransferResult


class HandoverWindow:
    def __init__(
        self,
        *,
        limit: int = 10,
        seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or not 0 < seconds < math.inf
        ):
            raise ValueError("invalid_handover_window")
        self._limit, self._seconds, self._monotonic = limit, seconds, monotonic
        self._commits: deque[float] = deque()

    def allowed(self) -> bool:
        now = self._monotonic()
        if not math.isfinite(now):
            return False
        while self._commits and now - self._commits[0] >= self._seconds:
            self._commits.popleft()
        return len(self._commits) < self._limit

    def committed(self) -> None:
        if not self.allowed():
            raise RuntimeError("handover_limit_reached")
        self._commits.append(self._monotonic())


class RoutingOutput(Protocol):
    async def clarify(self, session_id: str) -> None: ...

    async def goodbye(self, session_id: str) -> None: ...


class RoutingService:
    def __init__(
        self,
        *,
        coordinator: HandoverCoordinator,
        visibility: CommunityVisibility,
        output: RoutingOutput,
    ) -> None:
        self._coordinator, self._visibility, self._output = coordinator, visibility, output
        self._clarified: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    async def _unresolved(self, session_id: str) -> TransferResult:
        from caller_runtime.services.handover import TransferResult

        if session_id not in self._clarified:
            self._clarified.add(session_id)
            try:
                async with asyncio.timeout(10):
                    await self._output.clarify(session_id)
            except Exception:
                return await self._coordinator.finish(session_id, goodbye=self._output.goodbye)
            return TransferResult("failed", "policy_rejected")
        return await self._coordinator.finish(session_id, goodbye=self._output.goodbye)

    async def transfer(
        self, session_id: str, destination_id: str, *, context: Mapping[str, Any]
    ) -> TransferResult:
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            result = await self._coordinator.transfer(session_id, destination_id, context=context)
            if result.reason_code == "loop_limited":
                return await self._unresolved(session_id)
            return result

    async def select_community(
        self, session_id: str, community_id: str | None, *, context: Mapping[str, Any]
    ) -> TransferResult:
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            deadline = time.monotonic() + 10
            directory = self._visibility.view_for(self._visibility.lifecycle_target("routing"))[
                "communities"
            ]
            entry = next(
                (item["entryAgentId"] for item in directory if item["communityId"] == community_id),
                None,
            )
            if entry is None:
                return await self._unresolved(session_id)
            routing = self._visibility.lifecycle_target("routing")
            if self._coordinator.metadata_for(session_id).active_agent_id != routing:
                result = await self._coordinator.transfer(
                    session_id, routing, context=context, deadline=deadline
                )
                if result.outcome != "committed":
                    return (
                        await self._unresolved(session_id)
                        if result.reason_code == "loop_limited"
                        else result
                    )
            result = await self._coordinator.transfer(
                session_id, entry, context=context, deadline=deadline
            )
            return (
                await self._unresolved(session_id)
                if result.reason_code == "loop_limited"
                else result
            )
