"""Explicit protected-role bootstrap and content-free, recoverable publication audit."""

import asyncio
import copy
import json
from dataclasses import dataclass, field

import pytest
from azure.cosmos.exceptions import CosmosResourceExistsError


@pytest.mark.parametrize("kind", ["local", "cosmos-double"])
def test_bootstrap_is_idempotent_with_stable_protected_ids_and_no_business_admission(
    validator,
    configuration_graph,
    kind,
) -> None:
    from management_app.adapters.configuration_repository import (
        CosmosConfigurationRepository,
        LocalConfigurationRepository,
        graph_document,
    )
    from management_app.api.configuration import ETAG
    from management_app.services.bootstrap import bootstrap, protected_graph
    from tests.integration.test_configuration_persistence import ContainerDouble

    assert protected_graph() == protected_graph()
    assert protected_graph()["schemaVersion"] == "2.1.0"
    container = ContainerDouble() if kind == "cosmos-double" else None
    store = (
        LocalConfigurationRepository(validator=validator)
        if container is None
        else CosmosConfigurationRepository(container=container, validator=validator)
    )

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
        assert snapshot["relationships"] == snapshot["communityConnections"] == []
        assert snapshot["layout"] == {"communities": [], "agentsByCommunity": {}}
        assert first.etag == etag and ETAG.fullmatch(etag)
        graph = graph_document(snapshot)
        graph["agents"].extend(
            copy.deepcopy(agent)
            for agent in configuration_graph["agents"]
            if agent["role"] == "ordinary"
        )
        graph["communities"] = copy.deepcopy(configuration_graph["communities"])
        before = copy.deepcopy(container.items) if container is not None else None
        for condition in ("*", '"stale"'):
            assert await store.publish(graph, if_match=condition, actor_id="manager-one") is None
            assert await store.read_active() == (snapshot, etag)
            if container is not None:
                assert container.items == before
        published = await store.publish(graph, if_match=first.etag, actor_id="manager-one")
        assert published is not None and published[0]["revisionNumber"] == 2
        assert await store.read_revision(snapshot["id"]) == snapshot
        assert (await bootstrap(store)).configuration_ready is True
        assert await store.read_active() == published

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

    async def read_item(self, *, item, partition_key, initial_headers):
        assert initial_headers == {"x-ms-consistency-level": "Strong"}
        assert self.documents[item]["dayBucket"] == partition_key
        return copy.deepcopy(self.documents[item])


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
        LocalAuditRepository()
        if kind == "local"
        else CosmosAuditRepository(container=container, retention_seconds=86400)
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
        expected = {
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
        if kind == "cosmos-double":
            expected.update({"dayBucket", "ttl"})
            assert record["dayBucket"] == snapshot["createdAt"][:10]
            assert record["ttl"] == 86400
        assert set(record) == expected
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


def test_revocation_audit_survives_authority_changes_without_reapplying_mutations(
    validator, capability_graph
):
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.services.audit import AuditService

    async def exercise():
        repository = LocalConfigurationRepository(validator=validator)
        await repository.initialize(capability_graph, actor_id="manager-one")
        before = await repository.read_authority()
        first = await repository.revoke_capabilities(
            {
                "disabledToolIds": ["tool-public"],
                "disabledConnectionIds": [],
                "revokedAssignments": [],
            },
            if_match=before["_etag"],
            actor_id="manager-one",
        )
        original = await repository.read_revocation_audit("audit-security-2")
        assert original["actor_ref"] == "manager-one" and original["action"] == "security.revoke"
        await repository.revoke_capabilities(
            {
                "disabledToolIds": [],
                "disabledConnectionIds": ["connection-one"],
                "revokedAssignments": [],
            },
            if_match=first["_etag"],
            actor_id="manager-two",
        )
        assert await repository.read_revocation_audit("audit-security-2") == original
        second = await repository.read_revocation_audit("audit-security-3")
        assert second["actor_ref"] == "manager-two"
        audit = LocalAuditRepository()
        service = AuditService(repository=audit)
        state = await repository.read_authority()
        assert await service.recover_security(original) is True
        assert await service.recover_security(original) is False
        assert await service.recover_security(second) is True
        await repository.acknowledge_revocation_audit("audit-security-2", retention_seconds=86400)
        assert await repository.read_revocation_audit("audit-security-2") == original
        assert await repository.read_authority() == state
        assert len(audit.records) == 2
        assert "Synthetic approved prompt" not in json.dumps(audit.records)
        assert "secret://" not in json.dumps(audit.records)

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["none", "batch_failed", "lost_commit", "canceled_commit"])
def test_cosmos_revocation_audit_is_atomic_and_recoverable_after_ambiguous_commit(
    validator, capability_graph, monkeypatch, failure
):
    from management_app.adapters.configuration_repository import CosmosConfigurationRepository
    from tests.integration.test_configuration_persistence import ContainerDouble

    async def exercise():
        container = ContainerDouble()
        repository = CosmosConfigurationRepository(container=container, validator=validator)
        await repository.initialize(capability_graph, actor_id="manager-one")
        before = await repository.read_authority()
        mutate = container.execute_item_batch

        async def change(**keywords):
            value = await mutate(**keywords)
            if failure == "lost_commit":
                raise RuntimeError("synthetic-private-lost-commit-reply")
            if failure == "canceled_commit":
                raise asyncio.CancelledError
            return value

        if failure == "batch_failed":
            container.fail_operation = 1
        monkeypatch.setattr(container, "execute_item_batch", change)
        update = {
            "disabledToolIds": ["tool-public"],
            "disabledConnectionIds": [],
            "revokedAssignments": [],
        }
        if failure == "none":
            await repository.revoke_capabilities(
                update, if_match=before["_etag"], actor_id="manager-one"
            )
        else:
            with pytest.raises(
                asyncio.CancelledError if failure == "canceled_commit" else RuntimeError
            ):
                await repository.revoke_capabilities(
                    update, if_match=before["_etag"], actor_id="manager-one"
                )
        current = await repository.read_authority()
        if failure == "batch_failed":
            assert current == before and "audit-security-2" not in container.items
        else:
            assert current["policyVersion"] == 2 and current["disabledToolIds"] == ["tool-public"]
            reconstructed = CosmosConfigurationRepository(container=container, validator=validator)
            record = await reconstructed.read_revocation_audit("audit-security-2")
            assert record["actor_ref"] == "manager-one" and record["action"] == "security.revoke"
            assert container.items["audit-security-2"]["ttl"] == -1
            assert len(container.batches[-1]) == 2
            assert "synthetic-private" not in json.dumps(record)
            if failure == "none":
                await reconstructed.acknowledge_revocation_audit(
                    "audit-security-2", retention_seconds=86400
                )
                assert container.items["audit-security-2"]["ttl"] == 86400
                assert await reconstructed.read_revocation_audit("audit-security-2") == record
                assert await reconstructed.read_authority() == current
                count = len(container.batches)
                await reconstructed.acknowledge_revocation_audit(
                    "audit-security-2", retention_seconds=86400
                )
                assert len(container.batches) == count
                with pytest.raises(RuntimeError):
                    await reconstructed.acknowledge_revocation_audit(
                        "audit-security-2", retention_seconds=172800
                    )
                assert container.items["audit-security-2"]["ttl"] == 86400

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["local", "cosmos-double"])
def test_audit_duplicate_identity_requires_identical_metadata(kind):
    from datetime import UTC, datetime

    from management_app.adapters.audit_repository import CosmosAuditRepository, LocalAuditRepository
    from management_app.services.audit import AuditRecord

    original = AuditRecord(
        id="audit-identity-fixture",
        timestamp=datetime.now(UTC),
        action="authorization.check",
        outcome="denied",
        actor_ref="anonymous",
        revision_id=None,
        correlation_id="correlation-fixture",
        reason_code="permission_denied",
    )

    async def exercise():
        container = AuditContainerDouble()
        repository = (
            LocalAuditRepository()
            if kind == "local"
            else CosmosAuditRepository(container=container, retention_seconds=86400)
        )
        assert await repository.append(original) is True
        assert await repository.append(original) is False
        with pytest.raises(RuntimeError, match="audit_identity_conflict"):
            await repository.append(original.model_copy(update={"actor_ref": "different-manager"}))
        records = repository.records if kind == "local" else container.documents
        assert records[original.id]["actor_ref"] == "anonymous"

    asyncio.run(exercise())


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
