"""Conditional current authority, not cached configuration, orders every attempt."""

import json
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


def attempt(**changes):
    from management_app.models.permissions import CapabilityAttempt

    return CapabilityAttempt.model_validate(
        {
            "requestId": "request-public-fixture",
            "sessionId": "session-public-fixture",
            "agentId": "agent-alpha",
            "agentEpoch": 1,
            "pinnedRevisionId": "revision-fixture",
            "toolId": "tool-public",
            "actionId": "lookup",
            "inputs": {},
            "deadlineAt": datetime.now(UTC) + timedelta(seconds=10),
            **changes,
        }
    )


@pytest.mark.anyio
async def test_authority_admission_is_conditional_and_does_not_advance_configuration(
    validator, capability_graph
):
    from management_app.adapters.configuration_repository import LocalConfigurationRepository

    repository = LocalConfigurationRepository(validator=validator)
    snapshot, pointer = await repository.initialize(capability_graph, actor_id="manager-fixture")
    authority = await repository.read_authority()
    request = attempt(pinnedRevisionId=snapshot["id"])
    admission = await repository.admit_capability(
        request, if_match=authority["_etag"], actor_id="runtime-fixture"
    )
    current = await repository.read_authority()
    assert (
        admission.admission_sequence
        == authority["admissionSequence"] + 1
        == current["admissionSequence"]
    )
    assert admission.request_id == request.request_id
    assert admission.policy_version == authority["policyVersion"]
    assert current["_etag"] != authority["_etag"]
    assert await repository.read_active() == (snapshot, pointer)
    assert all(key not in json.dumps(current) for key in ("inputs", "results", "transcript"))


@pytest.mark.anyio
async def test_revocation_committed_first_blocks_stale_admission(validator, capability_graph):
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.models.permissions import PermissionConflict

    repository = LocalConfigurationRepository(validator=validator)
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")
    authority = await repository.read_authority()
    update = {
        "disabledToolIds": ["tool-public"],
        "disabledConnectionIds": [],
        "revokedAssignments": [],
    }
    policy = await repository.revoke_capabilities(
        update, if_match=authority["_etag"], actor_id="manager-fixture"
    )
    assert policy["policyVersion"] == authority["policyVersion"] + 1
    with pytest.raises(PermissionConflict):
        await repository.admit_capability(
            attempt(pinnedRevisionId=snapshot["id"]),
            if_match=authority["_etag"],
            actor_id="runtime-fixture",
        )
    assert (await repository.read_authority())["admissionSequence"] == 0


@pytest.mark.anyio
async def test_admission_before_revocation_remains_metadata_but_later_attempt_is_denied(
    validator, capability_graph
):
    from management_app.adapters.configuration_repository import LocalConfigurationRepository

    repository = LocalConfigurationRepository(validator=validator)
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")
    authority = await repository.read_authority()
    admitted = await repository.admit_capability(
        attempt(pinnedRevisionId=snapshot["id"]),
        if_match=authority["_etag"],
        actor_id="runtime-fixture",
    )
    current = await repository.read_authority()
    await repository.revoke_capabilities(
        {
            "disabledToolIds": [],
            "disabledConnectionIds": ["connection-one"],
            "revokedAssignments": [],
        },
        if_match=current["_etag"],
        actor_id="manager-fixture",
    )
    assert admitted.admission_sequence == 1
    assert (await repository.read_authority())["disabledConnectionIds"] == ["connection-one"]


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "foreign-authority"},
        {"organizationId": "other"},
        {"policyVersion": True},
        {"admissionSequence": -1},
        {"updatedAt": "2026-01-01T00:00:00"},
        {"inputs": {"private": "synthetic-private-marker"}},
        {"disabledToolIds": ["tool-public", "tool-public"]},
    ],
)
def test_authority_rejects_invalid_identity_content_time_or_duplicate_sets(changes):
    from pydantic import ValidationError

    from management_app.models.permissions import PermissionAuthority

    values = {
        "id": "security-authority",
        "organizationId": "default",
        "policyVersion": 1,
        "disabledToolIds": [],
        "disabledConnectionIds": [],
        "revokedAssignments": [],
        "admissionSequence": 0,
        "updatedAt": datetime.now(UTC),
        "updatedBy": "manager-fixture",
    }
    with pytest.raises(ValidationError):
        PermissionAuthority.model_validate(values | changes)


def test_capability_admission_has_no_arguments_results_or_free_text():
    from pydantic import ValidationError

    from management_app.models.permissions import CapabilityAdmission

    request = attempt()
    metadata = request.model_dump(exclude={"inputs", "deadline_at"}) | {
        "admission_id": "admission-fixture",
        "admission_sequence": 1,
        "policy_version": 1,
        "admitted_at": datetime.now(UTC),
        "actor_id": "runtime-fixture",
    }
    admission = CapabilityAdmission.model_validate(metadata)
    assert "inputs" not in admission.model_dump() and "deadlineAt" not in admission.model_dump(
        by_alias=True
    )
    with pytest.raises(ValidationError):
        CapabilityAdmission.model_validate(
            metadata | {"results": {"content": "synthetic-private-marker"}}
        )


