"""The one-time `apply_default_prompts` command (FR-013, research R7)."""

import asyncio
import copy

import pytest


@pytest.mark.parametrize("kind", ["local", "cosmos-double"])
def test_apply_default_prompts_publishes_once_and_changes_only_protected_prompts(
    validator, configuration_graph, kind
):
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.adapters.configuration_repository import (
        CosmosConfigurationRepository,
        LocalConfigurationRepository,
        graph_document,
        verified_revision,
    )
    from management_app.services.apply_default_prompts import (
        ACTOR,
        DEFAULTS,
        apply_default_prompts,
    )
    from management_app.services.audit import AuditService
    from tests.integration.test_configuration_persistence import ContainerDouble

    store = (
        LocalConfigurationRepository(validator=validator)
        if kind == "local"
        else CosmosConfigurationRepository(container=ContainerDouble(), validator=validator)
    )
    records = LocalAuditRepository()
    audit = AuditService(repository=records)
    graph = copy.deepcopy(configuration_graph)
    for agent in graph["agents"]:
        if agent["role"] in DEFAULTS:
            agent["prompt"] = "Synthetic edited " + agent["role"] + " prompt"

    async def scenario():
        initial, _etag = await store.initialize(graph, actor_id="manager-one")
        number = await apply_default_prompts(store, audit)
        assert number == initial["revisionNumber"] + 1
        published, etag = await store.read_active()
        assert published["revisionNumber"] == number and published["createdBy"] == ACTOR
        before, after = graph_document(initial), graph_document(published)
        for agent in after["agents"]:
            if agent["role"] in DEFAULTS:
                assert agent["prompt"] == DEFAULTS[agent["role"]]
                agent["prompt"] = next(
                    item["prompt"]
                    for item in before["agents"]
                    if item["agentId"] == agent["agentId"]
                )
        # Every other field, and the stored shape, is unchanged.
        assert after == before
        assert set(published) == set(initial)
        # The published revision verifies, and its verified form verifies again.
        assert verified_revision(verified_revision(published)) == verified_revision(published)
        assert [record["actor_ref"] for record in records.records.values()] == [ACTOR]
        assert await apply_default_prompts(store, audit) is None
        assert await store.read_active() == (published, etag)
        assert len(records.records) == 1

    asyncio.run(scenario())


def test_apply_default_prompts_refuses_an_uninitialized_store(validator):
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.services.apply_default_prompts import apply_default_prompts
    from management_app.services.audit import AuditService

    store = LocalConfigurationRepository(validator=validator)
    audit = AuditService(repository=LocalAuditRepository())
    with pytest.raises(RuntimeError, match="configuration_not_initialized"):
        asyncio.run(apply_default_prompts(store, audit))
