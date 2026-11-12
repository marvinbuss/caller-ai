"""Local/SDK-double publication proofs; not live Cosmos durability acceptance."""

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from azure.cosmos.exceptions import CosmosBatchOperationError, CosmosResourceNotFoundError
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

US2_REAL_COSMOS_PROFILE = {
    "profileId": "us2-real-cosmos-v1",
    "status": "defined_not_executed",
    "defaultEnabled": False,
    "activation": {
        "explicitOptIn": "us2-real-cosmos",
        "requiredTasks": ["T099", "T112", "T117", "T118"],
        "environmentEvidence": "docs/acceptance/live-environment.md",
        "runner": "code/tools/scripts/verify_release.py",
        "runnerImplementedBy": "T112",
    },
    "target": {
        "nonProductionOnly": True,
        "dedicatedApprovedContainerRequired": True,
        "emptyTargetRequired": True,
        "dataOwner": "management",
        "partitionKey": "/organizationId",
        "organizationId": "default",
        "consistency": "Strong",
        "identity": "workload_identity",
        "privateNetworkingRequired": True,
        "syntheticDataOnly": True,
        "automaticProvisioning": False,
        "automaticCleanup": False,
    },
    "scenarios": [
        "publish_complete_revision_pointer_and_authority",
        "reject_conflicting_and_invalid_edits_without_partial_commit",
        "restart_management_without_losing_saved_revisions",
        "restart_runtime_and_retrieve_snapshot_through_management_api",
        "prove_rejected_or_partial_revisions_are_never_activated",
        "verify_idempotent_metadata_only_audit_recovery",
    ],
    "requiredEvidence": [
        "approved_environment_and_image_references",
        "observed_account_Strong_consistency_and_private_access",
        "stable_revision_id_number_graphHash_and_authority_after_restart",
        "accepted_and_rejected_manager_ETag_outcomes",
        "runtime_snapshot_selection_through_authenticated_management_api",
        "redacted_test_results_and_reviewed_cleanup_disposition",
    ],
    "liveCriterion": "SC-012",
    "unavailableOutcome": "blocked",
    "excludedCriteria": ["SC-008"],
    "evidenceDestination": "docs/acceptance/us2.md",
}


def test_real_cosmos_profile_is_declarative_opt_in_and_keeps_live_evidence_pending() -> None:
    profile = json.loads(json.dumps(US2_REAL_COSMOS_PROFILE))
    assert profile["status"] == "defined_not_executed" and profile["defaultEnabled"] is False
    assert {"T099", "T112", "T117", "T118"} <= set(profile["activation"]["requiredTasks"])
    assert profile["target"]["consistency"] == "Strong"
    assert profile["target"]["organizationId"] == "default"
    assert profile["target"]["partitionKey"] == "/organizationId"
    assert profile["target"]["identity"] == "workload_identity"
    assert all(
        profile["target"][name] is True
        for name in (
            "nonProductionOnly",
            "dedicatedApprovedContainerRequired",
            "emptyTargetRequired",
            "privateNetworkingRequired",
            "syntheticDataOnly",
        )
    )
    assert (
        profile["target"]["automaticProvisioning"] is profile["target"]["automaticCleanup"] is False
    )
    assert profile["liveCriterion"] == "SC-012" and profile["unavailableOutcome"] == "blocked"
    assert profile["excludedCriteria"] == ["SC-008"]
    assert len(profile["scenarios"]) == 6 and len(profile["requiredEvidence"]) == 6


