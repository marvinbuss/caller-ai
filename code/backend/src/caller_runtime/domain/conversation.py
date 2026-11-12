"""Strict metadata models for one test session: the start permit, authority slots and clocks.

Everything here is bounded metadata, never conversation content; models forbid extra fields, are
frozen and hide input in validation errors. `StartPermit` encodes the timing rules the whole runtime
relies on: a 10 s admission window, a permit lifetime of at most 30 s, and a session lifetime of
600 s with a 120 s idle limit. Naive datetimes are rejected because every deadline is UTC.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Mode = Literal["text", "speech"]
ResourceKind = Literal["model", "capability", "live"]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_conversation_time_required")
    return value.astimezone(UTC)


AwareTime = Annotated[datetime, AfterValidator(utc)]


class Metadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, populate_by_name=True, hide_input_in_errors=True
    )


class StartPermit(Metadata):
    issuer: str = Field(alias="iss", min_length=1, max_length=512)
    audience: str = Field(alias="aud", min_length=1, max_length=512)
    issued_epoch: int = Field(alias="iat", ge=0)
    expires_epoch: int = Field(alias="exp", ge=0)
    issued_at: AwareTime = Field(alias="issuedAt")
    admission_started_at: AwareTime = Field(alias="admissionStartedAt")
    admission_deadline_utc: AwareTime = Field(alias="admissionDeadlineUtc")
    tenant_id: Identifier = Field(alias="tenantId")
    actor_id: Identifier = Field(alias="actorId")
    mode: Mode
    management_session_digest: Digest = Field(alias="managementSessionDigest")
    request_id: Identifier = Field(alias="requestId")
    nonce: Digest
    auth_expires_at: AwareTime = Field(alias="authExpiresAt")

    @property
    def actor_key(self) -> str:
        # Hashed so the owner key can address storage without holding the tenant or actor ids.
        return hashlib.sha256((self.tenant_id + "\0" + self.actor_id).encode("utf-8")).hexdigest()

    @property
    def nonce_digest(self) -> str:
        return hashlib.sha256(self.nonce.encode("ascii")).hexdigest()

    @model_validator(mode="after")
    def original_startup_budget(self) -> Self:
        if (
            self.issued_epoch != int(self.issued_at.timestamp())
            or not 0 < self.expires_epoch - self.issued_epoch <= 30
            or self.expires_epoch > int(self.auth_expires_at.timestamp())
            or self.admission_deadline_utc != self.admission_started_at + timedelta(seconds=10)
            or not self.admission_started_at <= self.issued_at < self.admission_deadline_utc
            or self.auth_expires_at <= self.issued_at
        ):
            raise ValueError("invalid_start_permit_lifetime")
        return self


class AttemptIntent(Metadata):
    number: int = Field(ge=1)
    kind: ResourceKind
    session_id: Identifier = Field(alias="sessionId")
    attempt_id: Identifier = Field(alias="attemptId")
    owner_id: Identifier = Field(alias="ownerId")
    owner_epoch: int = Field(alias="ownerEpoch", ge=1)
    generation: int = Field(ge=1, le=2**32 - 1)
    binding_hash: Digest = Field(alias="bindingHash")
    provenance_ref: Identifier = Field(alias="provenanceRef")
    recorded_at: AwareTime = Field(alias="recordedAt")
    provider_session_id: Identifier | None = Field(default=None, alias="providerSessionId")


class ResourceProgress(Metadata):
    attempted: int = Field(default=0, ge=0)
    settled: int = Field(default=0, ge=0)
    attempt: AttemptIntent | None = None
    settlement_ref: Identifier | None = Field(default=None, alias="settlementRef")

    @model_validator(mode="after")
    def valid_progress(self) -> Self:
        if self.settled > self.attempted:
            raise ValueError("invalid_attempt_watermark")
        if self.attempted == 0:
            if self.attempt is not None or self.settled or self.settlement_ref is not None:
                raise ValueError("invalid_zero_attempt_history")
        elif self.attempt is None or self.attempt.number != self.attempted:
            raise ValueError("missing_attempt_history")
        elif self.settled == self.attempted and self.settlement_ref is None:
            raise ValueError("missing_settlement_evidence")
        return self


class ClosureReceipt(Metadata):
    session_id: Identifier = Field(alias="sessionId")
    provider_session_id: Identifier = Field(alias="providerSessionId")
    owner_id: Identifier = Field(alias="ownerId")
    owner_epoch: int = Field(alias="ownerEpoch", ge=1)
    generation: int = Field(ge=1, le=2**32 - 1)
    observed_at: AwareTime = Field(alias="observedAt")
    provenance_ref: Identifier = Field(alias="provenanceRef")
    reason: Literal["close_requested", "expired", "content", "remote_hangup", "connection_lost"]
    voice_seconds: float = Field(alias="voiceSeconds", ge=0, allow_inf_nan=False)
    authenticated: Literal[True]


class CleanupObservation(Metadata):
    session_id: Identifier = Field(alias="sessionId")
    mode: Mode
    owner_id: Identifier = Field(alias="ownerId")
    owner_epoch: int = Field(alias="ownerEpoch", ge=1)
    observed_at: AwareTime = Field(alias="observedAt")
    disposal_ref: Identifier = Field(alias="disposalRef")
    settlement_ref: Identifier | None = Field(default=None, alias="settlementRef")
    resources: dict[ResourceKind, ResourceProgress]
    closure: ClosureReceipt | None = None
    disposed: Literal[True]

    @model_validator(mode="after")
    def exact_resources(self) -> Self:
        if set(self.resources) != {"model", "capability", "live"}:
            raise ValueError("missing_resource_observations")
        for kind, progress in self.resources.items():
            intent = progress.attempt
            if intent is not None and (
                intent.kind != kind
                or intent.session_id != self.session_id
                or intent.owner_id != self.owner_id
                or intent.owner_epoch != self.owner_epoch
            ):
                raise ValueError("foreign_resource_observation")
        return self


class CleanupEvidence(Metadata):
    kind: Literal["no_external_attempt", "work_disposed", "speech_session_closed"]
    session_id: Identifier = Field(alias="sessionId")
    mode: Mode
    owner_id: Identifier = Field(alias="ownerId")
    owner_epoch: int = Field(alias="ownerEpoch", ge=1)
    attempt_watermark: int = Field(alias="attemptWatermark", ge=0)
    observed_at: AwareTime = Field(alias="observedAt")
    disposal_ref: Identifier = Field(alias="disposalRef")
    settlement_ref: Identifier | None = Field(default=None, alias="settlementRef")
    closure: ClosureReceipt | None = None

    @model_validator(mode="after")
    def resource_specific(self) -> Self:
        if self.kind == "speech_session_closed":
            if self.mode != "speech" or self.closure is None or self.settlement_ref is None:
                raise ValueError("speech_closure_evidence_required")
        elif self.closure is not None:
            raise ValueError("unexpected_live_closure_evidence")
        if self.kind == "no_external_attempt" and self.attempt_watermark != 0:
            raise ValueError("attempted_work_is_not_zero_allocation")
        if self.kind == "work_disposed" and self.settlement_ref is None:
            raise ValueError("work_settlement_evidence_required")
        return self


def initial_resources() -> dict[ResourceKind, ResourceProgress]:
    return {
        "model": ResourceProgress(),
        "capability": ResourceProgress(),
        "live": ResourceProgress(),
    }


class TestSlot(Metadata):
    __test__ = False
    session_id: Identifier = Field(alias="sessionId")
    actor_key: Digest = Field(alias="actorKey")
    request_id: Identifier = Field(alias="requestId")
    mode: Mode
    owner_id: Identifier = Field(alias="ownerId")
    owner_epoch: int = Field(alias="ownerEpoch", ge=1)
    revision_id: Identifier = Field(alias="revisionId")
    graph_hash: Digest = Field(alias="graphHash")
    policy_hash: Digest = Field(alias="policyHash")
    started_at: AwareTime = Field(alias="startedAt")
    admission_deadline_utc: AwareTime = Field(alias="admissionDeadlineUtc")
    auth_expires_at: AwareTime = Field(alias="authExpiresAt")
    expires_at: AwareTime = Field(alias="expiresAt")
    idle_expires_at: AwareTime = Field(alias="idleExpiresAt")
    # From the admitting revision's `conversationSettings`; fixed for the session's life.
    idle_seconds: int = Field(default=120, alias="idleSeconds", ge=30, le=120)
    last_user_at: AwareTime | None = Field(default=None, alias="lastUserAt")
    phase: Literal["starting", "active", "ending", "cleanup_pending"] = "starting"
    ready_at: AwareTime | None = Field(default=None, alias="readyAt")
    terminal_fence: bool = Field(default=False, alias="terminalFence")
    generation: int = Field(default=1, ge=1, le=2**32 - 1)
    agent_epoch: int = Field(default=1, alias="agentEpoch", ge=1)
    active_agent_id: Identifier = Field(alias="activeAgentId")
    active_community_id: Identifier | None = Field(alias="activeCommunityId")
    attempted_through: int = Field(default=0, alias="attemptedThrough", ge=0)
    settled_through: int = Field(default=0, alias="settledThrough", ge=0)
    resources: dict[ResourceKind, ResourceProgress] = Field(default_factory=initial_resources)
    live_ever_attempted: bool = Field(default=False, alias="liveEverAttempted")
    closed_through_generation: int = Field(default=0, alias="closedThroughGeneration", ge=0)
    cleanup_evidence: CleanupEvidence | None = Field(default=None, alias="cleanupEvidence")
    closure_receipt: ClosureReceipt | None = Field(default=None, alias="closureReceipt")

    @model_validator(mode="after")
    def bounded_history_and_lifetime(self) -> Self:
        last_user = self.last_user_at or self.started_at
        if (
            self.expires_at != self.started_at + timedelta(seconds=600)
            or not self.started_at <= last_user < self.expires_at
            or self.idle_expires_at
            != min(last_user + timedelta(seconds=self.idle_seconds), self.expires_at)
            or not self.started_at
            < self.admission_deadline_utc
            <= self.started_at + timedelta(seconds=10)
            or self.auth_expires_at <= self.started_at
        ):
            raise ValueError("invalid_conversation_deadlines")
        if self.phase == "active" and self.ready_at is None:
            raise ValueError("missing_initial_readiness")
        if self.ready_at is not None and not (
            self.started_at
            <= self.ready_at
            < min(self.admission_deadline_utc, self.auth_expires_at)
        ):
            raise ValueError("late_initial_readiness")
        if self.terminal_fence != (self.phase in {"ending", "cleanup_pending"}):
            raise ValueError("invalid_terminal_fence")
        if set(self.resources) != {"model", "capability", "live"}:
            raise ValueError("missing_resource_history")
        unresolved = []
        for kind, progress in self.resources.items():
            attempt = progress.attempt
            if attempt is not None and (
                attempt.kind != kind
                or attempt.session_id != self.session_id
                or attempt.owner_id != self.owner_id
                or attempt.owner_epoch != self.owner_epoch
                or attempt.generation > self.generation
            ):
                raise ValueError("foreign_attempt_history")
            if progress.attempted > progress.settled:
                unresolved.append(progress.attempted)
        attempted = max(progress.attempted for progress in self.resources.values())
        settled = min(unresolved) - 1 if unresolved else attempted
        if self.attempted_through != attempted or self.settled_through != settled:
            raise ValueError("invalid_attempt_watermarks")
        if self.live_ever_attempted != (self.resources["live"].attempted > 0):
            raise ValueError("invalid_live_attempt_history")
        if self.mode == "text" and self.live_ever_attempted:
            raise ValueError("text_cannot_allocate_live")
        live = self.resources["live"]
        receipt = self.closure_receipt
        if self.closed_through_generation > self.generation:
            raise ValueError("invalid_closed_generation")
        if not self.live_ever_attempted and (receipt is not None or self.closed_through_generation):
            raise ValueError("unexpected_live_closure")
        if receipt is not None:
            attempt = live.attempt
            if (
                attempt is None
                or live.settled != live.attempted
                or receipt.session_id != self.session_id
                or receipt.owner_id != self.owner_id
                or receipt.owner_epoch != self.owner_epoch
                or receipt.generation != attempt.generation
                or receipt.provider_session_id != attempt.provider_session_id
                or receipt.provenance_ref != attempt.provenance_ref
                or receipt.observed_at < attempt.recorded_at
                or self.closed_through_generation != receipt.generation
            ):
                raise ValueError("foreign_live_closure")
        elif self.live_ever_attempted and live.settled == live.attempted:
            raise ValueError("missing_live_closure")
        evidence = self.cleanup_evidence
        if evidence is not None:
            if (
                not self.terminal_fence
                or evidence.session_id != self.session_id
                or evidence.mode != self.mode
                or evidence.owner_id != self.owner_id
                or evidence.owner_epoch != self.owner_epoch
                or evidence.attempt_watermark != self.attempted_through
                or evidence.observed_at < self.started_at
                or self.settled_through != self.attempted_through
            ):
                raise ValueError("foreign_or_unsettled_cleanup_evidence")
            if evidence.kind == "no_external_attempt" and self.attempted_through:
                raise ValueError("attempted_work_is_not_zero_allocation")
            if evidence.kind == "work_disposed" and self.live_ever_attempted:
                raise ValueError("live_closure_evidence_required")
            if evidence.kind == "speech_session_closed" and evidence.closure != receipt:
                raise ValueError("wrong_segment_cleanup_evidence")
        return self


class TestAuthority(Metadata):
    __test__ = False
    ttl: Literal[-1] = -1
    id: Literal["browser-test-authority-v1"]
    session_id: Literal["browser-test-authority-v1"] = Field(alias="sessionId")
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    utc_high_water: AwareTime = Field(alias="utcHighWater")
    slots: list[TestSlot] = Field(max_length=5)
    consumed_nonces: dict[Digest, AwareTime] = Field(alias="consumedNonces", max_length=100)

    @model_validator(mode="after")
    def bounded_authority(self) -> Self:
        if len({slot.session_id for slot in self.slots}) != len(self.slots):
            raise ValueError("duplicate_test_session")
        if len({slot.actor_key for slot in self.slots}) != len(self.slots):
            raise ValueError("duplicate_test_actor")
        for slot in self.slots:
            observations = [slot.started_at]
            observations += [
                value for value in (slot.last_user_at, slot.ready_at) if value is not None
            ]
            observations += [
                progress.attempt.recorded_at
                for progress in slot.resources.values()
                if progress.attempt is not None
            ]
            if slot.closure_receipt is not None:
                observations.append(slot.closure_receipt.observed_at)
            if slot.cleanup_evidence is not None:
                observations.append(slot.cleanup_evidence.observed_at)
            if max(observations) > self.utc_high_water:
                raise ValueError("future_authority_observation")
        if len(self.model_dump_json(by_alias=True).encode("utf-8")) > 128 * 1024:
            raise ValueError("test_authority_too_large")
        return self


@dataclass(slots=True)
class SessionLimits:
    started_at: datetime
    admission_deadline_utc: datetime
    auth_expires_at: datetime
    idle_seconds: int = 120
    ready_at: datetime | None = field(default=None, init=False)
    _last_user_at: datetime = field(init=False)
    _observed_at: datetime = field(init=False)
    _terminal: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.started_at = utc(self.started_at)
        self.admission_deadline_utc = utc(self.admission_deadline_utc)
        self.auth_expires_at = utc(self.auth_expires_at)
        if (
            not self.started_at
            < self.admission_deadline_utc
            <= self.started_at + timedelta(seconds=10)
        ):
            raise ValueError("invalid_admission_deadline")
        self._last_user_at = self._observed_at = self.started_at

    @property
    def expires_at(self) -> datetime:
        return self.started_at + timedelta(seconds=600)

    @property
    def idle_expires_at(self) -> datetime:
        return min(self._last_user_at + timedelta(seconds=self.idle_seconds), self.expires_at)

    def _require(self, now: datetime) -> datetime:
        now = utc(now)
        if now < self._observed_at:
            raise PermissionError("conversation_clock_invalid")
        self._observed_at = now
        if self._terminal or now >= min(
            self.auth_expires_at, self.expires_at, self.idle_expires_at
        ):
            raise PermissionError("conversation_expired")
        return now

    def mark_ready(self, now: datetime) -> None:
        now = self._require(now)
        if self.ready_at is None:
            if now >= self.admission_deadline_utc:
                raise PermissionError("admission_deadline_exceeded")
            self.ready_at = now

    def require_media(self, now: datetime) -> None:
        self._require(now)
        if self.ready_at is None:
            raise PermissionError("conversation_not_ready")

    def user_activity(self, now: datetime) -> None:
        self._last_user_at = self._require(now)

    def end(self) -> None:
        self._terminal = True

    def cleanup_deadline(self, now: datetime) -> datetime:
        return utc(now) + timedelta(seconds=10)


@dataclass(frozen=True, slots=True)
class WorkBudget:
    work_accepted_at: datetime
    work_deadline_utc: datetime

    @classmethod
    def accept(cls, limits: SessionLimits, now: datetime) -> "WorkBudget":
        now = limits._require(now)
        deadline = min(
            now + timedelta(seconds=10),
            limits.auth_expires_at,
            limits.expires_at,
            limits.idle_expires_at,
        )
        if limits.ready_at is None:
            deadline = min(deadline, limits.admission_deadline_utc)
        if now >= deadline:
            raise PermissionError("work_deadline_exceeded")
        return cls(now, deadline)

    def remaining(self, now: datetime) -> float:
        now = utc(now)
        if now < self.work_accepted_at:
            raise PermissionError("conversation_clock_invalid")
        return max(0.0, (self.work_deadline_utc - now).total_seconds())

    def require(self, now: datetime) -> None:
        if self.remaining(now) <= 0:
            raise PermissionError("work_deadline_exceeded")
