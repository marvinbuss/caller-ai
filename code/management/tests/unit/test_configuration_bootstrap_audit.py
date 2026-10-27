"""Explicit protected-role bootstrap and content-free, recoverable publication audit."""

import asyncio
import copy
import json
from dataclasses import dataclass, field

import pytest
from azure.cosmos.exceptions import CosmosResourceExistsError


def test_bootstrap_is_idempotent_with_stable_protected_ids_and_no_business_admission(
    validator,
) -> None:
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.services.bootstrap import bootstrap, protected_graph

    assert protected_graph() == protected_graph()
    store = LocalConfigurationRepository(validator=validator)

    async def scenario():
        first, second = await asyncio.gather(bootstrap(store), bootstrap(store))
        assert first == second and first.configuration_ready is False
        snapshot, etag = await store.read_active()
        assert len(snapshot["agents"]) == 3 and not snapshot["communities"]
        assert {agent["role"] for agent in snapshot["agents"]} == {"welcome", "routing", "goodbye"}
        assert all(
            agent["enabled"] and agent["communityId"] is None for agent in snapshot["agents"]
        )
        assert snapshot["revisionNumber"] == 1

    asyncio.run(scenario())


def test_bootstrap_does_not_replace_a_managers_existing_graph(
    validator, configuration_graph
) -> None:
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.services.bootstrap import bootstrap

    store = LocalConfigurationRepository(validator=validator)

    async def scenario():
        original = await store.initialize(configuration_graph, actor_id="manager-one")
        result = await bootstrap(store)
        assert result.configuration_ready is True
        assert await store.read_active() == original

    asyncio.run(scenario())


@dataclass
class AuditContainerDouble:
    documents: dict = field(default_factory=dict)

    async def create_item(self, *, body, retry_write):
        assert retry_write == 0 and body["organizationId"] == "default"
        if body["id"] in self.documents:
            raise CosmosResourceExistsError(status_code=409)
        self.documents[body["id"]] = copy.deepcopy(body)
        return copy.deepcopy(body)


@pytest.mark.parametrize("kind", ["local", "cosmos-double"])
def test_publication_audit_recovers_idempotently_without_prompts_or_tools(
    validator, capability_graph, kind
) -> None:
    from management_app.adapters.audit_repository import CosmosAuditRepository, LocalAuditRepository
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.services.audit import AuditService

    store = LocalConfigurationRepository(validator=validator)
    container = AuditContainerDouble()
    audit = (
        LocalAuditRepository() if kind == "local" else CosmosAuditRepository(container=container)
    )
    service = AuditService(repository=audit)

    async def scenario():
        snapshot, etag = await store.initialize(capability_graph, actor_id="manager-one")
        before = copy.deepcopy(snapshot)
        first = await service.recover_publication(snapshot)
        repeated = await service.recover_publication(snapshot)
        assert first is True and repeated is False
        assert snapshot == before
        records = (
            list(audit.records.values()) if kind == "local" else list(container.documents.values())
        )
        assert len(records) == 1
        record = records[0]
        assert set(record) == {
            "id",
            "organizationId",
            "timestamp",
            "action",
            "outcome",
            "actor_ref",
            "revision_id",
            "correlation_id",
            "reason_code",
        }
        assert record["revision_id"] == snapshot["id"] and record["actor_ref"] == "manager-one"
        serialized = json.dumps(records)
        assert "Synthetic approved prompt" not in serialized and "lookup" not in serialized
        assert "secret://" not in serialized

    asyncio.run(scenario())


def test_security_audit_rejects_content_bearing_fields() -> None:
    from pydantic import ValidationError

    from management_app.services.audit import AuditRecord

    with pytest.raises(ValidationError):
        AuditRecord.model_validate(
            {"prompt": "Synthetic rejected content", "action": "unrestricted-action"}
        )


def test_audit_failure_does_not_revert_committed_revision_and_can_be_recovered(
    validator, configuration_graph
) -> None:
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.services.audit import AuditService

    class FailingAudit:
        async def append(self, record):
            raise RuntimeError("synthetic-private-audit-details")

    async def scenario():
        store = LocalConfigurationRepository(validator=validator)
        committed = await store.initialize(configuration_graph, actor_id="manager-one")
        with pytest.raises(RuntimeError, match="^audit_store_unavailable$"):
            await AuditService(repository=FailingAudit()).recover_publication(committed[0])
        assert await store.read_active() == committed
        assert await AuditService(repository=LocalAuditRepository()).recover_publication(
            committed[0]
        )

    asyncio.run(scenario())