@dataclass
class ContainerDouble:
    items: dict = field(default_factory=dict)
    batches: list = field(default_factory=list)
    sequence: int = 0
    fail_operation: int | None = None
    authority_conflict: bool = False

    async def read_item(self, *, item, partition_key, initial_headers):
        assert partition_key == "default"
        assert initial_headers == {"x-ms-consistency-level": "Strong"}
        await asyncio.sleep(0)
        if item not in self.items:
            raise CosmosResourceNotFoundError(
                status_code=404, message="synthetic-private-storage-details"
            )
        return copy.deepcopy(self.items[item])

    async def execute_item_batch(self, *, batch_operations, partition_key, retry_write):
        assert partition_key == "default" and retry_write == 0
        if self.authority_conflict and "security-authority" in self.items:
            self.authority_conflict = False
            self.items["security-authority"]["admissionSequence"] += 1
            self.items["security-authority"]["_etag"] = '"concurrent-admission"'
        candidate = copy.deepcopy(self.items)
        responses = []
        for index, operation in enumerate(batch_operations):
            action, arguments = operation[:2]
            options = operation[2] if len(operation) == 3 else {}
            document = copy.deepcopy(arguments[0] if action == "create" else arguments[1])
            identifier = document["id"]
            status = 201 if action == "create" else 200
            if index == self.fail_operation:
                status = 500
            elif action == "create" and identifier in candidate:
                status = 409
            elif (
                action == "replace"
                and options.get("if_match_etag") != candidate[identifier]["_etag"]
            ):
                status = 412
            if status >= 400:
                raise CosmosBatchOperationError(
                    error_index=index,
                    status_code=status,
                    headers={},
                    message="synthetic-private-storage-details",
                )
            document["_etag"] = f'"fixture-{self.sequence + index + 1}"'
            candidate[identifier] = document
            responses.append(
                {
                    "statusCode": status,
                    "eTag": document["_etag"],
                    "resourceBody": copy.deepcopy(document),
                }
            )
        self.items = candidate
        self.sequence += len(batch_operations)
        self.batches.append(copy.deepcopy(batch_operations))
        return responses


@pytest.fixture(params=["local", "cosmos-double"])
def repository(request, validator):
    from management_app.adapters.configuration_repository import (
        CosmosConfigurationRepository,
        LocalConfigurationRepository,
    )

    if request.param == "local":
        return LocalConfigurationRepository(validator=validator), None
    container = ContainerDouble()
    return CosmosConfigurationRepository(container=container, validator=validator), container


def test_conditional_initialization_is_idempotent_and_detached(
    repository, configuration_graph
) -> None:
    store, container = repository

    async def scenario():
        assert await store.read_active() is None
        first = await store.initialize(configuration_graph, actor_id="bootstrap")
        repeated = await store.initialize(configuration_graph, actor_id="bootstrap")
        assert first == repeated
        first[0]["agents"][0]["prompt"] = "Changed caller-owned copy"
        latest = await store.read_active()
        assert latest[0]["agents"][0]["prompt"] == "Synthetic approved prompt"

    asyncio.run(scenario())


def test_capability_admission_and_revocation_share_conditional_authority(
    repository, capability_graph
):
    from management_app.models.permissions import CapabilityAttempt, PermissionConflict

    store, container = repository

    async def scenario():
        snapshot, pointer = await store.initialize(capability_graph, actor_id="manager-fixture")
        authority = await store.read_authority()
        attempt = CapabilityAttempt(
            requestId="request-store-fixture",
            sessionId="session-store-fixture",
            agentId="agent-alpha",
            agentEpoch=1,
            pinnedRevisionId=snapshot["id"],
            toolId="tool-public",
            actionId="lookup",
            inputs={},
            deadlineAt=datetime.now(UTC) + timedelta(seconds=10),
        )
        admission = await store.admit_capability(
            attempt, if_match=authority["_etag"], actor_id="runtime-fixture"
        )
        current = await store.read_authority()
        assert current["admissionSequence"] == admission.admission_sequence == 1
        assert await store.read_active() == (snapshot, pointer)
        with pytest.raises(PermissionConflict):
            await store.revoke_capabilities(
                {
                    "disabledToolIds": ["tool-public"],
                    "disabledConnectionIds": [],
                    "revokedAssignments": [],
                },
                if_match=authority["_etag"],
                actor_id="manager-fixture",
            )
        revoked = await store.revoke_capabilities(
            {
                "disabledToolIds": ["tool-public"],
                "disabledConnectionIds": [],
                "revokedAssignments": [],
            },
            if_match=current["_etag"],
            actor_id="manager-fixture",
        )
        assert revoked["policyVersion"] == 2 and revoked["admissionSequence"] == 1
        if container is not None:
            batch = container.batches[-2]
            assert [operation[0] for operation in batch] == ["replace", "create"]
            record = container.items[admission.admission_id]
            assert (
                record["organizationId"] == "default"
                and "inputs" not in record
                and "result" not in record
            )

    asyncio.run(scenario())


