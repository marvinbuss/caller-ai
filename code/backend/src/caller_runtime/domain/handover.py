"""Immutable handover outcome metadata; transferred content is never durable data."""

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from caller_runtime.domain.session import OpaqueId

HandoverOutcome = Literal["requested", "committed", "failed", "canceled"]
HandoverReason = Literal[
    "requested",
    "completed",
    "destination_unavailable",
    "deadline_exceeded",
    "caller_disconnected",
    "policy_rejected",
    "owner_lost",
    "transfer_in_progress",
    "loop_limited",
    "store_unavailable",
]


class HandoverRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, hide_input_in_errors=True
    )

    handover_id: OpaqueId = Field(alias="handoverId")
    session_id: OpaqueId = Field(alias="sessionId")
    configuration_revision_id: OpaqueId = Field(alias="configurationRevisionId")
    physical_owner_id: OpaqueId = Field(alias="physicalOwnerId")
    connection_epoch: int = Field(alias="connectionEpoch", ge=1, strict=True)
    source_agent_id: OpaqueId = Field(alias="sourceAgentId")
    destination_agent_id: OpaqueId = Field(alias="destinationAgentId")
    source_agent_epoch: int = Field(alias="sourceAgentEpoch", ge=0, strict=True)
    destination_agent_epoch: int = Field(alias="destinationAgentEpoch", ge=1, strict=True)
    requested_at: AwareDatetime = Field(alias="requestedAt")
    completed_at: AwareDatetime | None = Field(default=None, alias="completedAt")
    outcome: HandoverOutcome = "requested"
    reason_code: HandoverReason = Field(default="requested", alias="reasonCode")

    @field_validator("requested_at", "completed_at")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def ordered_metadata(self) -> Self:
        if self.source_agent_id == self.destination_agent_id:
            raise ValueError("handover_destination_must_differ")
        if self.destination_agent_epoch != self.source_agent_epoch + 1:
            raise ValueError("invalid_handover_epoch")
        if self.outcome == "requested":
            if self.completed_at is not None or self.reason_code != "requested":
                raise ValueError("invalid_pending_handover")
        elif self.completed_at is None or self.completed_at < self.requested_at:
            raise ValueError("invalid_handover_completion")
        if (self.outcome == "committed") != (self.reason_code == "completed"):
            raise ValueError("invalid_handover_outcome_reason")
        if self.outcome != "requested" and self.reason_code == "requested":
            raise ValueError("invalid_handover_outcome_reason")
        return self

    def to_document(self) -> dict[str, object]:
        return {"id": "handover-" + self.handover_id, **self.model_dump(mode="json", by_alias=True)}
