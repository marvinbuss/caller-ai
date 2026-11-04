"""Terminal-cleanup observation and independent verification of cleanup evidence.

`CleanupObservations` is written by the owner of a session's resources as they are disposed;
`ResourceCleanupVerifier` checks a claimed cleanup against that independent record before the
admission slot may be released. Mismatches are `rejected`; missing or late evidence is `unknown`,
which keeps the slot held rather than guessing.
"""

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from uuid import uuid4

from caller_runtime.domain.conversation import (
    AttemptIntent,
    CleanupEvidence,
    CleanupObservation,
    ClosureReceipt,
    ResourceKind,
    ResourceProgress,
    TestSlot,
    initial_resources,
    utc,
)


class CleanupReceiptVerifier(Protocol):
    async def verify(
        self, slot: TestSlot, evidence: CleanupEvidence, deadline: float
    ) -> Literal["verified", "unknown", "rejected"]: ...


@dataclass(slots=True)
class _CleanupRecord:
    slot: TestSlot
    disposed: Callable[[], Awaitable[bool]]
    resources: dict[ResourceKind, ResourceProgress] = field(default_factory=initial_resources)
    closure: ClosureReceipt | None = None
    observation: CleanupObservation | None = None


class CleanupObservations:
    def __init__(self, *, owner_id: str, utc_now: Callable[[], datetime]) -> None:
        if not owner_id or utc_now is None:
            raise ValueError("trusted_cleanup_owner_required")
        self._owner_id, self._utc_now = owner_id, utc_now
        self._records: dict[str, _CleanupRecord] = {}

    def register(self, slot: TestSlot, *, disposed: Callable[[], Awaitable[bool]]) -> None:
        if slot.owner_id != self._owner_id or not callable(disposed):
            raise PermissionError("foreign_cleanup_owner")
        if slot.session_id in self._records:
            raise PermissionError("cleanup_owner_already_registered")
        if len(self._records) >= 5:
            raise PermissionError("cleanup_observation_capacity")
        self._records[slot.session_id] = _CleanupRecord(slot.model_copy(deep=True), disposed)

    def record(
        self, intent: AttemptIntent, settlement_ref: str, *, closure: ClosureReceipt | None = None
    ) -> None:
        record = self._records.get(intent.session_id)
        if (
            record is None
            or intent.owner_id != self._owner_id
            or intent.owner_epoch != record.slot.owner_epoch
        ):
            raise PermissionError("foreign_cleanup_owner")
        if (intent.kind == "live") != (closure is not None):
            raise PermissionError("invalid_cleanup_observation")
        progress = ResourceProgress(
            attempted=intent.number,
            settled=intent.number,
            attempt=intent,
            settlementRef=settlement_ref,
        )
        previous = record.resources[intent.kind]
        if previous == progress and (closure is None or closure == record.closure):
            return
        if intent.number <= previous.attempted:
            raise PermissionError("stale_cleanup_observation")
        if closure is not None and (
            closure.session_id != intent.session_id
            or closure.owner_id != intent.owner_id
            or closure.owner_epoch != intent.owner_epoch
            or closure.generation != intent.generation
            or closure.provenance_ref != intent.provenance_ref
            or closure.provider_session_id != intent.provider_session_id
        ):
            raise PermissionError("foreign_cleanup_observation")
        record.resources[intent.kind] = progress
        if closure is not None:
            record.closure = closure
        record.observation = None

    async def observe(self, slot: TestSlot, deadline: float) -> CleanupObservation | None:
        record = self._records.get(slot.session_id)
        if (
            record is None
            or not slot.terminal_fence
            or slot.owner_id != self._owner_id
            or slot.owner_epoch != record.slot.owner_epoch
            or slot.mode != record.slot.mode
            or record.resources != slot.resources
            or record.closure != slot.closure_receipt
        ):
            return None
        try:
            async with asyncio.timeout_at(deadline):
                if await record.disposed() is not True:
                    return None
            if record.observation is None:
                record.observation = CleanupObservation(
                    sessionId=slot.session_id,
                    mode=slot.mode,
                    ownerId=slot.owner_id,
                    ownerEpoch=slot.owner_epoch,
                    observedAt=utc(self._utc_now()),
                    disposalRef="disposed-" + uuid4().hex,
                    settlementRef="settled-" + uuid4().hex if slot.attempted_through else None,
                    resources=record.resources,
                    closure=record.closure,
                    disposed=True,
                )
            return CleanupObservation.model_validate_json(
                record.observation.model_dump_json(by_alias=True)
            )
        except Exception:
            return None

    def forget(self, session_id: str) -> None:
        self._records.pop(session_id, None)


