"""Strict browser-test control metadata; content and signed permits are never console state.

The models carry only identifiers, modes, digests, timestamps and status words between the
browser, the console state and the backend permit: a ticket request, the authenticate message,
the start permit (with the original startup budget and the actor and nonce keys) and the console
snapshot. All are strict and frozen, reject unknown fields, and keep serialized metadata under
8 KiB, so conversation text, audio and signed permits cannot end up in console state.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Mode = Literal["text", "speech"]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_test_time_required")
    return value.astimezone(UTC)


AwareTime = Annotated[datetime, AfterValidator(utc)]


class TestMetadata(BaseModel):
    __test__ = False
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, populate_by_name=True, hide_input_in_errors=True
    )

    @model_validator(mode="after")
    def bounded_metadata(self) -> Self:
        if len(self.model_dump_json(by_alias=True).encode("utf-8")) > 8192:
            raise ValueError("test_control_limit")
        return self


class TicketRequest(TestMetadata):
    mode: Mode
    request_id: Identifier = Field(alias="requestId")


class Authenticate(TestMetadata):
    type: Literal["authenticate"]
    permit: str = Field(min_length=1, max_length=6144, repr=False)
    request_id: Identifier = Field(alias="requestId")


class StartPermit(TestMetadata):
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


class TicketResponse(TestMetadata):
    permit: str = Field(min_length=1, max_length=6144, repr=False)
    mode: Mode
    request_id: Identifier = Field(alias="requestId")
    socket_path: Literal["/v1/console/sessions"] = Field(alias="socketPath")
    expires_at: AwareTime = Field(alias="expiresAt")
    admission_deadline_utc: AwareTime = Field(alias="admissionDeadlineUtc")


class ConsoleSnapshot(TestMetadata):
    status: Literal[
        "idle",
        "permission",
        "starting",
        "ready",
        "stopping",
        "ended",
        "unavailable",
        "busy",
        "cleanup_pending",
    ] = "idle"
    mode: Mode = "text"
    session_id: Identifier | None = Field(default=None, alias="sessionId")
    revision_id: Identifier | None = Field(default=None, alias="revisionId")
    revision_number: int | None = Field(default=None, alias="revisionNumber", ge=1)
    agent_id: Identifier | None = Field(default=None, alias="agentId")
    community_id: Identifier | None = Field(default=None, alias="communityId")
    generation: int = Field(default=1, ge=1, le=2**32 - 1)
    expires_at: AwareTime | None = Field(default=None, alias="expiresAt")
    idle_expires_at: AwareTime | None = Field(default=None, alias="idleExpiresAt")
    cleanup_status: Literal["active", "complete", "pending"] | None = Field(
        default=None, alias="cleanupStatus"
    )
    code: (
        Literal[
            "stopped",
            "manager_busy",
            "unavailable",
            "permission_denied",
            "invalid_input",
            "auth_expired",
            "idle_expired",
            "lifetime_expired",
            "protocol_error",
            "quota",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def trusted_ready(self) -> Self:
        if self.status == "ready" and any(
            value is None
            for value in (
                self.session_id,
                self.revision_id,
                self.revision_number,
                self.agent_id,
                self.expires_at,
                self.idle_expires_at,
            )
        ):
            raise ValueError("trusted_ready_metadata_required")
        if (
            self.expires_at is not None
            and self.idle_expires_at is not None
            and self.idle_expires_at > self.expires_at
        ):
            raise ValueError("invalid_console_expiry")
        return self