def test_failed_cosmos_admission_batch_has_no_partial_authority_or_admission(
    validator, capability_graph
):
    from management_app.adapters.configuration_repository import CosmosConfigurationRepository
    from management_app.models.permissions import CapabilityAttempt

    async def scenario():
        container = ContainerDouble()
        store = CosmosConfigurationRepository(container=container, validator=validator)
        snapshot, _ = await store.initialize(capability_graph, actor_id="manager-fixture")
        authority = await store.read_authority()
        before = copy.deepcopy(container.items)
        container.fail_operation = 1
        request = CapabilityAttempt(
            requestId="request-store-fixture",
            sessionId="session-store-fixture",
            agentId="agent-alpha",
            agentEpoch=1,
            pinnedRevisionId=snapshot["id"],
            toolId="tool-public",
            actionId="lookup",
            inputs={},
            deadlineAt=datetime.now(UTC) + timedelta(seconds=10),
        )
        with pytest.raises(RuntimeError, match="permission_authority_unavailable"):
            await store.admit_capability(
                request, if_match=authority["_etag"], actor_id="runtime-fixture"
            )
        assert container.items == before

    asyncio.run(scenario())


def test_two_conditional_writers_commit_once(repository, configuration_graph) -> None:
    store, container = repository

    async def scenario():
        initial, etag = await store.initialize(configuration_graph, actor_id="bootstrap")
        alpha, beta = copy.deepcopy(configuration_graph), copy.deepcopy(configuration_graph)
        alpha["agents"][3]["prompt"] = "First manager edit"
        beta["agents"][3]["prompt"] = "Second manager edit"
        outcomes = await asyncio.gather(
            store.publish(alpha, if_match=etag, actor_id="manager-one"),
            store.publish(beta, if_match=etag, actor_id="manager-two"),
        )
        committed = [outcome for outcome in outcomes if outcome is not None]
        assert len(committed) == 1
        current = await store.read_active()
        assert current == committed[0] and current[0]["revisionNumber"] == 2
        assert current[0]["id"] != initial["id"]
        assert current[1] != etag
        if container is not None:
            assert len(container.items) == 4

    asyncio.run(scenario())


def test_invalid_graph_never_partially_publishes(repository, configuration_graph) -> None:
    from management_app.adapters.configuration_repository import ConfigurationRejected

    store, container = repository

    async def scenario():
        before = await store.initialize(configuration_graph, actor_id="bootstrap")
        configuration_graph["agents"][3]["communityId"] = "missing"
        with pytest.raises(ConfigurationRejected) as error:
            await store.publish(configuration_graph, if_match=before[1], actor_id="manager-one")
        assert error.value.issues and str(error.value) == "configuration_rejected"
        assert await store.read_active() == before
        if container is not None:
            assert len(container.batches) == 1

    asyncio.run(scenario())


def test_exact_pinned_revocation_and_disabled_tool_share_publication_batch(
    repository, capability_graph
) -> None:
    store, container = repository

    async def scenario():
        before = await store.initialize(capability_graph, actor_id="bootstrap")
        capability_graph["agents"][3]["capabilityAssignments"] = []
        capability_graph["tools"][0]["enabled"] = False
        published = await store.publish(
            capability_graph, if_match=before[1], actor_id="manager-one"
        )
        assert published is not None
        authority = await store.read_authority()
        assert "tool-public" in authority["disabledToolIds"]
        assert {
            "agentId": "agent-alpha",
            "toolId": "tool-public",
            "actionName": "lookup",
        } in authority["revokedAssignments"]
        assert authority["policyVersion"] == 2
        if container is not None:
            operations = container.batches[-1]
            assert [operation[0] for operation in operations] == ["create", "replace", "replace"]
            assert operations[1][1][0] == "active" and operations[2][1][0] == "security-authority"
            assert "if_match_etag" in operations[1][2] and "if_match_etag" in operations[2][2]

    asyncio.run(scenario())


