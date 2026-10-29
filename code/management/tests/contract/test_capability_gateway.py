"""Execution/revocation API contracts; default foundation has no business bypass."""

import importlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def capability_api():
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.api.capabilities import create_router
    from management_app.models.permissions import PermissionConflict
    from management_app.observability import Telemetry
    from management_app.services.audit import AuditService, revocation_record
    from management_app.services.capability_gateway import GatewayError

    class Identity:
        async def require_runtime(self, request):
            if request.headers.get("X-Fixture-Identity") != "runtime":
                raise HTTPException(
                    403 if request.headers.get("X-Fixture-Identity") else 401,
                    "synthetic-private-auth-error",
                )
            return "runtime-fixture"

        async def require_manager(self, request):
            if request.headers.get("X-Fixture-Identity") != "manager":
                raise HTTPException(
                    403 if request.headers.get("X-Fixture-Identity") else 401,
                    "synthetic-private-auth-error",
                )
            return "manager-fixture"

    class Gateway:
        calls = 0
        failure = None

        async def execute(self, attempt, *, actor_id):
            self.calls += 1
            if self.failure:
                raise GatewayError(self.failure)
            return {
                "requestId": attempt.request_id,
                "sessionId": attempt.session_id,
                "agentId": attempt.agent_id,
                "agentEpoch": attempt.agent_epoch,
                "result": {"name": "Synthetic public result"},
            }

    class Repository:
        calls = 0
        record = None

        async def revoke_capabilities(self, payload, *, if_match, actor_id):
            if if_match != '"authority-fixture"':
                raise PermissionConflict("synthetic-private-conflict-details")
            self.calls += 1
            document = {
                "id": "security-authority",
                "organizationId": "default",
                "policyVersion": 2,
                "admissionSequence": 0,
                "updatedAt": datetime.now(UTC).isoformat(),
                "updatedBy": actor_id,
                **payload,
                "_etag": '"authority-next"',
            }
            self.record = revocation_record(document).model_dump(mode="json")
            return document

        async def read_revocation_audit(self, identifier):
            assert identifier == self.record["id"]
            return dict(self.record)

        async def acknowledge_revocation_audit(self, identifier, *, retention_seconds):
            assert identifier == self.record["id"] and retention_seconds == 86400

    identity, gateway, repository = Identity(), Gateway(), Repository()
    telemetry = Telemetry("management", "local")
    application = FastAPI()
    audit = AuditService(repository=LocalAuditRepository())
    application.state.security_audit = audit
    application.include_router(
        create_router(
            gateway=gateway,
            repository=repository,
            runtime_identity=identity,
            manager_identity=identity,
            telemetry=telemetry,
            audit=audit,
            audit_retention_seconds=86400,
        )
    )
    with TestClient(application, base_url="https://management.example.invalid") as client:
        yield client, gateway, repository, telemetry
    telemetry.close()


def body():
    return {
        "requestId": "request-fixture",
        "sessionId": "session-fixture",
        "agentId": "agent-alpha",
        "agentEpoch": 1,
        "pinnedRevisionId": "revision-fixture",
        "toolId": "tool-public",
        "actionId": "lookup",
        "inputs": {},
        "deadlineAt": (datetime.now(UTC) + timedelta(seconds=10)).isoformat(),
    }


def test_capability_routes_require_explicit_composition():
    from management_app.management_app import create_app

    application = create_app()
    routes = {getattr(route, "path", "") for route in application._api.routes}
    assert "/v1/runtime/capabilities/execute" not in routes
    assert "/v1/security/revocations" not in routes


def test_explicit_capability_router_provides_execution_and_revocation_only():
    module = importlib.import_module("management_app.api.capabilities")
    assert callable(module.create_router)


@pytest.mark.parametrize("identity", [None, "manager", "unknown"])
def test_execution_denies_non_runtime_before_body_or_effect(capability_api, identity):
    client, gateway, repository, telemetry = capability_api
    headers = {"X-Fixture-Identity": identity} if identity else {}
    response = client.post(
        "/v1/runtime/capabilities/execute",
        content="synthetic-private-malformed-body",
        headers=headers,
    )
    assert response.status_code == (403 if identity else 401) and gateway.calls == 0
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "synthetic-private" not in response.text


def test_capability_denials_audit_only_verified_identity_not_submitted_references(capability_api):
    import json

    client, gateway, _repository, _telemetry = capability_api
    gateway.failure = 403
    submitted = body() | {"inputs": {"synthetic-private-input": "synthetic-private-value"}}
    response = client.post(
        "/v1/runtime/capabilities/execute",
        json=submitted,
        headers={"X-Fixture-Identity": "runtime"},
    )
    assert response.status_code == 403 and response.headers["X-Audit-Status"] == "recorded"
    records = list(client.app.state.security_audit.repository.records.values())
    assert len(records) == 1 and records[0]["actor_ref"] == "runtime-fixture"
    assert records[0]["action"] == "capability.denied" and records[0]["outcome"] == "denied"
    assert records[0]["revision_id"] is None
    serialized = json.dumps(records)
    assert all(
        value not in serialized
        for value in (
            "synthetic-private",
            "session-fixture",
            "agent-alpha",
            "revision-fixture",
            "tool-public",
        )
    )


