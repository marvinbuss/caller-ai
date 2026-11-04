"""Models of the current permission authority and of capability admission attempts.

The authority is one document (at most 128 KiB) listing disabled tools, disabled connections and
revoked assignments with a policy version; every change is conditioned on its ETag, and a stale one
raises `PermissionConflict`. Admission attempts hold fixed metadata only, never inputs or results.
"""

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from management_app.models.capabilities import ConfigurationModel, Identifier, Name

AUTHORITY_LIMIT = 128 * 1024


class PermissionConflict(RuntimeError):
    """The supplied authority ETag no longer owns the requested mutation."""


class RevokedAssignment(ConfigurationModel):
    agent_id: Identifier = Field(alias="agentId")
    tool_id: Identifier = Field(alias="toolId")
    action_name: Name = Field(alias="actionName")


class RevocationRequest(ConfigurationModel):
    disabled_tool_ids: list[Identifier] = Field(alias="disabledToolIds")
    disabled_connection_ids: list[Identifier] = Field(alias="disabledConnectionIds")
    revoked_assignments: list[RevokedAssignment] = Field(alias="revokedAssignments")

    @model_validator(mode="after")
    def unique_sets(self) -> Self:
        revoked = [
            (item.agent_id, item.tool_id, item.action_name) for item in self.revoked_assignments
        ]
        if (
            len(set(self.disabled_tool_ids)) != len(self.disabled_tool_ids)
            or len(set(self.disabled_connection_ids)) != len(self.disabled_connection_ids)
            or len(set(revoked)) != len(revoked)
        ):
            raise ValueError("duplicate_revocation")
        return self


class PermissionAuthority(RevocationRequest):
    id: Literal["security-authority"] = "security-authority"
    organization_id: Literal["default"] = Field(default="default", alias="organizationId")
    policy_version: int = Field(alias="policyVersion", ge=1)
    admission_sequence: int = Field(alias="admissionSequence", ge=0)
    updated_at: AwareDatetime = Field(alias="updatedAt")
    updated_by: Identifier = Field(alias="updatedBy")
    etag: str | None = Field(
        default=None, alias="_etag", min_length=1, max_length=256, exclude=True
    )

    @field_validator("updated_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounded_authority(self) -> Self:
        if len(self.model_dump_json(by_alias=True).encode("utf-8")) > AUTHORITY_LIMIT:
            raise ValueError("permission_authority_too_large")
        return self


class CapabilityReferences(ConfigurationModel):
    request_id: Identifier = Field(alias="requestId")
    session_id: Identifier = Field(alias="sessionId")
    agent_id: Identifier = Field(alias="agentId")
    agent_epoch: int = Field(alias="agentEpoch", ge=1)
    pinned_revision_id: Identifier = Field(alias="pinnedRevisionId")
    tool_id: Identifier = Field(alias="toolId")
    action_id: Name = Field(alias="actionId")


class CapabilityAttempt(CapabilityReferences):
    inputs: dict[str, JsonValue] = Field(repr=False)
    deadline_at: AwareDatetime = Field(alias="deadlineAt", repr=False)
    single_attempt: bool = Field(default=False, alias="singleAttempt", exclude=True)

    @field_validator("deadline_at")
    @classmethod
    def utc_deadline(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class CapabilityAdmission(CapabilityReferences):
    admission_id: Identifier = Field(alias="admissionId")
    admission_sequence: int = Field(alias="admissionSequence", ge=1)
    policy_version: int = Field(alias="policyVersion", ge=1)
    admitted_at: AwareDatetime = Field(alias="admittedAt")
    actor_id: Identifier = Field(alias="actorId")