def test_injected_cosmos_survives_repository_reconstruction(validator, configuration_graph) -> None:
    from management_app.adapters.configuration_repository import CosmosConfigurationRepository

    async def scenario():
        container = ContainerDouble()
        first = CosmosConfigurationRepository(container=container, validator=validator)
        initial = await first.initialize(configuration_graph, actor_id="bootstrap")
        second = CosmosConfigurationRepository(container=container, validator=validator)
        assert await second.read_active() == initial

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", [0, 1, 2])
def test_batch_failure_leaves_revision_pointer_and_authority_unchanged(
    validator, configuration_graph, operation
) -> None:
    from management_app.adapters.configuration_repository import CosmosConfigurationRepository

    async def scenario():
        container = ContainerDouble()
        store = CosmosConfigurationRepository(container=container, validator=validator)
        initial = await store.initialize(configuration_graph, actor_id="bootstrap")
        before = copy.deepcopy(container.items)
        container.fail_operation = operation
        with pytest.raises(RuntimeError, match="^configuration_store_unavailable$"):
            await store.publish(configuration_graph, if_match=initial[1], actor_id="manager-one")
        assert container.items == before

    asyncio.run(scenario())


def test_authority_admission_conflict_retries_without_losing_its_sequence(
    validator, configuration_graph
) -> None:
    from management_app.adapters.configuration_repository import CosmosConfigurationRepository

    async def scenario():
        container = ContainerDouble()
        store = CosmosConfigurationRepository(container=container, validator=validator)
        initial = await store.initialize(configuration_graph, actor_id="bootstrap")
        container.authority_conflict = True
        result = await store.publish(
            configuration_graph, if_match=initial[1], actor_id="manager-one"
        )
        assert result is not None and result[0]["revisionNumber"] == 2
        assert (await store.read_authority())["admissionSequence"] == 1

    asyncio.run(scenario())


def test_tampered_snapshot_hash_fails_closed(validator, configuration_graph) -> None:
    from management_app.adapters.configuration_repository import CosmosConfigurationRepository

    async def scenario():
        container = ContainerDouble()
        store = CosmosConfigurationRepository(container=container, validator=validator)
        initial = await store.initialize(configuration_graph, actor_id="bootstrap")
        container.items[initial[0]["id"]]["agents"][3]["prompt"] = "Unpublished mutation"
        with pytest.raises(RuntimeError, match="^configuration_store_unavailable$"):
            await store.read_active()
        assert "Unpublished mutation" not in json.dumps(container.batches)

    asyncio.run(scenario())


def stored_by_earlier_release(container: ContainerDouble, revision_id: str, **changes) -> None:
    """Rewrite a stored revision as an earlier writer left it, hashing the graph as stored."""
    import hashlib

    from management_app.services.configuration_validation import GRAPH_FIELDS, encoded

    document = container.items[revision_id]
    document.update(changes)
    graph = {key: value for key, value in document.items() if key in GRAPH_FIELDS}
    document["graphHash"] = hashlib.sha256(encoded(graph)).hexdigest()


@pytest.mark.parametrize(
    "routable",
    [
        pytest.param(None, id="schema-2.0.0-without-routable"),
        pytest.param(True, id="2.1.0-writer-with-explicit-routable"),
    ],
)
def test_revisions_from_earlier_releases_still_load_and_bootstrap(
    validator, configuration_graph, routable
) -> None:
    from management_app.adapters.configuration_repository import (
        CosmosConfigurationRepository,
        verified_revision,
    )
    from management_app.api.configuration import revision_response
    from management_app.services.bootstrap import bootstrap
    from management_app.services.configuration_publication import runtime_projection

    async def scenario():
        container = ContainerDouble()
        store = CosmosConfigurationRepository(container=container, validator=validator)
        initial = await store.initialize(configuration_graph, actor_id="bootstrap")
        communities = [
            {key: value for key, value in community.items() if key != "routable"}
            | ({} if routable is None else {"routable": routable})
            for community in configuration_graph["communities"]
        ]
        stored_by_earlier_release(
            container,
            initial[0]["id"],
            schemaVersion="2.0.0" if routable is None else "2.1.0",
            communities=communities,
        )
        batches = len(container.batches)
        # int failed here: startup read the active revision and rejected it as invalid_snapshot.
        result = await bootstrap(store)
        active, etag = await store.read_active()
        assert result.revision_id == active["id"] == initial[0]["id"] and etag == initial[1]
        assert result.configuration_ready
        assert all(community.get("routable", True) is True for community in active["communities"])
        assert active["communities"] == communities
        assert await store.read_revision(initial[0]["id"]) == active
        # int failed next: both configuration endpoints verify the repository's snapshot again.
        assert verified_revision(active) == active
        assert revision_response((active, etag), "c" * 32).status_code == 200
        assert runtime_projection(verified_revision(active))["communities"] == communities
        assert len(container.batches) == batches
        container.items[initial[0]["id"]]["communities"][0]["name"] = "Unpublished mutation"
        with pytest.raises(RuntimeError, match="^configuration_store_unavailable$"):
            await store.read_active()

    asyncio.run(scenario())