class ResourceCleanupVerifier:
    def __init__(
        self,
        *,
        observe: Callable[[TestSlot, float], Awaitable[CleanupObservation | None]],
        utc_now: Callable[[], datetime],
    ) -> None:
        if observe is None or utc_now is None:
            raise ValueError("independent_cleanup_observation_required")
        self._observe, self._utc_now = observe, utc_now

    @staticmethod
    def evidence(slot: TestSlot, observed: CleanupObservation) -> CleanupEvidence:
        kind: Literal["no_external_attempt", "work_disposed", "speech_session_closed"] = (
            "speech_session_closed"
            if slot.live_ever_attempted
            else "no_external_attempt"
            if slot.attempted_through == 0
            else "work_disposed"
        )
        return CleanupEvidence(
            kind=kind,
            sessionId=slot.session_id,
            mode=slot.mode,
            ownerId=slot.owner_id,
            ownerEpoch=slot.owner_epoch,
            attemptWatermark=slot.attempted_through,
            observedAt=observed.observed_at,
            disposalRef=observed.disposal_ref,
            settlementRef=observed.settlement_ref,
            closure=observed.closure,
        )

    async def verify(
        self, slot: TestSlot, evidence: CleanupEvidence, deadline: float
    ) -> Literal["verified", "unknown", "rejected"]:
        if (
            not math.isfinite(deadline)
            or not 0 < deadline - asyncio.get_running_loop().time() <= 10
        ):
            return "unknown"
        expected = (
            "speech_session_closed"
            if slot.live_ever_attempted
            else "no_external_attempt"
            if slot.attempted_through == 0
            else "work_disposed"
        )
        if (
            not slot.terminal_fence
            or evidence.kind != expected
            or evidence.session_id != slot.session_id
            or evidence.mode != slot.mode
            or evidence.owner_id != slot.owner_id
            or evidence.owner_epoch != slot.owner_epoch
            or evidence.attempt_watermark != slot.attempted_through
        ):
            return "rejected"
        if slot.settled_through != slot.attempted_through:
            return "unknown"
        try:
            async with asyncio.timeout_at(deadline):
                observed = await self._observe(slot, deadline)
            if observed is None:
                return "unknown"
            now = utc(self._utc_now())
            if (
                observed.session_id != slot.session_id
                or observed.mode != slot.mode
                or observed.owner_id != slot.owner_id
                or observed.owner_epoch != slot.owner_epoch
                or observed.disposal_ref != evidence.disposal_ref
                or observed.settlement_ref != evidence.settlement_ref
                or observed.observed_at != evidence.observed_at
                or not slot.started_at <= observed.observed_at <= now
                or observed.resources != slot.resources
                or observed.closure != evidence.closure
                or observed.closure != slot.closure_receipt
            ):
                return "rejected"
            for progress in observed.resources.values():
                if progress.attempted != progress.settled:
                    return "unknown"
                if (
                    progress.attempt is not None
                    and progress.attempt.recorded_at > observed.observed_at
                ):
                    return "rejected"
            if slot.live_ever_attempted:
                live = observed.resources["live"].attempt
                closure = observed.closure
                if live is None or closure is None:
                    return "unknown"
                if (
                    closure.session_id != live.session_id
                    or closure.owner_id != live.owner_id
                    or closure.owner_epoch != live.owner_epoch
                    or closure.generation != live.generation
                    or closure.provider_session_id != live.provider_session_id
                    or closure.provenance_ref != live.provenance_ref
                    or not live.recorded_at <= closure.observed_at <= observed.observed_at
                ):
                    return "rejected"
            return "verified"
        except Exception:
            return "unknown"