def test_capability_authentication_denial_is_classified_anonymous_without_payload_access(
    capability_api,
):
    client, gateway, _repository, _telemetry = capability_api
    response = client.post(
        "/v1/runtime/capabilities/execute", content="synthetic-private-malformed"
    )
    assert response.status_code == 401 and gateway.calls == 0
    records = list(client.app.state.security_audit.repository.records.values())
    assert records[0]["actor_ref"] == "anonymous" and records[0]["action"] == "authorization.check"


def test_revocation_audit_failure_reports_pending_without_replaying_commit(
    capability_api, monkeypatch
):
    client, _gateway, repository, _telemetry = capability_api

    async def unavailable(record):
        raise RuntimeError("synthetic-private-audit-error")

    monkeypatch.setattr(client.app.state.security_audit, "recover_security", unavailable)
    response = client.put(
        "/v1/security/revocations",
        json={
            "disabledToolIds": ["tool-public"],
            "disabledConnectionIds": [],
            "revokedAssignments": [],
        },
        headers={"X-Fixture-Identity": "manager", "If-Match": '"authority-fixture"'},
    )
    assert response.status_code == 200 and response.headers["X-Audit-Status"] == "pending"
    assert repository.calls == 1 and repository.record["action"] == "security.revoke"
    assert "synthetic-private" not in response.text


def test_invalid_revocation_is_audited_as_revocation_not_capability_execution(capability_api):
    client, _gateway, repository, _telemetry = capability_api
    response = client.put(
        "/v1/security/revocations",
        json={"unknown": "synthetic-private-value"},
        headers={"X-Fixture-Identity": "manager", "If-Match": '"authority-fixture"'},
    )
    assert response.status_code == 422 and repository.calls == 0
    records = list(client.app.state.security_audit.repository.records.values())
    assert (
        records[0]["action"] == "security.revoke"
        and records[0]["reason_code"] == "request_rejected"
    )


def test_denial_audit_outage_cannot_change_authentication_outcome_or_expose_error(
    capability_api, monkeypatch
):
    client, gateway, _repository, _telemetry = capability_api

    async def unavailable(record):
        raise RuntimeError("synthetic-private-audit-error")

    monkeypatch.setattr(client.app.state.security_audit, "record", unavailable)
    response = client.post("/v1/runtime/capabilities/execute", content="synthetic-private-input")
    assert response.status_code == 401 and response.headers["X-Audit-Status"] == "pending"
    assert gateway.calls == 0 and "synthetic-private" not in response.text


def test_oversized_capability_request_is_audited_without_body_retention(capability_api):
    client, gateway, _repository, _telemetry = capability_api
    response = client.post(
        "/v1/runtime/capabilities/execute",
        content=b"x" * 65537,
        headers={"X-Fixture-Identity": "runtime"},
    )
    assert response.status_code == 413 and response.headers["X-Audit-Status"] == "recorded"
    assert gateway.calls == 0
    records = list(client.app.state.security_audit.repository.records.values())
    assert (
        records[0]["actor_ref"] == "runtime-fixture"
        and records[0]["reason_code"] == "request_rejected"
    )


def test_execution_validates_then_delegates_only_to_gateway(capability_api):
    client, gateway, repository, telemetry = capability_api
    response = client.post(
        "/v1/runtime/capabilities/execute", json=body(), headers={"X-Fixture-Identity": "runtime"}
    )
    assert response.status_code == 200 and gateway.calls == 1
    assert response.json()["result"] == {"name": "Synthetic public result"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("status", [403, 422, 503, 504])
def test_gateway_errors_are_safe_problem_details(capability_api, status):
    client, gateway, repository, telemetry = capability_api
    gateway.failure = status
    response = client.post(
        "/v1/runtime/capabilities/execute", json=body(), headers={"X-Fixture-Identity": "runtime"}
    )
    assert response.status_code == status
    assert set(response.json()) == {"type", "title", "status", "code", "correlation_id"}
    assert "inputs" not in response.text and "synthetic-private" not in response.text


def test_invalid_reference_or_extra_fields_are_not_reflected_or_dispatched(capability_api):
    client, gateway, repository, telemetry = capability_api
    response = client.post(
        "/v1/runtime/capabilities/execute",
        json=body() | {"transcript": "synthetic-private-transcript"},
        headers={"X-Fixture-Identity": "runtime"},
    )
    assert response.status_code == 422 and gateway.calls == 0
    assert "synthetic-private-transcript" not in response.text


@pytest.mark.parametrize(
    "etag,status", [(None, 428), ('"stale"', 412), ('"authority-fixture"', 200)]
)
def test_revocation_requires_manager_and_current_authority_etag(capability_api, etag, status):
    client, gateway, repository, telemetry = capability_api
    headers = {"X-Fixture-Identity": "manager"}
    if etag is not None:
        headers["If-Match"] = etag
    response = client.put(
        "/v1/security/revocations",
        json={
            "disabledToolIds": ["tool-public"],
            "disabledConnectionIds": [],
            "revokedAssignments": [],
        },
        headers=headers,
    )
    assert response.status_code == status and repository.calls == (1 if status == 200 else 0)
    if status == 200:
        assert (
            response.json() == {"policyVersion": 2}
            and response.headers["etag"] == '"authority-next"'
        )


def test_runtime_identity_cannot_revoke_manager_permissions(capability_api):
    client, gateway, repository, telemetry = capability_api
    response = client.put(
        "/v1/security/revocations",
        json={},
        headers={"X-Fixture-Identity": "runtime", "If-Match": '"authority-fixture"'},
    )
    assert response.status_code == 403 and repository.calls == 0