def test_non_routable_community_round_trips(repository, configuration_graph) -> None:
    store, _container = repository
    graph = copy.deepcopy(configuration_graph)
    graph["communities"][1]["routable"] = False

    async def scenario():
        published, _etag = await store.initialize(graph, actor_id="bootstrap")
        alpha, beta = published["communities"]
        assert alpha["routable"] is True and beta["routable"] is False
        assert (await store.read_active())[0] == published

    asyncio.run(scenario())


def test_snapshot_limit_includes_server_metadata(repository, configuration_graph) -> None:
    from management_app.adapters.configuration_repository import ConfigurationRejected
    from management_app.services.configuration_validation import SNAPSHOT_LIMIT, encoded

    store, container = repository
    payload = copy.deepcopy(configuration_graph)
    payload["agents"].extend(
        {
            **copy.deepcopy(payload["agents"][3]),
            "agentId": f"padding-agent-{index}",
            "name": f"Padding agent {index}",
        }
        for index in range(32)
    )
    remaining = SNAPSHOT_LIMIT - len(encoded(payload))
    for agent in payload["agents"]:
        padding = min(16 * 1024 - len(agent["prompt"].encode("utf-8")), remaining)
        agent["prompt"] += "x" * padding
        remaining -= padding
    assert remaining == 0
    assert len(encoded(payload)) == SNAPSHOT_LIMIT
    assert store.validator.validate(payload) == []

    async def scenario():
        with pytest.raises(ConfigurationRejected) as failure:
            await store.initialize(payload, actor_id="manager-one")
        assert any(issue["code"] == "configuration_too_large" for issue in failure.value.issues)
        assert await store.read_active() is None
        if container is not None:
            assert not container.items

    asyncio.run(scenario())


@pytest.fixture
def acceptance_api(repository, configuration_graph, validator):
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.api.configuration import create_router
    from management_app.security import EntraPolicy, IdentityService, SessionStore, TokenVerifier
    from management_app.services.audit import AuditService

    store, container = repository
    policy = EntraPolicy(
        tenant_id=str(uuid4()),
        client_id=str(uuid4()),
        api_audience=str(uuid4()),
        origin="https://management.example.invalid",
        manager_role="Configuration.Manager",
        manager_scope="Configuration.ReadWrite",
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update({"kid": "acceptance-fixture-key", "alg": "RS256", "use": "sig"})

    class Keys:
        async def signing_keys(self):
            return {"keys": [public]}

    actors = [str(uuid4()), str(uuid4())]

    def headers(actor, role="Configuration.Manager"):
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": policy.issuer,
                "tid": policy.tenant_id,
                "aud": policy.api_audience,
                "oid": actor,
                "sub": actor,
                "iat": now,
                "nbf": now,
                "exp": now + 300,
                "ver": "2.0",
                "roles": [role],
                "scp": policy.manager_scope,
            },
            key,
            algorithm="RS256",
            headers={"kid": "acceptance-fixture-key"},
        )
        return {"Authorization": "Bearer " + token}

    identity = IdentityService(
        policy=policy,
        verifier=TokenVerifier(policy=policy, keys=Keys()),
        sessions=SessionStore(policy=policy),
    )
    audit = LocalAuditRepository()
    asyncio.run(store.initialize(configuration_graph, actor_id="acceptance-bootstrap"))
    app = FastAPI()
    app.include_router(
        create_router(
            repository=store,
            identity=identity,
            validator=validator,
            audit=AuditService(repository=audit),
        )
    )
    with TestClient(app, base_url=policy.origin) as client:
        yield client, actors, headers, audit


