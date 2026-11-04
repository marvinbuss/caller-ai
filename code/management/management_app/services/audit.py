"""Audit records and their recovery from committed state.

An `AuditRecord` allows only fixed fields, actions, outcomes and reason codes (no free text, no
configuration or conversation content) and requires a timezone-aware timestamp. A security
revocation record is rebuilt from the committed permission authority (policy version 2 or later),
so a record lost after the commit can be appended again; the same id with the same content is a no-
op.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import Field, StringConstraints, field_validator

from management_app.models.capabilities import ConfigurationModel, Identifier
from management_app.models.permissions import PermissionAuthority

if TYPE_CHECKING:
    from management_app.adapters.configuration_repository import Document


class AuditRecord(ConfigurationModel):
    id: Identifier
    organization_id: Literal["default"] = Field(default="default", alias="organizationId")
    timestamp: datetime
    action: Literal[
        "configuration.publish", "security.revoke", "authorization.check", "capability.denied"
    ]
    outcome: Literal["success", "denied", "error"]
    actor_ref: Identifier
    revision_id: Identifier | None
    correlation_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    reason_code: Literal[
        "revision_committed",
        "permission_denied",
        "policy_revoked",
        "dependency_unavailable",
        "request_rejected",
    ]

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aware_timestamp_required")
        return value.astimezone(UTC)


class AuditRepository(Protocol):
    async def append(self, record: AuditRecord) -> bool: ...


def validate_retention(seconds: int) -> None:
    if type(seconds) is not int or not 3600 <= seconds <= 2147483647:
        raise ValueError("reviewed_audit_retention_required")


def revocation_record(authority: Document) -> AuditRecord:
    value = PermissionAuthority.model_validate_json(json.dumps(authority))
    if value.policy_version < 2:
        raise ValueError("committed_revocation_required")
    return AuditRecord(
        id="audit-security-" + str(value.policy_version),
        timestamp=value.updated_at,
        action="security.revoke",
        outcome="success",
        actor_ref=value.updated_by,
        revision_id=None,
        correlation_id="security-policy-" + str(value.policy_version),
        reason_code="policy_revoked",
    )


class AuditService:
    def __init__(self, *, repository: AuditRepository) -> None:
        self.repository = repository

    async def record(self, record: AuditRecord) -> bool:
        try:
            async with asyncio.timeout(2):
                return await self.repository.append(record)
        except Exception:
            raise RuntimeError("audit_store_unavailable") from None

    async def recover_publication(self, committed_revision: Document) -> bool:
        try:
            revision_id = committed_revision["id"]
            record = AuditRecord(
                id="audit-" + revision_id,
                timestamp=datetime.fromisoformat(committed_revision["createdAt"]),
                action="configuration.publish",
                outcome="success",
                actor_ref=committed_revision["createdBy"],
                revision_id=revision_id,
                correlation_id=revision_id,
                reason_code="revision_committed",
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid_committed_revision_metadata") from None
        return await self.record(record)

    async def recover_security(self, committed_record: Document) -> bool:
        try:
            record = AuditRecord.model_validate_json(json.dumps(committed_record))
            if record.action != "security.revoke" or record.outcome != "success":
                raise ValueError("committed_revocation_required")
        except (ValueError, TypeError):
            raise ValueError("invalid_committed_security_metadata") from None
        return await self.record(record)
