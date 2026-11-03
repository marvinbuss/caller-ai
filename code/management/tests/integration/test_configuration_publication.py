"""US4 publication preserves immutable revisions and immediate current permission ordering."""

import asyncio
import copy
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture(params=["local", "cosmos-double"])
def publication_store(request, validator):
    from management_app.adapters import configuration_repository
    from management_app.services.configuration_publication import publication
    from tests.integration.test_configuration_persistence import ContainerDouble

    assert configuration_repository.publication is publication
    if request.param == "local":
        return configuration_repository.LocalConfigurationRepository(validator=validator), None
    container = ContainerDouble()
    return configuration_repository.CosmosConfigurationRepository(
        container=container, validator=validator
    ), container


@pytest.fixture
def cosmos_publication_store(validator):
    from management_app.adapters.configuration_repository import CosmosConfigurationRepository
    from tests.integration.test_configuration_persistence import ContainerDouble

    container = ContainerDouble()
    return CosmosConfigurationRepository(container=container, validator=validator), container


def test_twenty_publications_retain_old_revisions_and_reject_stale_manager_edits(
    publication_store, configuration_graph
):
    repository, container = publication_store

    async def scenario():
        original, original_etag = await repository.initialize(
            configuration_graph, actor_id="manager-fixture"
        )
        etag = original_etag
        for number in range(2, 22):
            graph = copy.deepcopy(configuration_graph)
            graph["agents"][3]["prompt"] += f" revision {number}"
            saved = await repository.publish(graph, if_match=etag, actor_id="manager-fixture")
            assert saved is not None and saved[0]["revisionNumber"] == number
            assert await repository.read_revision(original["id"]) == original
            assert (
                await repository.publish(graph, if_match=original_etag, actor_id="other-manager")
                is None
            )
            etag = saved[1]
        assert (await repository.read_active())[0]["revisionNumber"] == 21

    asyncio.run(scenario())


def test_save_revocation_is_effective_for_the_next_pinned_gateway_attempt(
    publication_store, capability_graph, validator
):
    from management_app.models.capabilities import ApprovedCapabilities
    from management_app.models.permissions import CapabilityAttempt
    from management_app.observability import Telemetry
    from management_app.services.capability_gateway import CapabilityGateway, GatewayError
    from management_app.services.capability_policy import CapabilityPolicy

    repository, container = publication_store

    class Transport:
        effects = 0

        async def execute(self, selected, *, request_id, deadline):
            self.effects += 1
            return {"name": "Synthetic public result"}

    transport, telemetry = Transport(), Telemetry("management", "local")
    gateway = CapabilityGateway(
        repository=repository,
        policy=CapabilityPolicy(
            reviewed=ApprovedCapabilities.from_reviewed(capability_graph),
            destinations={"fixture-public-endpoint": "https://public.example.invalid/rpc"},
            detector=validator.detector,
        ),
        transport=transport,
        telemetry=telemetry,
    )

    async def scenario():
        original, etag = await repository.initialize(capability_graph, actor_id="manager-fixture")
        attempt = CapabilityAttempt(
            requestId="request-fixture",
            sessionId="session-fixture",
            agentId="agent-alpha",
            agentEpoch=1,
            pinnedRevisionId=original["id"],
            toolId="tool-public",
            actionId="lookup",
            inputs={},
            deadlineAt=datetime.now(UTC) + timedelta(seconds=10),
        )
        assert (await gateway.execute(attempt, actor_id="runtime-fixture"))["result"][
            "name"
        ] == "Synthetic public result"
        graph = copy.deepcopy(capability_graph)
        graph["agents"][3]["capabilityAssignments"] = []
        saved = await repository.publish(graph, if_match=etag, actor_id="manager-fixture")
        assert saved is not None and saved[0]["id"] != original["id"]
        with pytest.raises(GatewayError) as failure:
            await gateway.execute(
                attempt.model_copy(update={"request_id": "request-next"}),
                actor_id="runtime-fixture",
            )
        assert failure.value.status == 403 and transport.effects == 1
        assert await repository.read_revision(original["id"]) == original

    try:
        asyncio.run(scenario())
    finally:
        telemetry.close()


def test_authority_only_admission_race_retries_without_losing_sequence_or_pointer(
    cosmos_publication_store, capability_graph
):
    repository, container = cosmos_publication_store

    async def scenario():
        original, etag = await repository.initialize(capability_graph, actor_id="manager-fixture")
        container.authority_conflict = True
        graph = copy.deepcopy(capability_graph)
        graph["agents"][3]["capabilityAssignments"] = []
        saved = await repository.publish(graph, if_match=etag, actor_id="manager-fixture")
        assert saved is not None and saved[0]["revisionNumber"] == 2
        authority = await repository.read_authority()
        assert authority["admissionSequence"] == 1 and authority["policyVersion"] == 2
        assert len(authority["revokedAssignments"]) == 1
        assert await repository.read_revision(original["id"]) == original

    asyncio.run(scenario())


def test_failed_authority_batch_never_partially_publishes_revision_or_pointer(
    cosmos_publication_store, configuration_graph
):
    repository, container = cosmos_publication_store

    async def scenario():
        before = await repository.initialize(configuration_graph, actor_id="manager-fixture")
        documents = copy.deepcopy(container.items)
        container.fail_operation = 2
        graph = copy.deepcopy(configuration_graph)
        graph["agents"][3]["prompt"] += " rejected save"
        with pytest.raises(RuntimeError, match="configuration_store_unavailable"):
            await repository.publish(graph, if_match=before[1], actor_id="manager-fixture")
        assert container.items == documents and await repository.read_active() == before

    asyncio.run(scenario())