def test_us2_two_managers_share_one_revision_and_conflicts_never_overwrite(
    acceptance_api, configuration_graph
) -> None:
    client, actors, headers, audit = acceptance_api
    first = client.get("/v2/configuration", headers=headers(actors[0]))
    second = client.get("/v2/configuration", headers=headers(actors[1]))
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() and first.headers["ETag"] == second.headers["ETag"]
    assert first.json()["organizationId"] == "default" and len(first.json()["communities"]) == 2
    assert {agent["role"] for agent in first.json()["agents"] if agent["role"] != "ordinary"} == {
        "welcome",
        "routing",
        "goodbye",
    }
    changed = copy.deepcopy(configuration_graph)
    changed["communities"][0]["name"] = "Shared community update"
    changed["agents"][3]["prompt"] = "Synthetic approved shared revision"
    saved = client.put(
        "/v2/configuration",
        json=changed,
        headers={**headers(actors[0]), "If-Match": first.headers["ETag"]},
    )
    assert saved.status_code == 200 and saved.json()["createdBy"] == actors[0]
    rejected = client.put(
        "/v2/configuration",
        json=configuration_graph,
        headers={**headers(actors[1]), "If-Match": second.headers["ETag"]},
    )
    assert rejected.status_code == 409
    assert client.get("/v2/configuration", headers=headers(actors[1])).json() == saved.json()
    changed["communities"][1]["routingDescription"] = "Synthetic second manager shared edit"
    second_saved = client.put(
        "/v2/configuration",
        json=changed,
        headers={**headers(actors[1]), "If-Match": saved.headers["ETag"]},
    )
    assert second_saved.status_code == 200 and second_saved.json()["createdBy"] == actors[1]
    for actor in actors:
        assert client.get("/v2/configuration", headers=headers(actor)).json() == second_saved.json()
    assert client.get("/v2/configuration").status_code == 401
    assert (
        client.get("/v2/configuration", headers=headers(actors[1], "Runtime.Reader")).status_code
        == 403
    )
    assert (
        len(
            [
                record
                for record in audit.records.values()
                if record["action"] == "configuration.publish"
            ]
        )
        == 2
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "delete-role",
        "disable-role",
        "duplicate-role",
        "bypass-role",
        "invalid-reference",
        "tenant-selection",
    ],
)
def test_us2_invalid_manager_edits_never_change_the_active_revision(
    acceptance_api, configuration_graph, mutation
) -> None:
    client, actors, headers, audit = acceptance_api
    before = client.get("/v2/configuration", headers=headers(actors[0]))
    changed = copy.deepcopy(configuration_graph)
    if mutation == "delete-role":
        changed["agents"].pop(0)
    elif mutation == "disable-role":
        changed["agents"][0]["enabled"] = False
    elif mutation == "duplicate-role":
        changed["agents"].append(
            {
                **copy.deepcopy(changed["agents"][0]),
                "agentId": "duplicate-welcome",
                "name": "Duplicate Welcome",
            }
        )
    elif mutation == "bypass-role":
        changed["relationships"].append(
            {
                "relationshipId": "forged-protected-path",
                "sourceAgentId": "agent-alpha",
                "targetAgentId": "agent-goodbye",
                "allowedContextFields": [],
            }
        )
    elif mutation == "invalid-reference":
        changed["communities"][0]["entryAgentId"] = "unknown-agent"
    else:
        changed["organizationId"] = "another-organization"
    response = client.put(
        "/v2/configuration",
        json=changed,
        headers={**headers(actors[0]), "If-Match": before.headers["ETag"]},
    )
    assert response.status_code == 422
    after = client.get("/v2/configuration", headers=headers(actors[1]))
    assert after.json() == before.json() and after.headers["ETag"] == before.headers["ETag"]
    assert not any(record["action"] == "configuration.publish" for record in audit.records.values())
