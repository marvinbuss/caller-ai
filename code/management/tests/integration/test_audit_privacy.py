"""Rejected sensitive configuration cannot partially persist into revisions or audit metadata."""

import asyncio
import json
from datetime import UTC, datetime

import pytest


def test_rejected_literal_credential_preserves_revision_and_safe_audit(
    configuration_graph, validator
):
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.adapters.configuration_repository import (
        ConfigurationRejected,
        LocalConfigurationRepository,
    )
    from management_app.services.audit import AuditRecord

    async def scenario():
        repository, audit = (
            LocalConfigurationRepository(validator=validator),
            LocalAuditRepository(),
        )
        before = await repository.initialize(configuration_graph, actor_id="manager-fixture")
        rejected = json.loads(json.dumps(configuration_graph))
        rejected["agents"][3]["prompt"] = "Bearer " + "synthetic-sensitive-candidate"
        with pytest.raises(ConfigurationRejected) as failure:
            await repository.publish(rejected, if_match=before[1], actor_id="manager-fixture")
        assert await repository.read_active() == before
        assert "synthetic-sensitive-candidate" not in str(failure.value)
        record = AuditRecord(
            id="audit-privacy-fixture",
            timestamp=datetime.now(UTC),
            action="authorization.check",
            outcome="denied",
            actor_ref="manager-fixture",
            revision_id=before[0]["id"],
            correlation_id="correlation-fixture",
            reason_code="permission_denied",
        )
        assert await audit.append(record)
        assert "synthetic-sensitive-candidate" not in json.dumps(audit.records)

    asyncio.run(scenario())
