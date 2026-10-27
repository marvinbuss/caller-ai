"""Allowlisted runtime audit metadata only; no caller, model or capability payload fields."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]


class AuditRecord(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", populate_by_name=True, hide_input_in_errors=True
    )
    id: Reference
    timestamp: datetime
    day_bucket: str = Field(alias="dayBucket", pattern=r"^\d{4}-\d{2}-\d{2}$")
    event_name: Literal[
        "call.admitted",
        "call.ended",
        "handover.requested",
        "handover.committed",
        "handover.failed",
        "capability.denied",
        "media.rejected",
    ] = Field(alias="eventName")
    outcome: Literal["success", "denied", "error"]
    actor_ref: Reference | None = Field(default=None, alias="actorRef")
    session_ref: Reference | None = Field(default=None, alias="sessionRef")
    revision_id: Reference | None = Field(default=None, alias="revisionId")
    correlation_id: str = Field(alias="correlationId", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    reason_code: Literal[
        "admitted",
        "completed",
        "caller_disconnected",
        "operation_failed",
        "deadline_exceeded",
        "owner_lost",
        "provider_unavailable",
        "policy_rejected",
        "handover_committed",
        "dependency_unavailable",
    ] = Field(alias="reasonCode")

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aware_audit_timestamp_required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def valid_partition(self) -> Self:
        if self.day_bucket != self.timestamp.date().isoformat() or not (
            self.actor_ref or self.session_ref
        ):
            raise ValueError("invalid_audit_partition_or_reference")
        return self
