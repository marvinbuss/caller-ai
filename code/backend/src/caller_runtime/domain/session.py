"""Call ownership metadata and separate non-serializable volatile call content."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

OpaqueId = Annotated[str, Field(min_length=1, max_length=128, strict=True)]
TerminalReason = Literal[
    "completed",
    "caller_disconnected",
    "operation_failed",
    "deadline_exceeded",
    "owner_lost",
    "provider_unavailable",
    "policy_rejected",
]


class LifecycleState(StrEnum):
    ADMITTED = "Admitted"
    INTRODUCING = "Introducing"
    ROUTING = "Routing"
    ACTIVE = "Active"
    TRANSFERRING = "Transferring"
    CLOSING = "Closing"
    ENDED = "Ended"


TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.ADMITTED: frozenset({LifecycleState.INTRODUCING, LifecycleState.ENDED}),
    LifecycleState.INTRODUCING: frozenset({LifecycleState.ROUTING, LifecycleState.ENDED}),
    LifecycleState.ROUTING: frozenset(
        {LifecycleState.ACTIVE, LifecycleState.CLOSING, LifecycleState.ENDED}
    ),
    LifecycleState.ACTIVE: frozenset(
        {LifecycleState.TRANSFERRING, LifecycleState.CLOSING, LifecycleState.ENDED}
    ),
    LifecycleState.TRANSFERRING: frozenset({LifecycleState.ACTIVE, LifecycleState.ENDED}),
    LifecycleState.CLOSING: frozenset({LifecycleState.ENDED}),
    LifecycleState.ENDED: frozenset(),
}


class CallSession(BaseModel):
    """Immutable metadata snapshot; no conversation fields or live SDK objects."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, hide_input_in_errors=True
    )

    session_id: OpaqueId = Field(alias="sessionId")
    provider_call_ref: str = Field(
        alias="providerCallRef", min_length=1, max_length=256, strict=True
    )
    configuration_revision_id: OpaqueId = Field(alias="configurationRevisionId")
    physical_owner_id: OpaqueId = Field(alias="physicalOwnerId")
    connection_epoch: int = Field(alias="connectionEpoch", ge=1, strict=True)
    agent_epoch: int = Field(default=0, alias="agentEpoch", ge=0, strict=True)
    active_agent_id: OpaqueId | None = Field(default=None, alias="activeAgentId")
    active_community_id: OpaqueId | None = Field(default=None, alias="activeCommunityId")
    state: LifecycleState = LifecycleState.ADMITTED
    lease_until: AwareDatetime = Field(alias="leaseUntil")
    started_at: AwareDatetime = Field(alias="startedAt")
    terminated_at: AwareDatetime | None = Field(default=None, alias="terminatedAt")
    terminal_reason_code: TerminalReason | None = Field(default=None, alias="terminalReasonCode")
    etag: str | None = Field(default=None, alias="_etag", min_length=1, max_length=256)

    @field_validator("lease_until", "started_at", "terminated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_lifecycle_metadata(self) -> Self:
        if self.lease_until < self.started_at:
            raise ValueError("lease_precedes_session_start")
        if self.state == LifecycleState.ENDED:
            if self.terminated_at is None or self.terminal_reason_code is None:
                raise ValueError("terminal_session_requires_metadata")
            if self.terminated_at < self.started_at:
                raise ValueError("termination_precedes_session_start")
        elif self.terminated_at is not None or self.terminal_reason_code is not None:
            raise ValueError("active_session_has_terminal_metadata")
        return self

    def to_document(self) -> dict[str, Any]:
        """Return only owned Cosmos metadata; server ETags are not document writes."""
        document = self.model_dump(mode="json", by_alias=True, exclude={"etag"})
        return {"id": self.session_id, **document}

    def transition(
        self,
        target: LifecycleState,
        *,
        at: datetime | None = None,
        terminal_reason: TerminalReason | None = None,
    ) -> Self:
        """Create a validated next snapshot; terminal sessions cannot transition."""
        if target not in TRANSITIONS[self.state]:
            raise ValueError("invalid_session_transition")
        values = self.model_dump()
        values["state"] = target
        if target == LifecycleState.ENDED:
            if at is None or terminal_reason is None:
                raise ValueError("terminal_transition_requires_metadata")
            values["terminated_at"] = at
            values["terminal_reason_code"] = terminal_reason
        elif terminal_reason is not None:
            raise ValueError("nonterminal_transition_has_terminal_reason")
        return type(self).model_validate(values)


class CapacityRecoveryBinding(BaseModel):
    """Immutable cleanup metadata; not physical ownership or proof of disposal."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    session_id: OpaqueId
    physical_owner_id: OpaqueId
    connection_epoch: int = Field(strict=True, ge=1)
    model_reservation: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{32}$")] | None = None


@dataclass(slots=True, repr=False)
class VolatileCallContext:
    """Call-local content only; never put this object in a durable repository."""

    audio_buffers: list[bytearray] = field(default_factory=list)
    conversation: list[dict[str, Any]] = field(default_factory=list)
    request_summary: str = ""
    transfer_reason: str = ""
    tool_results: list[Any] = field(default_factory=list)

    def purge(self) -> None:
        """Release owned references and overwrite retained mutable audio, idempotently."""
        for audio in self.audio_buffers:
            audio[:] = b"\x00" * len(audio)
        for message in self.conversation:
            message.clear()
        for result in self.tool_results:
            if isinstance(result, (dict, list, bytearray)):
                result.clear()
        self.audio_buffers.clear()
        self.conversation.clear()
        self.tool_results.clear()
        self.request_summary = ""
        self.transfer_reason = ""
