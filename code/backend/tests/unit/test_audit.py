"""Runtime audit ownership and strict metadata-only persistence contracts."""

import json
from datetime import UTC, datetime

import pytest


@pytest.fixture
def record():
    from caller_runtime.domain.audit import AuditRecord

    return AuditRecord(
        id="audit-fixture",
        timestamp=datetime(2026, 10, 26, tzinfo=UTC),
        dayBucket="2026-10-26",
        eventName="call.ended",
        outcome="success",
        sessionRef="session-fixture",
        actorRef=None,
        revisionId="revision-fixture",
        correlationId="correlation-fixture",
        reasonCode="completed",
    )


@pytest.mark.parametrize(
    "field", ["transcript", "prompt", "audio", "arguments", "results", "credential"]
)
def test_audit_rejects_sensitive_or_unknown_fields_without_echoing_values(record, field):
    from caller_runtime.domain.audit import AuditRecord

    value = record.model_dump(mode="json", by_alias=True) | {field: "synthetic-private-content"}
    with pytest.raises(ValueError) as failure:
        AuditRecord.model_validate_json(json.dumps(value))
    assert "synthetic-private-content" not in str(failure.value)


def test_audit_day_bucket_and_timestamp_must_match_and_be_utc(record):
    from caller_runtime.domain.audit import AuditRecord

    value = record.model_dump(mode="json", by_alias=True)
    for change in [
        {"dayBucket": "2026-10-25"},
        {"timestamp": "2026-10-26T00:00:00"},
        {"eventName": "synthetic-private-event"},
        {"reasonCode": "synthetic-private-reason"},
    ]:
        with pytest.raises(ValueError):
            AuditRecord.model_validate_json(json.dumps(value | change))


@pytest.mark.anyio
async def test_local_audit_is_idempotent_detached_and_conflicting_reuse_is_rejected(record):
    from caller_runtime.adapters.audit_repository import LocalAuditRepository

    repository = LocalAuditRepository()
    assert await repository.append(record)
    assert await repository.append(record) is False
    copied = repository.records
    copied.clear()
    assert len(repository.records) == 1
    with pytest.raises(ValueError, match="audit_identity_conflict"):
        await repository.append(record.model_copy(update={"outcome": "error"}))


@pytest.mark.anyio
async def test_repository_revalidates_model_copy_before_any_persistence(record):
    from caller_runtime.adapters.audit_repository import LocalAuditRepository

    repository = LocalAuditRepository()
    with pytest.raises(ValueError):
        await repository.append(
            record.model_copy(update={"reason_code": "synthetic-private-content"})
        )
    assert not repository.records


@pytest.mark.anyio
async def test_cosmos_audit_uses_only_injected_runtime_container_and_day_partition(record):
    from caller_runtime.adapters.audit_repository import CosmosAuditRepository

    class Container:
        bodies = []

        async def create_item(self, *, body, retry_write):
            assert retry_write == 0 and body["dayBucket"] == "2026-10-26"
            self.bodies.append(body)
            return body

    container = Container()
    assert await CosmosAuditRepository(container=container).append(record)
    assert container.bodies == [record.model_dump(mode="json", by_alias=True)]


@pytest.fixture
def anyio_backend():
    return "asyncio"
