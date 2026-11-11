"""US2 configuration API contracts; injected test identity is not a live auth bypass."""

import asyncio
import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

PATH = "/v2/configuration"
ETAG = '"configuration-fixture-1"'
COLLECTIONS = {
    "agents",
    "communities",
    "relationships",
    "communityConnections",
    "layout",
    "tools",
    "mcpConnections",
}
REVISION_FIELDS = {
    "id",
    "organizationId",
    "schemaVersion",
    "revisionNumber",
    "createdAt",
    "createdBy",
    "graphHash",
    "warnings",
    *COLLECTIONS,
}


def revision(payload: dict, number: int, actor: str) -> dict:
    from management_app.models.configuration import ConfigurationGraph

    graph = ConfigurationGraph.model_validate(payload).model_dump(mode="json", by_alias=True)
    return {
        **graph,
        "id": f"revision-{number}",
        "organizationId": "default",
        "revisionNumber": number,
        "createdAt": "2026-10-23T00:00:00Z",
        "createdBy": actor,
        "graphHash": hashlib.sha256(
            json.dumps(
                graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        ).hexdigest(),
    }


@dataclass
class IdentityDouble:
    mode: str = "manager"
    actor: str = "manager-one"
    calls: int = 0

    async def require_manager(self, request) -> str:
        self.calls += 1
        if self.mode == "anonymous":
            raise HTTPException(status_code=401, detail="synthetic-private-auth-details")
        if self.mode == "forbidden":
            raise HTTPException(status_code=403, detail="synthetic-private-auth-details")
        return self.actor


@dataclass
class ValidatorDouble:
    errors: list[dict] = field(default_factory=list)
    calls: int = 0

    def validate(self, payload: dict) -> list[dict]:
        self.calls += 1
        return copy.deepcopy(self.errors)


@dataclass
class AuditDouble:
    revisions: list[str] = field(default_factory=list)
    fail: bool = False

    async def recover_publication(self, snapshot):
        if self.fail:
            raise RuntimeError("synthetic-private-audit-details")
        self.revisions.append(snapshot["id"])
        return True

    async def record(self, record):
        return True


@dataclass
class RepositoryDouble:
    snapshot: dict
    etag: str = ETAG
    reads: int = 0
    publications: int = 0
    fail_read: bool = False
    audit: AuditDouble = field(default_factory=AuditDouble)

    async def read_active(self) -> tuple[dict, str]:
        self.reads += 1
        if self.fail_read:
            raise RuntimeError("synthetic-private-storage-details")
        return copy.deepcopy(self.snapshot), self.etag

    async def publish(
        self, payload: dict, *, if_match: str, actor_id: str, actor_name: str = ""
    ) -> tuple[dict, str] | None:
        if if_match != self.etag:
            return None
        self.publications += 1
        number = self.snapshot["revisionNumber"] + 1
        self.snapshot = revision(payload, number, actor_id)
        self.etag = f'"configuration-fixture-{number}"'
        return copy.deepcopy(self.snapshot), self.etag


@pytest.fixture
def configuration_api(configuration_graph):
    from management_app.api.configuration import create_router

    identity, validator = IdentityDouble(), ValidatorDouble()
    repository = RepositoryDouble(revision(configuration_graph, 1, "bootstrap-fixture"))
    app = FastAPI()
    app.include_router(
        create_router(
            repository=repository, identity=identity, validator=validator, audit=repository.audit
        )
    )
    with TestClient(app) as client:
        yield client, identity, validator, repository


def problem(response, status: int) -> dict:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert {"type", "title", "status", "code", "correlation_id"} <= body.keys()
    assert body["status"] == status
    assert isinstance(body["type"], str) and body["type"]
    assert isinstance(body["title"], str) and body["title"]
    assert re.fullmatch(r"[a-z][a-z0-9_]+", body["code"])
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", body["correlation_id"])
    assert "synthetic-private-auth-details" not in response.text
    assert "synthetic-private-storage-details" not in response.text
    return body


def test_authorized_get_returns_complete_revision_and_strong_etag(configuration_api) -> None:
    client, identity, validator, repository = configuration_api
    response = client.get(PATH)
    assert response.status_code == 200
    assert response.headers["ETag"] == ETAG
    assert set(response.json()) == REVISION_FIELDS
    assert response.json() == repository.snapshot | {"warnings": []}
    assert response.json()["organizationId"] == "default"
    assert len(response.json()["communities"]) == 2
    assert identity.calls == 1 and repository.publications == 0


def test_put_publishes_complete_revision_with_actor_and_new_etag(
    configuration_api, configuration_graph
) -> None:
    client, identity, validator, repository = configuration_api
    payload = copy.deepcopy(configuration_graph)
    payload["agents"][3]["prompt"] = "Updated synthetic prompt"
    response = client.put(PATH, json=payload, headers={"If-Match": ETAG})
    assert response.status_code == 200
    assert response.headers["ETag"] != ETAG
    body = response.json()
    assert set(body) == REVISION_FIELDS
    assert body["revisionNumber"] == 2 and body["createdBy"] == identity.actor
    assert all(body[name] == payload[name] for name in COLLECTIONS)
    assert repository.publications == 1 and validator.calls == 1
    current = client.get(PATH)
    assert current.json() == body and current.headers["ETag"] == response.headers["ETag"]


@pytest.mark.parametrize("mode,status", [("anonymous", 401), ("forbidden", 403)])
@pytest.mark.parametrize("method", ["get", "put"])
def test_unauthorized_requests_do_not_read_or_mutate_private_graph(
    configuration_api, mode, status, method
) -> None:
    client, identity, validator, repository = configuration_api
    identity.mode = mode
    before = copy.deepcopy(repository.snapshot)
    response = (
        client.get(PATH)
        if method == "get"
        else client.put(
            PATH,
            content=b"deliberately-invalid-json",
            headers={"If-Match": ETAG},
        )
    )
    problem(response, status)
    assert repository.reads == repository.publications == validator.calls == 0
    assert repository.snapshot == before
    assert "Synthetic approved prompt" not in response.text


def test_missing_if_match_is_428_without_publication(
    configuration_api, configuration_graph
) -> None:
    client, identity, validator, repository = configuration_api
    problem(client.put(PATH, json=configuration_graph), 428)
    assert repository.publications == validator.calls == 0


@pytest.mark.parametrize(
    "value,status", [('"stale-etag"', 409), ("*", 422), ('W/"configuration-fixture-1"', 422)]
)
def test_stale_or_non_exact_precondition_cannot_overwrite(
    configuration_api, configuration_graph, value: str, status: int
) -> None:
    client, identity, validator, repository = configuration_api
    before = copy.deepcopy(repository.snapshot)
    problem(client.put(PATH, json=configuration_graph, headers={"If-Match": value}), status)
    assert repository.publications == 0 and repository.snapshot == before


def test_two_managers_cannot_silently_overwrite_shared_revision(
    configuration_api, configuration_graph
) -> None:
    client, identity, validator, repository = configuration_api
    first = client.get(PATH)
    identity.actor = "manager-two"
    second = client.get(PATH)
    assert first.headers["ETag"] == second.headers["ETag"]
    first_payload = copy.deepcopy(configuration_graph)
    first_payload["agents"][3]["name"] = "Manager one update"
    identity.actor = "manager-one"
    saved = client.put(PATH, json=first_payload, headers={"If-Match": first.headers["ETag"]})
    assert saved.status_code == 200
    stale_payload = copy.deepcopy(configuration_graph)
    stale_payload["agents"][4]["name"] = "Manager two unsaved update"
    identity.actor = "manager-two"
    problem(client.put(PATH, json=stale_payload, headers={"If-Match": second.headers["ETag"]}), 409)
    assert repository.publications == 1 and repository.snapshot | {"warnings": []} == saved.json()


@pytest.mark.parametrize("character", ["x", "\u00e9"], ids=["ascii", "utf8"])
def test_oversized_snapshot_is_rejected_before_publication(
    configuration_api, configuration_graph, character: str
) -> None:
    client, identity, validator, repository = configuration_api
    payload = copy.deepcopy(configuration_graph)
    payload["agents"][3]["prompt"] = character * (512 * 1024 + 1)
    before = copy.deepcopy(repository.snapshot)
    problem(client.put(PATH, json=payload, headers={"If-Match": ETAG}), 413)
    assert repository.publications == 0 and repository.snapshot == before


def test_validation_error_has_safe_fields_and_never_echoes_rejected_values(
    configuration_api, configuration_graph
) -> None:
    client, identity, validator, repository = configuration_api
    candidate = str(uuid4())
    payload = copy.deepcopy(configuration_graph)
    payload["agents"][3]["prompt"] = "=".join(("Password", candidate))
    validator.errors = [
        {
            "path": "agents[3].prompt",
            "rule_id": "connection_string",
            "policy_version": 1,
            "code": "credential_literal_prohibited",
            "input": candidate,
        }
    ]
    before = copy.deepcopy(repository.snapshot)
    response = client.put(PATH, json=payload, headers={"If-Match": ETAG})
    body = problem(response, 422)
    assert body["code"] == "credential_literal_prohibited"
    assert body["errors"][0]["path"] == "agents[3].prompt"
    assert body["errors"][0]["rule_id"] == "connection_string"
    assert body["errors"][0]["policy_version"] == 1
    assert candidate not in response.text and "input" not in body["errors"][0]
    assert repository.publications == 0 and repository.snapshot == before


def test_malformed_json_uses_safe_problem_details(configuration_api) -> None:
    client, identity, validator, repository = configuration_api
    response = client.put(
        PATH, content=b"invalid-configuration-fixture", headers={"If-Match": ETAG}
    )
    problem(response, 422)
    assert "invalid-configuration-fixture" not in response.text
    assert repository.publications == validator.calls == 0


def test_storage_failure_has_no_private_diagnostics_or_local_fallback(configuration_api) -> None:
    client, identity, validator, repository = configuration_api
    repository.fail_read = True
    problem(client.get(PATH), 503)
    assert repository.publications == 0


def test_corrupt_full_revision_hash_is_unavailable_without_reflection(configuration_api) -> None:
    client, identity, validator, repository = configuration_api
    repository.snapshot["agents"][3]["prompt"] = "synthetic-private-corrupt-revision"
    response = client.get(PATH)
    problem(response, 503)
    assert "synthetic-private-corrupt-revision" not in response.text
    assert repository.publications == 0


def test_successful_publication_is_audited_once(configuration_api, configuration_graph) -> None:
    client, identity, validator, repository = configuration_api
    response = client.put(PATH, json=configuration_graph, headers={"If-Match": ETAG})
    assert response.status_code == 200
    assert repository.audit.revisions == [response.json()["id"]]
    assert response.headers["X-Audit-Status"] == "recorded"


def test_audit_failure_acknowledges_commit_and_marks_recovery_pending(
    configuration_api, configuration_graph
) -> None:
    client, identity, validator, repository = configuration_api
    repository.audit.fail = True
    response = client.put(PATH, json=configuration_graph, headers={"If-Match": ETAG})
    assert response.status_code == 200 and repository.publications == 1
    assert response.headers["X-Audit-Status"] == "pending"
    assert response.json() == repository.snapshot | {"warnings": []}
    assert "synthetic-private-audit-details" not in response.text


def test_publication_audit_uses_original_deadline_after_controlled_elapsed_work(
    configuration_api, configuration_graph, monkeypatch
) -> None:
    from management_app.api import configuration

    client, identity, validator, repository = configuration_api
    clock = SimpleNamespace(origin=None, seconds=0.0)
    deadlines = []

    class Timers:
        @staticmethod
        def get_running_loop():
            if clock.origin is None:
                clock.origin = asyncio.get_running_loop().time()
            return SimpleNamespace(time=lambda: clock.origin + clock.seconds)

        @staticmethod
        def timeout_at(deadline):
            deadlines.append(deadline)
            return asyncio.timeout_at(asyncio.get_running_loop().time() + 0.1)

    async def authorize(request):
        clock.seconds = 9.5
        return identity.actor

    async def blocked_audit(snapshot):
        await asyncio.Event().wait()

    monkeypatch.setattr(configuration, "asyncio", Timers)
    monkeypatch.setattr(identity, "require_manager", authorize)
    monkeypatch.setattr(repository.audit, "recover_publication", blocked_audit)
    response = client.put(PATH, json=configuration_graph, headers={"If-Match": ETAG})
    assert response.status_code == 200 and response.headers["X-Audit-Status"] == "pending"
    assert repository.publications == 1 and response.json() == repository.snapshot | {
        "warnings": []
    }
    assert deadlines == [clock.origin + 10, clock.origin + 10]


def test_invalid_save_has_no_publication_audit(configuration_api) -> None:
    client, identity, validator, repository = configuration_api
    problem(client.put(PATH, content=b"invalid", headers={"If-Match": ETAG}), 422)
    assert repository.audit.revisions == []


def test_unreachable_communities_are_warnings_that_never_block(
    configuration_api, configuration_graph
) -> None:
    client, _identity, _validator, repository = configuration_api
    configuration_graph["communities"][1]["routable"] = False
    response = client.put(PATH, json=configuration_graph, headers={"If-Match": ETAG})
    assert response.status_code == 200 and repository.publications == 1
    assert response.json()["warnings"] == [
        {"code": "community_unreachable", "communityId": "community-beta"}
    ]
    assert client.get(PATH).json()["warnings"] == response.json()["warnings"]


def test_publication_metadata_is_returned_to_editors_only(configuration_graph) -> None:
    from management_app.api.configuration import create_router

    class ReaderIdentity(IdentityDouble):
        async def require_reader(self, request, *, reader_role):
            return "tester-application"

    stamped = revision(configuration_graph, 1, "bootstrap-fixture") | {
        "publication": {
            "publishedAt": "2026-11-11T10:00:00Z",
            "publishedBy": "actor-one",
            "publishedByName": "Example Editor",
            "agentStamps": {},
        }
    }
    identity = ReaderIdentity()
    repository = RepositoryDouble(stamped)
    app = FastAPI()
    app.include_router(
        create_router(
            repository=repository,
            identity=identity,
            validator=ValidatorDouble(),
            audit=repository.audit,
            reader_role="Conversation.Tester",
        )
    )
    with TestClient(app) as client:
        assert client.get(PATH).json()["publication"]["publishedByName"] == "Example Editor"
        identity.mode = "forbidden"
        reader = client.get(PATH)
    assert reader.status_code == 200 and "publication" not in reader.json()
