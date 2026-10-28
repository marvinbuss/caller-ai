"""Explicit local admission controller; not a distributed quota or socket migration service."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import uuid4

from caller_runtime.adapters.session_repository import metadata_model
from caller_runtime.domain.session import CallSession, LifecycleState
from caller_runtime.domain.visibility import CommunityVisibility
from caller_runtime.metrics import RuntimeMetrics


class CapacityRepository(Protocol):
    async def create(self, metadata: Mapping[str, Any]) -> bool: ...

    async def read(self, session_id: str) -> dict[str, Any] | None: ...

    async def replace(self, session_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]: ...


class SharedCapacityRepository(CapacityRepository, Protocol):
    async def register_capacity_owner(
        self, owner_id: str, *, lease_seconds: float
    ) -> Literal["serving", "draining", "drained"]: ...

    async def renew_capacity_owner(
        self, owner_id: str, *, lease_seconds: float
    ) -> Literal["serving", "draining", "drained"]: ...

    async def serving_capacity_owners(self, owner_ids: tuple[str, ...]) -> tuple[str, ...]: ...

    async def capacity_owner_state(
        self, owner_id: str
    ) -> Literal["serving", "draining", "drained"]: ...

    async def begin_capacity_drain(
        self, owner_id: str
    ) -> Literal["serving", "draining", "drained"]: ...

    async def finish_capacity_drain(self, owner_id: str) -> Literal["postponed", "drained"]: ...

    async def reserve_capacity(
        self,
        metadata: Mapping[str, Any],
        *,
        maximum_sessions: int,
        sessions_per_owner: int,
        model_session_quota: int,
    ) -> Literal["reserved", "duplicate", "unavailable"]: ...

    async def release_capacity(self, session_id: str, *, physical_owner_id: str) -> None: ...

    async def reserve_model_capacity(
        self, session_id: str, *, physical_owner_id: str, reservation: str
    ) -> bool: ...

    async def release_model_capacity(
        self, reservation: str, *, session_id: str, physical_owner_id: str
    ) -> None: ...

    async def set_model_capacity_quota(self, quota: int) -> None: ...


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    outcome: Literal["admitted", "duplicate", "unavailable"]
    session_id: str


@dataclass(slots=True)
class OwnerCapacity:
    state: Literal["serving", "draining", "drained"]
    heartbeat_at: float


class CapacityService:
    """Repository-authorized local reservations; persistent live authority remains gated."""

    def __init__(
        self,
        *,
        repository: SharedCapacityRepository,
        configuration: Mapping[str, Any],
        sessions_per_owner: int,
        maximum_sessions: int,
        model_session_quota: int,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_seconds: float = 60,
        operation_budget: float = 10,
        maximum_pending: int = 256,
        metrics: RuntimeMetrics | None = None,
    ) -> None:
        if (
            any(
                type(value) is not int or not 1 <= value <= 10000
                for value in (sessions_per_owner, maximum_sessions)
            )
            or type(model_session_quota) is not int
            or not 0 <= model_session_quota <= 10000
            or type(maximum_pending) is not int
            or not 1 <= maximum_pending <= 4096
            or not 0 < lease_seconds <= 300
            or not 0 < operation_budget <= 10
        ):
            raise ValueError("invalid_capacity_configuration")
        self._repository = repository
        self._revision = CommunityVisibility(configuration).revision_id
        self._slots, self._maximum, self._quota = (
            sessions_per_owner,
            maximum_sessions,
            model_session_quota,
        )
        self._monotonic, self._utc_now = monotonic, utc_now
        self._lease, self._budget, self._pending_limit = (
            lease_seconds,
            operation_budget,
            maximum_pending,
        )
        self._owners: dict[str, OwnerCapacity] = {}
        self._active: dict[str, str] = {}
        self._extra_models: dict[str, tuple[str, str]] = {}
        self._pending = 0
        self._lock = asyncio.Lock()
        self._metrics = metrics

    def _publish_capacity(self) -> None:
        if self._metrics is not None:
            self._metrics.reservations(len(self._active))
            self._metrics.saturation("session_slots", min(1, len(self._active) / self._maximum))
            models = len(self._active) + len(self._extra_models)
            self._metrics.saturation(
                "model_sessions", min(1, models / self._quota) if self._quota else 1
            )
            self._metrics.saturation("admission_queue", self._pending / self._pending_limit)

    async def register_owner(self, owner_id: str) -> None:
        if not isinstance(owner_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", owner_id
        ):
            raise ValueError("invalid_capacity_owner")
        async with asyncio.timeout(self._budget):
            async with self._lock:
                if owner_id not in self._owners:
                    if len(self._owners) >= 1024:
                        raise RuntimeError("owner_registry_full")
                    state = await self._repository.register_capacity_owner(
                        owner_id, lease_seconds=self._lease
                    )
                    self._owners[owner_id] = OwnerCapacity(state, self._monotonic())

    async def admit(self, *, session_id: str, provider_call_ref: str) -> AdmissionResult:
        try:
            result = await self._admit(session_id=session_id, provider_call_ref=provider_call_ref)
            if self._metrics is not None:
                self._metrics.admission(result.outcome)
            return result
        finally:
            self._publish_capacity()

    async def _admit(self, *, session_id: str, provider_call_ref: str) -> AdmissionResult:
        unavailable = AdmissionResult("unavailable", session_id)
        if self._pending >= self._pending_limit:
            return unavailable
        self._pending += 1
        self._publish_capacity()
        try:
            async with asyncio.timeout(self._budget):
                async with self._lock:
                    if (
                        session_id in self._active
                        or await self._repository.read(session_id) is not None
                    ):
                        return AdmissionResult("duplicate", session_id)
                    if len(self._active) >= self._maximum:
                        return unavailable
                    counts = {
                        owner: sum(value == owner for value in self._active.values())
                        for owner in self._owners
                    }
                    available = set(
                        await self._repository.serving_capacity_owners(tuple(self._owners))
                    )
                    eligible = [owner for owner in self._owners if owner in available]
                    if not eligible:
                        return unavailable
                    owner = min(eligible, key=lambda item: (counts[item], item))
                    now = self._utc_now()
                    candidate = CallSession(
                        sessionId=session_id,
                        providerCallRef=provider_call_ref,
                        configurationRevisionId=self._revision,
                        physicalOwnerId=owner,
                        connectionEpoch=1,
                        startedAt=now,
                        leaseUntil=now + timedelta(seconds=self._lease),
                    )
                    reservation = await self._repository.reserve_capacity(
                        candidate.to_document(),
                        maximum_sessions=self._maximum,
                        sessions_per_owner=self._slots,
                        model_session_quota=self._quota,
                    )
                    if reservation == "duplicate":
                        return AdmissionResult("duplicate", session_id)
                    if reservation != "reserved":
                        return unavailable
                    self._active[session_id] = owner
                    if not await self._repository.create(candidate.to_document()):
                        self._active.pop(session_id, None)
                        return AdmissionResult("duplicate", session_id)
                    return AdmissionResult("admitted", session_id)
        except Exception:
            return unavailable
        finally:
            self._pending -= 1

    async def heartbeat(self, owner_id: str) -> None:
        async with asyncio.timeout(self._budget):
            async with self._lock:
                owner = self._owners[owner_id]
                await self._repository.capacity_owner_state(owner_id)
                owner.state = await self._repository.renew_capacity_owner(
                    owner_id, lease_seconds=self._lease
                )
                owner.heartbeat_at = self._monotonic()

    async def release(self, session_id: str) -> None:
        """Trusted caller invokes this only after verified physical call closure."""
        async with asyncio.timeout(self._budget):
            async with self._lock:
                if session_id not in self._active:
                    return
                document = await self._repository.read(session_id)
                if document is not None:
                    current = metadata_model(document)
                    if current.physical_owner_id != self._active[session_id]:
                        raise PermissionError("session_owner_mismatch")
                    if current.state != LifecycleState.ENDED:
                        ended = current.transition(
                            LifecycleState.ENDED, at=self._utc_now(), terminal_reason="completed"
                        )
                        await self._repository.replace(session_id, ended.model_dump(by_alias=True))
                await self._repository.release_capacity(
                    session_id, physical_owner_id=self._active[session_id]
                )
                self._active.pop(session_id, None)

                self._publish_capacity()

    async def active_count(self) -> int:
        async with self._lock:
            return len(self._active)

    async def reserve_model_session(self, session_id: str) -> str | None:
        """Trusted model adapter reserves overlap before preparing a fresh receiver."""
        async with asyncio.timeout(self._budget):
            async with self._lock:
                if session_id not in self._active:
                    raise PermissionError("session_model_budget_not_owned")
                document = await self._repository.read(session_id)
                if document is None:
                    raise PermissionError("session_authority_unknown")
                current = metadata_model(document)
                if (
                    current.state == LifecycleState.ENDED
                    or current.lease_until <= self._utc_now()
                    or current.physical_owner_id != self._active[session_id]
                ):
                    raise PermissionError("session_model_budget_not_owned")
                reservation = uuid4().hex
                if (
                    await self._repository.reserve_model_capacity(
                        session_id,
                        physical_owner_id=current.physical_owner_id,
                        reservation=reservation,
                    )
                    is not True
                ):
                    return None
                self._extra_models[reservation] = (session_id, current.physical_owner_id)
                self._publish_capacity()
                return reservation

    async def release_model_session(self, reservation: str) -> None:
        """Release only after confirmed model disposal; duplicate release is harmless."""
        async with asyncio.timeout(self._budget):
            async with self._lock:
                binding = self._extra_models.get(reservation)
                if binding is None:
                    return
                session_id, physical_owner_id = binding
                await self._repository.release_model_capacity(
                    reservation, session_id=session_id, physical_owner_id=physical_owner_id
                )
                del self._extra_models[reservation]
                self._publish_capacity()

    async def set_model_session_quota(self, quota: int) -> None:
        if type(quota) is not int or not 0 <= quota <= 10000:
            raise ValueError("invalid_model_session_quota")
        async with asyncio.timeout(self._budget):
            async with self._lock:
                await self._repository.set_model_capacity_quota(quota)
                self._quota = quota
                self._publish_capacity()

    async def owner_state(self, owner_id: str) -> str:
        async with asyncio.timeout(self._budget):
            async with self._lock:
                owner = self._owners[owner_id]
                owner.state = await self._repository.capacity_owner_state(owner_id)
                return owner.state

    async def begin_drain(self, owner_id: str) -> str:
        async with asyncio.timeout(self._budget):
            async with self._lock:
                owner = self._owners[owner_id]
                owner.state = await self._repository.begin_capacity_drain(owner_id)
                return owner.state

    async def finish_drain(self, owner_id: str) -> str:
        async with asyncio.timeout(self._budget):
            async with self._lock:
                owner = self._owners[owner_id]
                outcome = await self._repository.finish_capacity_drain(owner_id)
                if outcome == "drained":
                    owner.state = "drained"
                return outcome
