"""Authorized pre-drain control; active calls and unacknowledged sockets block retirement."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from caller_runtime.services.capacity import CapacityService
from caller_runtime.services.owner_routing import OwnerRouting


class DrainService:
    def __init__(
        self,
        *,
        capacity: CapacityService,
        owner_routing: OwnerRouting,
        authorize: Callable[[Any], Awaitable[str]],
        operation_budget: float = 10,
    ) -> None:
        if authorize is None or not 0 < operation_budget <= 10:
            raise ValueError("explicit_drain_authority_required")
        self._capacity, self._routing, self._authorize = capacity, owner_routing, authorize
        self._budget = operation_budget
        self._lock = asyncio.Lock()

    async def _authorized(self, principal: Any) -> None:
        actor = await self._authorize(principal)
        if not isinstance(actor, str) or not actor.strip():
            raise PermissionError("drain_permission_required")

    async def begin(self, owner_id: str, *, principal: Any) -> str:
        async with asyncio.timeout(self._budget):
            await self._authorized(principal)
            async with self._lock:
                return await self._capacity.begin_drain(owner_id)

    async def state(self, owner_id: str) -> str:
        async with asyncio.timeout(self._budget):
            return await self._capacity.owner_state(owner_id)

    async def can_terminate(self, owner_id: str) -> bool:
        async with asyncio.timeout(self._budget):
            return await self._capacity.owner_state(
                owner_id
            ) == "drained" and not await self._routing.has_connections(owner_id)

    async def finish(self, owner_id: str, *, principal: Any) -> str:
        async with asyncio.timeout(self._budget):
            await self._authorized(principal)
            async with self._lock:
                if await self._routing.has_connections(owner_id):
                    return "postponed"
                return await self._capacity.finish_drain(owner_id)