@pytest.fixture
def gateway_case(validator, capability_graph):
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.models.capabilities import ApprovedCapabilities
    from management_app.observability import Telemetry
    from management_app.services.capability_gateway import CapabilityGateway
    from management_app.services.capability_policy import CapabilityPolicy

    class Transport:
        def __init__(self):
            self.calls = []
            self.before_return = None

        async def execute(self, selected, *, request_id, deadline):
            self.calls.append((selected, request_id, deadline))
            if self.before_return:
                await self.before_return()
            return {"name": "Synthetic public result", "private": "Synthetic hidden result"}

    repository, transport = LocalConfigurationRepository(validator=validator), Transport()
    telemetry = Telemetry("management", "local")
    policy = CapabilityPolicy(
        reviewed=ApprovedCapabilities.from_reviewed(capability_graph),
        destinations={"fixture-public-endpoint": "https://public.example.invalid/rpc"},
        detector=validator.detector,
    )
    gateway = CapabilityGateway(
        repository=repository, policy=policy, transport=transport, telemetry=telemetry
    )
    yield gateway, repository, transport, telemetry
    telemetry.close()


@pytest.mark.anyio
async def test_denial_preserves_only_the_repository_verified_revision_for_audit(
    gateway_case, capability_graph
):
    from management_app.services.capability_gateway import GatewayError

    gateway, repository, transport, _telemetry = gateway_case
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")
    authority = await repository.read_authority()
    await repository.revoke_capabilities(
        {"disabledToolIds": ["tool-public"], "disabledConnectionIds": [], "revokedAssignments": []},
        if_match=authority["_etag"],
        actor_id="manager-fixture",
    )
    with pytest.raises(GatewayError) as failure:
        await gateway.execute(attempt(pinnedRevisionId=snapshot["id"]), actor_id="runtime-fixture")
    assert failure.value.status == 403 and failure.value.revision_id == snapshot["id"]
    assert not transport.calls


@pytest.mark.anyio
async def test_gateway_returns_only_allowlisted_public_fields_after_admission(
    gateway_case, capability_graph
):
    gateway, repository, transport, telemetry = gateway_case
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")
    request = attempt(pinnedRevisionId=snapshot["id"])
    result = await gateway.execute(request, actor_id="runtime-fixture")
    assert result == {
        "requestId": request.request_id,
        "sessionId": request.session_id,
        "agentId": request.agent_id,
        "agentEpoch": 1,
        "result": {"name": "Synthetic public result"},
    }
    assert (
        len(transport.calls) == 1 and (await repository.read_authority())["admissionSequence"] == 1
    )
    assert "Synthetic hidden result" not in str(result)
    assert all("Synthetic public result" not in span.to_json() for span in telemetry.spans)


@pytest.mark.anyio
async def test_gateway_denies_committed_revocation_before_any_external_effect(
    gateway_case, capability_graph
):
    from management_app.services.capability_gateway import GatewayError

    gateway, repository, transport, telemetry = gateway_case
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")
    authority = await repository.read_authority()
    await repository.revoke_capabilities(
        {
            "disabledToolIds": [],
            "disabledConnectionIds": ["connection-one"],
            "revokedAssignments": [],
        },
        if_match=authority["_etag"],
        actor_id="manager-fixture",
    )
    with pytest.raises(GatewayError) as failure:
        await gateway.execute(attempt(pinnedRevisionId=snapshot["id"]), actor_id="runtime-fixture")
    assert failure.value.status == 403 and transport.calls == []


@pytest.mark.anyio
async def test_admitted_read_may_finish_but_next_attempt_rechecks_revocation(
    gateway_case, capability_graph
):
    from management_app.services.capability_gateway import GatewayError

    gateway, repository, transport, telemetry = gateway_case
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")

    async def revoke():
        authority = await repository.read_authority()
        await repository.revoke_capabilities(
            {
                "disabledToolIds": ["tool-public"],
                "disabledConnectionIds": [],
                "revokedAssignments": [],
            },
            if_match=authority["_etag"],
            actor_id="manager-fixture",
        )

    transport.before_return = revoke
    request = attempt(pinnedRevisionId=snapshot["id"])
    assert (await gateway.execute(request, actor_id="runtime-fixture"))["result"] == {
        "name": "Synthetic public result"
    }
    with pytest.raises(GatewayError) as failure:
        await gateway.execute(request, actor_id="runtime-fixture")
    assert failure.value.status == 403 and len(transport.calls) == 1


