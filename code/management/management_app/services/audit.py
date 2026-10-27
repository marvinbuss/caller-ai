"""Allowlisted audit metadata and retryable recovery from committed revision metadata."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field, StringConstraints, field_validator

from management_app.adapters.configuration_repository import Document
from management_app.models.capabilities import ConfigurationModel, Identifier


class AuditRecord(ConfigurationModel):
    id: Identifier
    organization_id: Literal["default"] = Field(default="default", alias="organizationId")
    timestamp: datetime
    action: Literal["configuration.publish", "security.revoke", "authorization.check"]
    outcome: Literal["success", "denied", "error"]
    actor_ref: Identifier
    revision_id: Identifier | None
    correlation_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    reason_code: Literal[
        "revision_committed", "permission_denied", "policy_revoked", "dependency_unavailable"
    ]

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aware_timestamp_required")
        return value.astimezone(UTC)


class AuditRepository(Protocol):
    async def append(self, record: AuditRecord) -> bool: ...


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
