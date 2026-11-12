"""Reservation, fencing and attempt accounting for the one active test per manager.

`TestAdmissionService` is the only writer of the authority document (strong single partition,
ETag-conditioned). It admits a start permit once, tracks the epoch and generation of each slot, and
records every external attempt before it is dispatched and its settlement afterwards, so cleanup can
prove nothing was left running. Failures raise `AdmissionError` with an internal code; callers map
it to a public code and never see storage detail. There is no force-clear: a slot is released only
with verified cleanup evidence or when its deadlines pass.
"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from caller_runtime.adapters.admission_repository import (
    AuthorityConflict,
    AuthoritySnapshot,
    AuthorityUnavailable,
    TestAdmissionRepository,
)
from caller_runtime.domain.configuration import RevisionCache, RevisionSnapshot, encoded
from caller_runtime.domain.conversation import (
    MAX_WORK_SECONDS,
    AttemptIntent,
    CleanupEvidence,
    ClosureReceipt,
    Mode,
    ResourceKind,
    StartPermit,
    TestAuthority,
    TestSlot,
    WorkBudget,
    utc,
)
from caller_runtime.services.cleanup import CleanupReceiptVerifier

OperationResult = TypeVar("OperationResult")
# The contract allows media rechecks at most every second; frames arrive every 20 ms.
MEDIA_RECHECK_SECONDS = 0.5
# The watcher rechecks once per second; one check (read plus workload identity) shares this.
WATCH_CHECK_SECONDS = 0.9


class AdmissionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SettlementObserver(Protocol):
    def record(
        self, intent: AttemptIntent, settlement_ref: str, *, closure: ClosureReceipt | None = None
    ) -> None: ...


class TestAdmissionService:
    __test__ = False

    def __init__(
        self,
        *,
        repository: TestAdmissionRepository,
        configuration: RevisionCache,
        owner_id: str,
        utc_now: Callable[[], datetime],
        monotonic: Callable[[], float] = time.monotonic,
        max_clock_skew_seconds: float,
        cleanup_verifier: CleanupReceiptVerifier | None = None,
    ) -> None:
        if (
            repository is None
            or configuration is None
            or not owner_id
            or len(owner_id) > 128
            or type(max_clock_skew_seconds) not in (int, float)
            or not math.isfinite(max_clock_skew_seconds)
            or not 0 <= max_clock_skew_seconds <= 1
        ):
            raise ValueError("explicit_test_admission_ports_required")
        self.repository, self.configuration, self.owner_id = repository, configuration, owner_id
        self._utc_now, self._monotonic, self._skew = utc_now, monotonic, max_clock_skew_seconds
        self._last_clock: tuple[datetime, float] | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._terminal: dict[str, asyncio.Event] = {}
        self._startup: dict[str, datetime] = {}
        self._pins: dict[str, RevisionSnapshot] = {}
        self._cleanup_verifier = cleanup_verifier
        self._dispatched: dict[tuple[str, ResourceKind], str] = {}
        self._writes = 0
        self._media: tuple[AuthoritySnapshot, float, int] | None = None
        self._media_lock = asyncio.Lock()

    async def _replace(
        self, current: AuthoritySnapshot, candidate: TestAuthority, *, deadline: float
    ) -> None:
        self._writes += 1
        self._media = None
        try:
            await self.repository.replace(current, candidate, deadline=deadline)
        finally:
            self._writes += 1

    async def _media_snapshot(self) -> AuthoritySnapshot:
        async with self._media_lock:
            cached, tick = self._media, self._monotonic()
            if (
                cached is not None
                and cached[2] == self._writes
                and 0 <= tick - cached[1] < MEDIA_RECHECK_SECONDS
            ):
                return cached[0]
            writes = self._writes
            current = await self.repository.read(
                deadline=min(self._deadline(), asyncio.get_running_loop().time() + 1)
            )
            if writes == self._writes:
                self._media = (current, tick, writes)
            return current

    def _now(self) -> datetime:
        try:
            now, tick = utc(self._utc_now()), self._monotonic()
            if not math.isfinite(tick):
                raise ValueError("invalid_clock")
            if self._last_clock is not None:
                previous, prior_tick = self._last_clock
                elapsed = (now - previous).total_seconds()
                if (
                    elapsed < 0
                    or tick < prior_tick
                    or abs(elapsed - (tick - prior_tick)) > self._skew
                ):
                    raise ValueError("untrusted_clock")
            self._last_clock = (now, tick)
            return now
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise AdmissionError("clock_unavailable") from None

    def _deadline(
        self, boundary: datetime | None = None, *, expired_code: str = "admission_deadline_exceeded"
    ) -> float:
        now = self._now()
        remaining = (
            10.0
            if boundary is None
            else min(10.0, (utc(boundary) - now).total_seconds() - self._skew)
        )
        if remaining <= 0:
            raise AdmissionError(expired_code)
        return asyncio.get_running_loop().time() + remaining

    def _document(self, current: AuthoritySnapshot) -> tuple[dict[str, Any], datetime]:
        now = self._now()
        authority = current.authority
        if authority.utc_high_water > now + timedelta(seconds=self._skew):
            raise AdmissionError("clock_unavailable")
        document = authority.model_dump(mode="json", by_alias=True)
        document["utcHighWater"] = max(now, authority.utc_high_water).isoformat()
        return document, now

    @staticmethod
    def _owned(current: AuthoritySnapshot, session_id: str, owner_id: str, epoch: int) -> TestSlot:
        for slot in current.authority.slots:
            if slot.session_id == session_id:
                if slot.owner_id != owner_id or slot.owner_epoch != epoch:
                    break
                return slot
        raise AdmissionError("stale_owner")

    def _track(self, slot: TestSlot, revision: RevisionSnapshot) -> TestSlot:
        if (
            slot.revision_id != revision.revision_id
            or slot.graph_hash != revision.graph_hash
            or slot.policy_hash != revision.policy_hash
        ):
            raise AdmissionError("authority_unavailable")
        self._locks.setdefault(slot.session_id, asyncio.Lock())
        self._terminal.setdefault(slot.session_id, asyncio.Event())
        self._startup[slot.session_id] = slot.admission_deadline_utc
        self._pins[slot.session_id] = revision
        return slot

    def snapshot_for(self, slot: TestSlot) -> RevisionSnapshot:
        revision = self._pins.get(slot.session_id)
        if (
            revision is None
            or slot.owner_id != self.owner_id
            or slot.session_id not in self._startup
            or self._terminal[slot.session_id].is_set()
            or slot.revision_id != revision.revision_id
            or slot.graph_hash != revision.graph_hash
            or slot.policy_hash != revision.policy_hash
        ):
            raise AdmissionError("stale_owner")
        return revision

    def _prune(self, authority: TestAuthority) -> None:
        present = {slot.session_id for slot in authority.slots}
        for session_id in tuple(self._startup):
            if session_id not in present:
                terminal = self._terminal.get(session_id)
                if terminal is not None:
                    terminal.set()
                self._forget(session_id)

    def _permit_current(self, permit: StartPermit, now: datetime) -> None:
        checked = now + timedelta(seconds=self._skew)
        if checked < permit.admission_started_at or checked >= permit.admission_deadline_utc:
            raise AdmissionError("admission_deadline_exceeded")
        if checked >= min(
            permit.auth_expires_at, datetime.fromtimestamp(permit.expires_epoch, UTC)
        ):
            raise AdmissionError("permit_expired")

    async def reserve(self, permit: StartPermit) -> TestSlot:
        boundary = min(
            permit.admission_deadline_utc,
            permit.auth_expires_at,
            datetime.fromtimestamp(permit.expires_epoch, UTC),
        )
        deadline = self._deadline(boundary)
        desired_id = "test-" + uuid4().hex
        try:
            for _attempt in range(4):
                current = await self.repository.read(deadline=deadline)
                document, now = self._document(current)
                self._permit_current(permit, now)
                authority = current.authority
                self._prune(authority)
                if permit.nonce_digest in authority.consumed_nonces:
                    raise AdmissionError("permit_replayed")
                try:
                    revision = self.configuration.pin()
                    graph = revision.for_runtime()
                except (ValueError, PermissionError):
                    raise AdmissionError("configuration_unavailable") from None
                nonces = {
                    digest: expiry.isoformat()
                    for digest, expiry in authority.consumed_nonces.items()
                    if expiry > now
                }
                if len(nonces) >= 100:
                    raise AdmissionError("capacity_unavailable")
                nonces[permit.nonce_digest] = datetime.fromtimestamp(
                    permit.expires_epoch, UTC
                ).isoformat()
                document["consumedNonces"] = nonces
                denial = (
                    "manager_busy"
                    if any(slot.actor_key == permit.actor_key for slot in authority.slots)
                    else "capacity_unavailable"
                    if len(authority.slots) >= 5
                    else None
                )
                if denial is None:
                    welcome = next(
                        agent["agentId"] for agent in graph["agents"] if agent["role"] == "welcome"
                    )
                    idle = int(revision.conversation_settings().get("idleTimeoutSeconds", 120))
                    slot = TestSlot(
                        sessionId=desired_id,
                        actorKey=permit.actor_key,
                        requestId=permit.request_id,
                        mode=permit.mode,
                        ownerId=self.owner_id,
                        ownerEpoch=1,
                        revisionId=revision.revision_id,
                        graphHash=revision.graph_hash,
                        policyHash=revision.policy_hash,
                        startedAt=now,
                        admissionDeadlineUtc=permit.admission_deadline_utc,
                        authExpiresAt=permit.auth_expires_at,
                        expiresAt=now + timedelta(seconds=600),
                        idleExpiresAt=now + timedelta(seconds=idle),
                        idleSeconds=idle,
                        activeAgentId=welcome,
                        activeCommunityId=None,
                    )
                    document["slots"].append(slot.model_dump(mode="json", by_alias=True))
                candidate = TestAuthority.model_validate_json(encoded(document))
                try:
                    await self._replace(current, candidate, deadline=deadline)
                except AuthorityConflict:
                    continue
                except AuthorityUnavailable:
                    observed = await self.repository.read(deadline=deadline)
                    if permit.nonce_digest not in observed.authority.consumed_nonces:
                        raise
                    if denial is not None:
                        raise AdmissionError(denial) from None
                    confirmed = self._owned(observed, desired_id, self.owner_id, 1)
                    self._permit_current(permit, self._now())
                    return self._track(confirmed, revision)
                if denial is not None:
                    raise AdmissionError(denial)
                observed = await self.repository.read(deadline=deadline)
                confirmed = self._owned(observed, desired_id, self.owner_id, 1)
                self._permit_current(permit, self._now())
                return self._track(confirmed, revision)
            raise AdmissionError("authority_conflict")
        except AuthorityUnavailable:
            raise AdmissionError("authority_unavailable") from None

    def _business(
        self, slot: TestSlot, now: datetime, work: WorkBudget | None, *, media: bool = False
    ) -> None:
        if slot.terminal_fence or self._terminal.get(slot.session_id, asyncio.Event()).is_set():
            raise AdmissionError("terminal")
        checked = now + timedelta(seconds=self._skew)
        if checked >= min(slot.auth_expires_at, slot.expires_at, slot.idle_expires_at):
            raise AdmissionError("session_expired")
        if slot.ready_at is None:
            if media:
                raise AdmissionError("session_not_ready")
            if checked >= slot.admission_deadline_utc:
                raise AdmissionError("admission_deadline_exceeded")
        elif work is None and not media:
            raise AdmissionError("work_deadline_required")
        if work is not None:
            if (
                checked < work.work_accepted_at
                or checked >= work.work_deadline_utc
                or not timedelta(0)
                < work.work_deadline_utc - work.work_accepted_at
                <= timedelta(seconds=MAX_WORK_SECONDS)
            ):
                raise AdmissionError("work_deadline_exceeded")

    async def _change(
        self,
        session_id: str,
        epoch: int,
        deadline: float,
        transform: Callable[[TestSlot, datetime], TestSlot],
    ) -> TestSlot:
        try:
            for _attempt in range(4):
                current = await self.repository.read(deadline=deadline)
                slot = self._owned(current, session_id, self.owner_id, epoch)
                document, now = self._document(current)
                candidate_slot = transform(slot, now)
                if candidate_slot == slot:
                    return slot
                document["slots"] = [
                    candidate_slot.model_dump(mode="json", by_alias=True)
                    if entry["sessionId"] == session_id
                    else entry
                    for entry in document["slots"]
                ]
                candidate = TestAuthority.model_validate_json(encoded(document))
                try:
                    await self._replace(current, candidate, deadline=deadline)
                except AuthorityConflict:
                    continue
                except AuthorityUnavailable:
                    observed = await self.repository.read(deadline=deadline)
                    confirmed = self._owned(observed, session_id, self.owner_id, epoch)
                    if confirmed == candidate_slot:
                        return confirmed
                    raise
                observed = await self.repository.read(deadline=deadline)
                confirmed = self._owned(observed, session_id, self.owner_id, epoch)
                if confirmed != candidate_slot:
                    raise AdmissionError("authority_unavailable")
                return confirmed
            raise AdmissionError("authority_conflict")
        except AuthorityUnavailable:
            raise AdmissionError("authority_unavailable") from None

    async def begin_attempt(
        self,
        session_id: str,
        owner_epoch: int,
        *,
        kind: ResourceKind,
        binding_hash: str,
        generation: int,
        work: WorkBudget | None = None,
    ) -> AttemptIntent:
        if session_id not in self._locks or session_id not in self._startup:
            raise AdmissionError("stale_owner")
        boundary = work.work_deadline_utc if work is not None else self._startup[session_id]
        deadline = self._deadline(
            boundary,
            expired_code="work_deadline_exceeded"
            if work is not None
            else "admission_deadline_exceeded",
        )
        attempt_id, provenance = "attempt-" + uuid4().hex, "source-" + uuid4().hex

        def mutate(slot: TestSlot, now: datetime) -> TestSlot:
            self._business(slot, now, work)
            if slot.generation != generation:
                raise AdmissionError("stale_generation")
            if kind == "live" and slot.mode != "speech":
                raise AdmissionError("mode_mismatch")
            progress = slot.resources[kind]
            if progress.attempted != progress.settled:
                # One unsettled attempt per resource kind keeps cleanup evidence unambiguous.
                raise AdmissionError("work_busy")
            intent = AttemptIntent(
                number=slot.attempted_through + 1,
                kind=kind,
                sessionId=session_id,
                attemptId=attempt_id,
                ownerId=self.owner_id,
                ownerEpoch=owner_epoch,
                generation=generation,
                bindingHash=binding_hash,
                provenanceRef=provenance,
                recordedAt=now,
            )
            document = slot.model_dump(mode="json", by_alias=True)
            document["resources"][kind].update(
                attempted=intent.number, attempt=intent.model_dump(mode="json", by_alias=True)
            )
            document["attemptedThrough"] = intent.number
            document["liveEverAttempted"] = slot.live_ever_attempted or kind == "live"
            unresolved = [
                value["attempted"]
                for value in document["resources"].values()
                if value["attempted"] > value["settled"]
            ]
            document["settledThrough"] = min(unresolved) - 1
            if kind == "live":
                # A new Live attempt invalidates any earlier closure receipt.
                document["closureReceipt"] = None
            return TestSlot.model_validate_json(encoded(document))

        async with self._locks[session_id]:
            result = await self._change(session_id, owner_epoch, deadline, mutate)
            terminal = self._terminal.get(session_id)
            if terminal is None or terminal.is_set():
                raise AdmissionError("terminal")
            intent = result.resources[kind].attempt
            if intent is None or intent.attempt_id != attempt_id:
                raise AdmissionError("authority_unavailable")
            return intent

    async def user_activity(
        self, session_id: str, owner_epoch: int, *, generation: int, mode: Mode
    ) -> TestSlot:
        if session_id not in self._locks or session_id not in self._startup:
            raise AdmissionError("stale_owner")
        deadline = min(self._deadline(), asyncio.get_running_loop().time() + 1)

        def mutate(slot: TestSlot, now: datetime) -> TestSlot:
            self._business(slot, now, None, media=True)
            if slot.mode != mode:
                raise AdmissionError("mode_mismatch")
            if slot.generation != generation:
                raise AdmissionError("stale_generation")
            document = slot.model_dump(mode="json", by_alias=True)
            document.update(
                lastUserAt=now.isoformat(),
                idleExpiresAt=min(
                    now + timedelta(seconds=slot.idle_seconds), slot.expires_at
                ).isoformat(),
            )
            return TestSlot.model_validate_json(encoded(document))

        return await self._change(session_id, owner_epoch, deadline, mutate)

    async def transition(
        self,
        session_id: str,
        owner_epoch: int,
        *,
        source_agent_id: str,
        source_agent_epoch: int,
        target_agent_id: str,
        target_community_id: str | None,
        work: WorkBudget,
    ) -> TestSlot:
        if session_id not in self._locks or session_id not in self._startup:
            raise AdmissionError("stale_owner")
        deadline = self._deadline(work.work_deadline_utc, expired_code="work_deadline_exceeded")

        def mutate(slot: TestSlot, now: datetime) -> TestSlot:
            self._business(slot, now, work)
            if slot.active_agent_id != source_agent_id or slot.agent_epoch != source_agent_epoch:
                raise AdmissionError("stale_agent_epoch")
            if (
                slot.mode == "speech"
                and slot.resources["live"].attempted != slot.resources["live"].settled
            ):
                # The old Live socket must be confirmed closed before a new generation may start.
                raise AdmissionError("live_segment_not_closed")
            if slot.generation >= 2**32 - 1:
                raise AdmissionError("generation_exhausted")
            document = slot.model_dump(mode="json", by_alias=True)
            document.update(
                activeAgentId=target_agent_id,
                activeCommunityId=target_community_id,
                agentEpoch=slot.agent_epoch + 1,
                generation=slot.generation + 1,
            )
            return TestSlot.model_validate_json(encoded(document))

        async with self._locks[session_id]:
            return await self._change(session_id, owner_epoch, deadline, mutate)

    async def retire_generation(
        self, session_id: str, owner_epoch: int, *, generation: int, work: WorkBudget
    ) -> TestSlot:
        if session_id not in self._locks or session_id not in self._startup:
            raise AdmissionError("stale_owner")
        deadline = self._deadline(work.work_deadline_utc, expired_code="work_deadline_exceeded")

        def mutate(slot: TestSlot, now: datetime) -> TestSlot:
            self._business(slot, now, work)
            if slot.mode != "speech" or slot.generation != generation:
                raise AdmissionError("stale_generation")
            if slot.generation >= 2**32 - 1:
                raise AdmissionError("generation_exhausted")
            document = slot.model_dump(mode="json", by_alias=True)
            document["generation"] = slot.generation + 1
            return TestSlot.model_validate_json(encoded(document))

        async with self._locks[session_id]:
            return await self._change(session_id, owner_epoch, deadline, mutate)

    async def perform(
        self,
        intent: AttemptIntent,
        operation: Callable[[], Awaitable[OperationResult]],
        *,
        work: WorkBudget | None = None,
    ) -> OperationResult:
        session_id = intent.session_id
        if intent.owner_id != self.owner_id or session_id not in self._startup:
            raise AdmissionError("stale_owner")
        lock = self._locks.get(session_id)
        if lock is None:
            raise AdmissionError("stale_owner")
        boundary = work.work_deadline_utc if work is not None else self._startup[session_id]
        deadline = self._deadline(
            boundary,
            expired_code="work_deadline_exceeded"
            if work is not None
            else "admission_deadline_exceeded",
        )
        async with asyncio.timeout_at(deadline):
            async with lock:
                try:
                    current = await self.repository.read(deadline=deadline)
                except AuthorityUnavailable:
                    raise AdmissionError("authority_unavailable") from None
                slot = self._owned(current, session_id, self.owner_id, intent.owner_epoch)
                _, now = self._document(current)
                self._business(slot, now, work)
                progress = slot.resources[intent.kind]
                if (
                    slot.generation != intent.generation
                    or progress.attempt != intent
                    or progress.settled == progress.attempted
                ):
                    raise AdmissionError("stale_attempt")
                key = (session_id, intent.kind)
                if self._dispatched.get(key) == intent.attempt_id:
                    raise AdmissionError("attempt_already_dispatched")
                # Marked before awaiting so a retry can never dispatch the same attempt twice.
                self._dispatched[key] = intent.attempt_id
                return await operation()

    async def mark_ready(self, session_id: str, owner_epoch: int) -> TestSlot:
        if session_id not in self._locks:
            raise AdmissionError("stale_owner")
        deadline = self._deadline(self._startup[session_id])

        def mutate(slot: TestSlot, now: datetime) -> TestSlot:
            self._business(slot, now, None, media=slot.ready_at is not None)
            if slot.ready_at is not None:
                return slot
            document = slot.model_dump(mode="json", by_alias=True)
            document.update(phase="active", readyAt=now.isoformat())
            return TestSlot.model_validate_json(encoded(document))

        async with self._locks[session_id]:
            return await self._change(session_id, owner_epoch, deadline, mutate)

    async def bind_live_session(
        self, intent: AttemptIntent, *, provider_session_id: str
    ) -> AttemptIntent:
        session_id = intent.session_id
        if (
            intent.kind != "live"
            or intent.owner_id != self.owner_id
            or session_id not in self._locks
            or self._dispatched.get((session_id, "live")) != intent.attempt_id
        ):
            raise AdmissionError("stale_attempt")
        deadline = self._deadline()

        def mutate(slot: TestSlot, now: datetime) -> TestSlot:
            progress = slot.resources["live"]
            if progress.attempt != intent or progress.attempted == progress.settled:
                raise AdmissionError("stale_attempt")
            if intent.provider_session_id is not None:
                if intent.provider_session_id != provider_session_id:
                    raise AdmissionError("provider_session_conflict")
                return slot
            document = slot.model_dump(mode="json", by_alias=True)
            document["resources"]["live"]["attempt"]["providerSessionId"] = provider_session_id
            return TestSlot.model_validate_json(encoded(document))

        async with self._locks[session_id]:
            slot = await self._change(session_id, intent.owner_epoch, deadline, mutate)
            bound = slot.resources["live"].attempt
            if bound is None or bound.provider_session_id != provider_session_id:
                raise AdmissionError("authority_unavailable")
            return bound

    async def settle_attempt(
        self, intent: AttemptIntent, *, settlement_ref: str, closure: ClosureReceipt | None = None
    ) -> TestSlot:
        if intent.owner_id != self.owner_id:
            raise AdmissionError("stale_owner")
        if intent.session_id not in self._locks:
            raise AdmissionError("stale_owner")
        deadline = self._deadline()

        def mutate(slot: TestSlot, now: datetime) -> TestSlot:
            progress = slot.resources[intent.kind]
            if progress.attempt != intent:
                raise AdmissionError("stale_attempt")
            if progress.settled == progress.attempted:
                if progress.settlement_ref != settlement_ref:
                    raise AdmissionError("settlement_conflict")
                return slot
            if (intent.kind == "live") != (closure is not None):
                raise AdmissionError("closure_evidence_required")
            document = slot.model_dump(mode="json", by_alias=True)
            document["resources"][intent.kind].update(
                settled=intent.number, settlementRef=settlement_ref
            )
            unresolved = [
                value["attempted"]
                for value in document["resources"].values()
                if value["attempted"] > value["settled"]
            ]
            document["settledThrough"] = (
                min(unresolved) - 1 if unresolved else slot.attempted_through
            )
            if closure is not None:
                if closure.observed_at > now + timedelta(seconds=self._skew):
                    raise AdmissionError("invalid_cleanup_evidence")
                document["closureReceipt"] = closure.model_dump(mode="json", by_alias=True)
                document["closedThroughGeneration"] = closure.generation
            return TestSlot.model_validate_json(encoded(document))

        async with self._locks[intent.session_id]:
            return await self._change(intent.session_id, intent.owner_epoch, deadline, mutate)

    def _cleanup_matches(self, slot: TestSlot, evidence: CleanupEvidence, now: datetime) -> bool:
        expected = (
            "speech_session_closed"
            if slot.live_ever_attempted
            else "no_external_attempt"
            if slot.attempted_through == 0
            else "work_disposed"
        )
        return (
            slot.terminal_fence
            and evidence.kind == expected
            and evidence.session_id == slot.session_id
            and evidence.mode == slot.mode
            and evidence.owner_id == slot.owner_id
            and evidence.owner_epoch == slot.owner_epoch
            and evidence.attempt_watermark == slot.attempted_through
            and slot.settled_through == slot.attempted_through
            and slot.started_at <= evidence.observed_at <= now + timedelta(seconds=self._skew)
            and (not slot.live_ever_attempted or evidence.closure == slot.closure_receipt)
        )

    async def _verified_cleanup(
        self, slot: TestSlot, evidence: CleanupEvidence, now: datetime, deadline: float
    ) -> bool:
        if self._cleanup_verifier is None or not self._cleanup_matches(slot, evidence, now):
            return False
        try:
            async with asyncio.timeout_at(deadline):
                return await self._cleanup_verifier.verify(slot, evidence, deadline) == "verified"
        except Exception:
            return False

    async def fence_for_cleanup(self, session_id: str, owner_epoch: int) -> TestSlot:
        if self._cleanup_verifier is None:
            raise AdmissionError("cleanup_authority_required")
        deadline = self._deadline()
        local_fence = self._terminal.get(session_id)
        if local_fence is not None:
            local_fence.set()
        try:
            for _attempt in range(4):
                current = await self.repository.read(deadline=deadline)
                slot = next(
                    (entry for entry in current.authority.slots if entry.session_id == session_id),
                    None,
                )
                if slot is None or slot.owner_epoch != owner_epoch:
                    raise AdmissionError("stale_owner")
                if slot.terminal_fence:
                    return slot
                document, _now = self._document(current)
                updated = slot.model_dump(mode="json", by_alias=True)
                updated.update(
                    phase="ending",
                    terminalFence=True,
                    generation=min(slot.generation + 1, 2**32 - 1),
                )
                document["slots"] = [
                    updated if entry["sessionId"] == session_id else entry
                    for entry in document["slots"]
                ]
                try:
                    await self._replace(
                        current,
                        TestAuthority.model_validate_json(encoded(document)),
                        deadline=deadline,
                    )
                except AuthorityConflict:
                    continue
                except AuthorityUnavailable:
                    observed = await self.repository.read(deadline=deadline)
                    fenced = next(
                        (
                            entry
                            for entry in observed.authority.slots
                            if entry.session_id == session_id
                        ),
                        None,
                    )
                    if (
                        fenced is None
                        or fenced.owner_epoch != owner_epoch
                        or not fenced.terminal_fence
                    ):
                        raise
                    return fenced
                observed = await self.repository.read(deadline=deadline)
                fenced = next(
                    (entry for entry in observed.authority.slots if entry.session_id == session_id),
                    None,
                )
                if fenced is None or fenced.owner_epoch != owner_epoch or not fenced.terminal_fence:
                    raise AdmissionError("authority_unavailable")
                return fenced
            raise AdmissionError("authority_conflict")
        except AuthorityUnavailable:
            raise AdmissionError("authority_unavailable") from None

    async def release(self, session_id: str, owner_epoch: int, evidence: CleanupEvidence) -> bool:
        deadline = self._deadline()
        if session_id not in self._locks:
            try:
                observed = await self.repository.read(deadline=deadline)
                self._prune(observed.authority)
                if all(slot.session_id != session_id for slot in observed.authority.slots):
                    return True
            except AuthorityUnavailable:
                return False
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        if len(self._locks) > 5 and session_id not in self._startup:
            self._locks.pop(session_id, None)
            raise AdmissionError("capacity_unavailable")
        try:
            async with lock:
                for _attempt in range(4):
                    current = await self.repository.read(deadline=deadline)
                    document, now = self._document(current)
                    slot = next(
                        (
                            entry
                            for entry in current.authority.slots
                            if entry.session_id == session_id
                        ),
                        None,
                    )
                    if slot is None:
                        self._forget(session_id)
                        return True
                    if slot.owner_epoch != owner_epoch:
                        return False
                    if not await self._verified_cleanup(slot, evidence, now, deadline):
                        return False
                    if slot.cleanup_evidence != evidence:
                        updated = slot.model_dump(mode="json", by_alias=True)
                        updated.update(
                            phase="cleanup_pending",
                            terminalFence=True,
                            cleanupEvidence=evidence.model_dump(mode="json", by_alias=True),
                        )
                        document["slots"] = [
                            updated if entry["sessionId"] == session_id else entry
                            for entry in document["slots"]
                        ]
                        candidate = TestAuthority.model_validate_json(encoded(document))
                        try:
                            await self._replace(current, candidate, deadline=deadline)
                        except AuthorityConflict:
                            continue
                        except AuthorityUnavailable:
                            observed = await self.repository.read(deadline=deadline)
                            persisted = next(
                                (
                                    entry
                                    for entry in observed.authority.slots
                                    if entry.session_id == session_id
                                ),
                                None,
                            )
                            if persisted is None:
                                self._forget(session_id)
                                return True
                            if persisted.cleanup_evidence != evidence:
                                return False
                        current = await self.repository.read(deadline=deadline)
                        document, now = self._document(current)
                        slot = next(
                            (
                                entry
                                for entry in current.authority.slots
                                if entry.session_id == session_id
                            ),
                            None,
                        )
                        if slot is None:
                            self._forget(session_id)
                            return True
                        if slot.cleanup_evidence != evidence or not self._cleanup_matches(
                            slot, evidence, now
                        ):
                            return False
                    document["slots"] = [
                        entry for entry in document["slots"] if entry["sessionId"] != session_id
                    ]
                    try:
                        await self._replace(
                            current,
                            TestAuthority.model_validate_json(encoded(document)),
                            deadline=deadline,
                        )
                    except AuthorityConflict:
                        continue
                    except AuthorityUnavailable:
                        observed = await self.repository.read(deadline=deadline)
                        if any(
                            entry.session_id == session_id for entry in observed.authority.slots
                        ):
                            return False
                        self._forget(session_id)
                        return True
                    observed = await self.repository.read(deadline=deadline)
                    if any(entry.session_id == session_id for entry in observed.authority.slots):
                        return False
                    self._forget(session_id)
                    return True
                return False
        except AuthorityUnavailable:
            return False
        finally:
            if session_id not in self._startup:
                self._locks.pop(session_id, None)

    def _forget(self, session_id: str) -> None:
        self._startup.pop(session_id, None)
        self._pins.pop(session_id, None)
        self._terminal.pop(session_id, None)
        self._locks.pop(session_id, None)
        for key in tuple(self._dispatched):
            if key[0] == session_id:
                self._dispatched.pop(key, None)

    async def check_work(
        self, session_id: str, owner_epoch: int, *, generation: int, work: WorkBudget
    ) -> TestSlot:
        if session_id not in self._locks or session_id not in self._startup:
            raise AdmissionError("stale_owner")
        try:
            # Bounded by the work budget: a 1-s cap failed starts whenever one read stalled.
            current = await self.repository.read(
                deadline=self._deadline(
                    work.work_deadline_utc, expired_code="work_deadline_exceeded"
                )
            )
            slot = self._owned(current, session_id, self.owner_id, owner_epoch)
            _, now = self._document(current)
            self._business(slot, now, work)
            if slot.generation != generation:
                raise AdmissionError("stale_generation")
            return slot
        except AuthorityUnavailable:
            raise AdmissionError("authority_unavailable") from None

    async def check_media(self, session_id: str, owner_epoch: int, *, generation: int) -> TestSlot:
        if session_id not in self._locks:
            raise AdmissionError("stale_owner")
        try:
            current = await self._media_snapshot()
            slot = self._owned(current, session_id, self.owner_id, owner_epoch)
            _, now = self._document(current)
            self._business(slot, now, None, media=True)
            if slot.generation != generation:
                raise AdmissionError("stale_generation")
            return slot
        except AuthorityUnavailable:
            raise AdmissionError("authority_unavailable") from None

    async def check_output(self, session_id: str, owner_epoch: int, *, generation: int) -> TestSlot:
        return await self.check_media(session_id, owner_epoch, generation=generation)

    async def watch(
        self,
        session_id: str,
        owner_epoch: int,
        *,
        stopped: asyncio.Event,
        observed: Callable[[TestSlot], Awaitable[None]],
    ) -> None:
        if session_id not in self._locks:
            raise AdmissionError("stale_owner")
        while not stopped.is_set():
            try:
                started = asyncio.get_running_loop().time()
                # A 0.1-s budget lost live sessions to one slow read or a signing-key refresh.
                deadline = min(self._deadline(), started + WATCH_CHECK_SECONDS)
                current = await self.repository.read(deadline=deadline)
                slot = self._owned(current, session_id, self.owner_id, owner_epoch)
                _, now = self._document(current)
                self._business(slot, now, None, media=slot.ready_at is not None)
                try:
                    async with asyncio.timeout_at(deadline):
                        await observed(slot)
                except TimeoutError:
                    raise AdmissionError("authority_unavailable") from None
                if stopped.is_set():
                    return
                await asyncio.wait_for(
                    stopped.wait(),
                    timeout=max(0.0, started + 1 - asyncio.get_running_loop().time()),
                )
            except TimeoutError:
                continue
            except AuthorityUnavailable:
                raise AdmissionError("authority_unavailable") from None

    def fence_local(self, session_id: str) -> None:
        terminal = self._terminal.get(session_id)
        if terminal is not None:
            terminal.set()

    async def mark_terminal(self, session_id: str, owner_epoch: int) -> TestSlot:
        if session_id not in self._locks:
            raise AdmissionError("stale_owner")
        self._terminal[session_id].set()
        deadline = self._deadline()

        def mutate(slot: TestSlot, now: datetime) -> TestSlot:
            if slot.terminal_fence:
                return slot
            document = slot.model_dump(mode="json", by_alias=True)
            document.update(
                phase="ending", terminalFence=True, generation=min(slot.generation + 1, 2**32 - 1)
            )
            return TestSlot.model_validate_json(encoded(document))

        async with self._locks[session_id]:
            return await self._change(session_id, owner_epoch, deadline, mutate)