@pytest.mark.anyio
async def test_conflict_retry_reauthorizes_after_revocation(
    gateway_case, capability_graph, monkeypatch
):
    from management_app.models.permissions import PermissionConflict
    from management_app.services.capability_gateway import GatewayError

    gateway, repository, transport, telemetry = gateway_case
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")

    async def race(request, *, if_match, actor_id):
        await repository.revoke_capabilities(
            {
                "disabledToolIds": ["tool-public"],
                "disabledConnectionIds": [],
                "revokedAssignments": [],
            },
            if_match=if_match,
            actor_id="manager-fixture",
        )
        raise PermissionConflict("synthetic-authority-race")

    monkeypatch.setattr(repository, "admit_capability", race)
    with pytest.raises(GatewayError) as failure:
        await gateway.execute(attempt(pinnedRevisionId=snapshot["id"]), actor_id="runtime-fixture")
    assert failure.value.status == 403 and transport.calls == []


@pytest.mark.anyio
async def test_authority_outage_fails_closed_without_external_effect(
    gateway_case, capability_graph, monkeypatch
):
    from management_app.services.capability_gateway import GatewayError

    gateway, repository, transport, telemetry = gateway_case
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")

    async def outage():
        raise RuntimeError("synthetic-private-store-payload")

    monkeypatch.setattr(repository, "read_authority", outage)
    with pytest.raises(GatewayError) as failure:
        await gateway.execute(attempt(pinnedRevisionId=snapshot["id"]), actor_id="runtime-fixture")
    assert failure.value.status == 503 and transport.calls == []
    assert "synthetic-private-store-payload" not in str(failure.value)


@pytest.mark.anyio
async def test_expired_original_deadline_has_no_admission_or_effect(gateway_case, capability_graph):
    from management_app.services.capability_gateway import GatewayError

    gateway, repository, transport, telemetry = gateway_case
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")
    with pytest.raises(GatewayError) as failure:
        await gateway.execute(
            attempt(
                pinnedRevisionId=snapshot["id"], deadlineAt=datetime.now(UTC) - timedelta(seconds=1)
            ),
            actor_id="runtime-fixture",
        )
    assert failure.value.status == 504 and transport.calls == []
    assert (await repository.read_authority())["admissionSequence"] == 0


@pytest.mark.anyio
async def test_retry_preserves_request_id_deadline_and_requires_fresh_admission(
    gateway_case, capability_graph, monkeypatch
):
    from management_app.services.capability_gateway import RetryableCapabilityError

    gateway, repository, transport, telemetry = gateway_case
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")
    calls = []

    async def flaky(selected, *, request_id, deadline):
        calls.append((request_id, deadline))
        if len(calls) == 1:
            raise RetryableCapabilityError("synthetic-private-retry-detail")
        return {"name": "Synthetic public result"}

    monkeypatch.setattr(transport, "execute", flaky)
    assert (
        await gateway.execute(attempt(pinnedRevisionId=snapshot["id"]), actor_id="runtime-fixture")
    )["result"] == {"name": "Synthetic public result"}
    assert len(calls) == 2 and calls[0] == calls[1]
    assert (await repository.read_authority())["admissionSequence"] == 2


@pytest.mark.anyio
async def test_conversation_single_attempt_never_replays_retryable_public_work(
    gateway_case, capability_graph, monkeypatch
):
    from management_app.services.capability_gateway import GatewayError, RetryableCapabilityError

    gateway, repository, transport, telemetry = gateway_case
    snapshot, _ = await repository.initialize(capability_graph, actor_id="manager-fixture")
    calls = []

    async def uncertain(selected, *, request_id, deadline):
        calls.append((request_id, deadline))
        raise RetryableCapabilityError("synthetic-unknown")

    monkeypatch.setattr(transport, "execute", uncertain)
    with pytest.raises(GatewayError) as failure:
        await gateway.execute(
            attempt(pinnedRevisionId=snapshot["id"], singleAttempt=True),
            actor_id="runtime-fixture",
        )
    assert failure.value.status == 503 and len(calls) == 1
    assert (await repository.read_authority())["admissionSequence"] == 1


@pytest.mark.anyio
async def test_new_configuration_grant_cannot_expand_pinned_call(gateway_case, capability_graph):
    from management_app.services.capability_gateway import GatewayError

    gateway, repository, transport, telemetry = gateway_case
    import copy

    without_grant = copy.deepcopy(capability_graph)
    without_grant["agents"][3]["capabilityAssignments"] = []
    pinned, pointer = await repository.initialize(without_grant, actor_id="manager-fixture")
    assert await repository.publish(capability_graph, if_match=pointer, actor_id="manager-fixture")
    with pytest.raises(GatewayError) as failure:
        await gateway.execute(attempt(pinnedRevisionId=pinned["id"]), actor_id="runtime-fixture")
    assert failure.value.status == 403 and transport.calls == []
