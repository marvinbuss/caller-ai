"""Physical-owner dispatch and terminal recovery; never reattach another worker's socket."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from caller_runtime.adapters.session_repository import SessionConflict, metadata_model
from caller_runtime.domain.session import CallSession, LifecycleState, PhysicalConnectionClaim
from caller_runtime.services.capacity import CapacityRepository


class ConnectionPort(Protocol):
    async def send_audio(self, payload: bytes) -> None: ...

    async def explain_unavailable(self) -> None: ...

    async def hangup(self) -> bool: ...


class PhysicalConnectionRepository(CapacityRepository, Protocol):
    async def claim_connection(self, claim: PhysicalConnectionClaim) -> bool: ...

    async def connection_claims(self, owner_id: str) -> tuple[PhysicalConnectionClaim, ...]: ...

    async def connection_claim_current(self, claim: PhysicalConnectionClaim) -> bool: ...

    async def close_connection_claim(self, claim: PhysicalConnectionClaim) -> None: ...


@dataclass(frozen=True, slots=True)
class OwnerLossResult:
    terminal_confirmed: bool
    hangup_acknowledged: bool


@dataclass(slots=True, repr=False)
class ConnectionBinding:
    physical_owner_id: str
    connection_epoch: int
    connection: ConnectionPort
    claim: PhysicalConnectionClaim
    fenced: bool = False
    closed: bool = False
    loss_result: OwnerLossResult | None = None


class OwnerRouting:
    def __init__(
        self,
        *,
        repository: PhysicalConnectionRepository,
        operation_budget: float = 10,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not 0 < operation_budget <= 10:
            raise ValueError("invalid_owner_operation_budget")
        self._repository, self._budget = repository, operation_budget
        self._monotonic, self._utc_now = monotonic, utc_now
        self._bindings: dict[str, ConnectionBinding] = {}
        self._lock = asyncio.Lock()

    async def _current(self, session_id: str) -> CallSession:
        document = await self._repository.read(session_id)
        if document is None:
            raise PermissionError("session_authority_unknown")
        return metadata_model(document)

    def _valid(self, metadata: CallSession, owner: str, epoch: int) -> bool:
        return (
            type(epoch) is int
            and metadata.physical_owner_id == owner
            and metadata.connection_epoch == epoch
            and metadata.state != LifecycleState.ENDED
            and metadata.lease_until > self._utc_now()
        )

    async def attach(
        self,
        session_id: str,
        *,
        physical_owner_id: str,
        connection_epoch: int,
        connection: ConnectionPort,
    ) -> None:
        async with asyncio.timeout(self._budget):
            async with self._lock:
                if session_id in self._bindings or not self._valid(
                    await self._current(session_id), physical_owner_id, connection_epoch
                ):
                    raise PermissionError("physical_connection_not_owned")
                claim = PhysicalConnectionClaim(
                    session_id=session_id,
                    physical_owner_id=physical_owner_id,
                    connection_epoch=connection_epoch,
                    claim_id=uuid4().hex,
                )
                binding = ConnectionBinding(
                    physical_owner_id, connection_epoch, connection, claim, fenced=True
                )
                self._bindings[session_id] = binding
                if not await self._repository.claim_connection(claim):
                    del self._bindings[session_id]
                    raise PermissionError("physical_connection_not_owned")
                if not self._valid(
                    await self._current(session_id), physical_owner_id, connection_epoch
                ):
                    raise PermissionError("physical_connection_not_owned")
                binding.fenced = False

    async def send_audio(
        self,
        session_id: str,
        *,
        physical_owner_id: str,
        connection_epoch: int,
        agent_epoch: int,
        payload: bytes,
    ) -> bool:
        try:
            async with asyncio.timeout(self._budget):
                async with self._lock:
                    binding = self._bindings.get(session_id)
                    if (
                        binding is None
                        or binding.fenced
                        or binding.closed
                        or binding.physical_owner_id != physical_owner_id
                        or binding.connection_epoch != connection_epoch
                        or not isinstance(payload, bytes)
                        or len(payload) > 64 * 1024
                    ):
                        return False
                    metadata = await self._current(session_id)
                    if (
                        not self._valid(metadata, physical_owner_id, connection_epoch)
                        or type(agent_epoch) is not int
                        or agent_epoch != metadata.agent_epoch
                        or not await self._repository.connection_claim_current(binding.claim)
                    ):
                        return False
                    await binding.connection.send_audio(payload)
                    return True
        except Exception:
            return False

    async def owner_for_callback(self, session_id: str) -> str:
        async with asyncio.timeout(self._budget):
            metadata = await self._current(session_id)
            if not self._valid(metadata, metadata.physical_owner_id, metadata.connection_epoch):
                raise PermissionError("callback_owner_unavailable")
            if any(
                claim.session_id == session_id and claim.closed
                for claim in await self._repository.connection_claims(metadata.physical_owner_id)
            ):
                raise PermissionError("callback_owner_unavailable")
            return metadata.physical_owner_id

    def connection_for(self, session_id: str) -> ConnectionPort:
        binding = self._bindings[session_id]
        if binding.fenced or binding.closed:
            raise PermissionError("connection_unavailable")
        return binding.connection

    async def has_connections(self, physical_owner_id: str) -> bool:
        async with asyncio.timeout(self._budget):
            async with self._lock:
                return any(
                    binding.physical_owner_id == physical_owner_id and not binding.closed
                    for binding in self._bindings.values()
                ) or any(
                    not claim.closed
                    for claim in await self._repository.connection_claims(physical_owner_id)
                )

    async def detach(
        self,
        session_id: str,
        *,
        physical_owner_id: str,
        connection_epoch: int,
        hangup_acknowledged: bool,
    ) -> None:
        async with asyncio.timeout(self._budget):
            async with self._lock:
                binding = self._bindings.get(session_id)
                if (
                    binding is None
                    or binding.physical_owner_id != physical_owner_id
                    or binding.connection_epoch != connection_epoch
                    or hangup_acknowledged is not True
                ):
                    raise PermissionError("verified_connection_closure_required")
                await self._repository.close_connection_claim(binding.claim)
                binding.closed = binding.fenced = True

    async def owner_lost(
        self, session_id: str, *, physical_owner_id: str, connection_epoch: int
    ) -> OwnerLossResult:
        binding = self._bindings.get(session_id)
        if binding is not None:
            if (
                binding.physical_owner_id != physical_owner_id
                or binding.connection_epoch != connection_epoch
            ):
                return OwnerLossResult(False, False)
            if binding.fenced:
                return binding.loss_result or OwnerLossResult(False, binding.closed)
            binding.fenced = True
        deadline = asyncio.get_running_loop().time() + self._budget
        reserve = min(0.5, self._budget / 2)
        terminal = acknowledged = False
        try:
            async with asyncio.timeout_at(deadline - reserve):
                async with self._lock:
                    for _attempt in range(3):
                        current = await self._current(session_id)
                        if (
                            current.physical_owner_id != physical_owner_id
                            or current.connection_epoch != connection_epoch
                        ):
                            break
                        if current.state == LifecycleState.ENDED:
                            terminal = True
                            break
                        ended = current.transition(
                            LifecycleState.ENDED, at=self._utc_now(), terminal_reason="owner_lost"
                        )
                        try:
                            await self._repository.replace(
                                session_id, ended.model_dump(by_alias=True)
                            )
                        except SessionConflict:
                            continue
                        terminal = True
                        break
        except Exception:
            terminal = False
        if binding is not None:
            try:
                async with asyncio.timeout_at(deadline - reserve / 2):
                    await binding.connection.explain_unavailable()
            except Exception:
                pass
            try:
                async with asyncio.timeout_at(deadline):
                    acknowledged = await binding.connection.hangup() is True
                    if acknowledged:
                        await self._repository.close_connection_claim(binding.claim)
            except Exception:
                pass
            binding.closed = acknowledged
            binding.loss_result = OwnerLossResult(terminal, acknowledged)
        return OwnerLossResult(terminal, acknowledged)
