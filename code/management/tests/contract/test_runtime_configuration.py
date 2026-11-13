"""Runtime snapshots use service identity, immutable revision integrity and conditional reads."""

import copy
import hashlib

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

PATH = "/v2/runtime/configuration"


def resolved(policy: dict) -> dict:
    """The projection resolves absent 2.2.0 fields to the shipped defaults (empty additions)."""
    from management_app.services.default_prompts import resolve_settings

    policy = copy.deepcopy(policy)
    policy["conversationSettings"] = resolve_settings(policy.get("conversationSettings"))
    for agent in policy["agents"]:
        agent.setdefault("voiceAddition", "")
        agent.setdefault("textAddition", "")
    return policy


def test_projection_always_carries_every_setting_and_addition_under_its_hash(
    runtime_configuration_api, validator, configuration_graph
):
    from management_app.adapters.configuration_repository import publication
    from management_app.models.configuration import MODALITY_SETTINGS
    from management_app.services import default_prompts
    from management_app.services.configuration_validation import encoded

    client, identity, repository, telemetry = runtime_configuration_api
    graph = copy.deepcopy(configuration_graph)
    graph["conversationSettings"] = {"textGlobalPrompt": "Synthetic global"}
    graph["agents"][3]["voiceAddition"] = "Synthetic voice addition"
    repository.snapshot = publication(
        validator, graph, actor="manager-fixture", previous=None, authority=None
    )[0]
    projection = client.get(PATH, headers={"X-Fixture-Identity": "runtime"}).json()
    settings = projection["conversationSettings"]
    assert set(MODALITY_SETTINGS) == set(settings)
    assert settings["textGlobalPrompt"] == "Synthetic global"
    assert settings["voicePlatformRules"] == default_prompts.VOICE_PLATFORM_RULES
    assert settings["quietHandover"] is True
    assert projection["agents"][3]["voiceAddition"] == "Synthetic voice addition"
    assert all("voiceAddition" in a and "textAddition" in a for a in projection["agents"])
    policy = {
        key: projection[key]
        for key in (
            "schemaVersion",
            "agents",
            "communities",
            "relationships",
            "communityConnections",
            "tools",
            "mcpConnections",
            "conversationSettings",
        )
    }
    assert projection["policyHash"] == hashlib.sha256(encoded(policy)).hexdigest()
    # Only the stored values were written; the defaults appear in the projection alone.
    assert repository.snapshot["conversationSettings"] == graph["conversationSettings"]
    assert repository.snapshot["schemaVersion"] == "2.2.0"


def test_v2_runtime_projection_has_independent_hash_and_no_layout(runtime_configuration_api):
    from management_app.adapters.configuration_repository import graph_document
    from management_app.services.configuration_validation import encoded

    client, identity, repository, telemetry = runtime_configuration_api
    headers = {"X-Fixture-Identity": "runtime"}
    response = client.get("/v2/runtime/configuration", headers=headers)
    assert response.status_code == 200
    projection = response.json()
    assert projection["schemaVersion"] == "2.1.0" and "layout" not in projection
    assert (
        projection["graphHash"]
        == hashlib.sha256(encoded(graph_document(repository.snapshot))).hexdigest()
    )
    policy = {
        key: value for key, value in graph_document(repository.snapshot).items() if key != "layout"
    }
    assert projection["policyHash"] == hashlib.sha256(encoded(resolved(policy))).hexdigest()
    assert projection["id"] == repository.snapshot["id"]
    assert response.headers["etag"] == repository.etag
    headers["If-None-Match"] = repository.etag
    unchanged = client.get("/v2/runtime/configuration", headers=headers)
    assert unchanged.status_code == 304 and unchanged.content == b""
    assert identity.calls == repository.reads == 2
    assert client.get("/v1/runtime/configuration", headers=headers).status_code == 404


def test_v2_runtime_projection_omits_secret_resolution_material(
    runtime_configuration_api, capability_graph, validator
):
    from management_app.adapters.configuration_repository import graph_document, publication
    from management_app.services.configuration_validation import encoded

    client, identity, repository, telemetry = runtime_configuration_api
    repository.snapshot = publication(
        validator,
        capability_graph,
        actor="manager-fixture",
        previous=None,
        authority=None,
        actor_name="Example Editor",
    )[0]
    assert repository.snapshot["publication"]["publishedByName"] == "Example Editor"
    before = copy.deepcopy(repository.snapshot)
    response = client.get("/v2/runtime/configuration", headers={"X-Fixture-Identity": "runtime"})
    assert response.status_code == 200
    projection = response.json()
    assert "publication" not in projection and "Example Editor" not in response.text
    assert "agentStamps" not in response.text and "warnings" not in projection
    assert "secret://" not in response.text and "credentialRef" not in response.text
    assert "layout" not in projection and repository.snapshot == before
    policy = graph_document(before)
    policy.pop("layout")
    for connection in policy["mcpConnections"]:
        connection.pop("credentialRef")
    assert projection["policyHash"] == hashlib.sha256(encoded(resolved(policy))).hexdigest()
    assert projection["graphHash"] == hashlib.sha256(encoded(graph_document(before))).hexdigest()
    assert projection["mcpConnections"][0]["connectionId"] == "connection-one"


@pytest.fixture
def runtime_configuration_api(configuration_graph, validator):
    from management_app.adapters.configuration_repository import publication
    from management_app.api.runtime_configuration import create_router
    from management_app.observability import Telemetry

    class Identity:
        calls = 0

        async def require_runtime(self, request):
            self.calls += 1
            actor = request.headers.get("X-Fixture-Identity")
            if actor != "runtime":
                raise HTTPException(403 if actor else 401, "synthetic-private-auth-details")
            return "runtime-fixture"

    class Repository:
        reads = 0
        failure = None
        etag = '"active-fixture"'
        snapshot = publication(
            validator, configuration_graph, actor="manager-fixture", previous=None, authority=None
        )[0]

        async def read_active(self):
            self.reads += 1
            if self.failure == "timeout":
                raise TimeoutError("synthetic-private-timeout-details")
            if self.failure == "unavailable":
                raise RuntimeError("synthetic-private-storage-details")
            return None if self.failure == "missing" else (copy.deepcopy(self.snapshot), self.etag)

    identity, repository = Identity(), Repository()
    telemetry = Telemetry("management", "local")
    application = FastAPI()
    application.include_router(
        create_router(repository=repository, identity=identity, telemetry=telemetry)
    )
    with TestClient(application) as client:
        yield client, identity, repository, telemetry
    telemetry.close()


def test_default_app_does_not_mount_runtime_snapshots():
    from management_app.management_app import create_app

    application = create_app()
    assert PATH not in {getattr(route, "path", "") for route in application._api.routes}


@pytest.mark.parametrize("actor", [None, "manager", "anonymous", "operations"])
def test_authentication_precedes_body_condition_and_private_store_reads(
    runtime_configuration_api, actor
):
    client, identity, repository, telemetry = runtime_configuration_api
    headers = {"If-None-Match": repository.etag}
    if actor:
        headers["X-Fixture-Identity"] = actor
    response = client.request(
        "GET",
        PATH + "?override=synthetic-private-input",
        headers=headers,
        content="synthetic-private-body",
    )
    assert response.status_code == (403 if actor else 401)
    assert repository.reads == 0
    assert "synthetic-private" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_snapshot_is_exact_integrity_checked_revision_with_private_cache_headers(
    runtime_configuration_api,
):
    from management_app.adapters.configuration_repository import graph_document
    from management_app.services.configuration_validation import encoded

    client, identity, repository, telemetry = runtime_configuration_api
    response = client.get(PATH, headers={"X-Fixture-Identity": "runtime"})
    assert response.status_code == 200
    expected = copy.deepcopy(repository.snapshot)
    expected.pop("layout")
    expected.pop("publication")
    expected |= resolved(expected)
    expected["policyHash"] = hashlib.sha256(encoded(graph_document(expected))).hexdigest()
    assert response.json() == expected
    assert response.headers["etag"] == repository.etag
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["vary"] == "Authorization"
    assert response.json()["schemaVersion"] == "2.1.0"
    assert (
        response.json()["graphHash"]
        == hashlib.sha256(encoded(graph_document(repository.snapshot))).hexdigest()
    )
    assert response.json()["id"].startswith("revision-")


def test_not_modified_has_no_body_but_still_authenticates_and_reads_current_authority(
    runtime_configuration_api,
):
    client, identity, repository, telemetry = runtime_configuration_api
    response = client.get(
        PATH, headers={"X-Fixture-Identity": "runtime", "If-None-Match": repository.etag}
    )
    assert response.status_code == 304 and response.content == b""
    assert identity.calls == repository.reads == 1
    assert response.headers["etag"] == repository.etag
    assert response.headers["cache-control"] == "private, no-cache"


def test_different_pointer_etag_returns_the_complete_current_snapshot(runtime_configuration_api):
    client, identity, repository, telemetry = runtime_configuration_api
    response = client.get(
        PATH, headers={"X-Fixture-Identity": "runtime", "If-None-Match": '"older-fixture"'}
    )
    assert response.status_code == 200 and response.json()["id"] == repository.snapshot["id"]
    assert "layout" not in response.json() and "policyHash" in response.json()


@pytest.mark.parametrize(
    "failure,status", [("missing", 503), ("unavailable", 503), ("timeout", 504)]
)
def test_dependency_failures_never_claim_not_modified_or_reflect_details(
    runtime_configuration_api, failure, status
):
    client, identity, repository, telemetry = runtime_configuration_api
    repository.failure = failure
    response = client.get(
        PATH, headers={"X-Fixture-Identity": "runtime", "If-None-Match": repository.etag}
    )
    assert response.status_code == status and response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "synthetic-private" not in response.text


@pytest.mark.parametrize("corruption", ["hash", "schema", "organization", "extra", "etag"])
def test_corrupt_store_revision_is_not_returned_even_with_matching_condition(
    runtime_configuration_api, corruption
):
    client, identity, repository, telemetry = runtime_configuration_api
    if corruption == "hash":
        repository.snapshot["agents"][0]["prompt"] += "synthetic-private-corruption"
    elif corruption == "schema":
        repository.snapshot["schemaVersion"] = "unsupported"
    elif corruption == "organization":
        repository.snapshot["organizationId"] = "different"
    elif corruption == "extra":
        repository.snapshot["transcript"] = "synthetic-private-content"
    else:
        repository.etag = "synthetic-private-etag"
    response = client.get(
        PATH, headers={"X-Fixture-Identity": "runtime", "If-None-Match": '"active-fixture"'}
    )
    assert response.status_code == 503
    assert "synthetic-private" not in response.text


@pytest.mark.parametrize("kind", ["query", "body", "condition"])
def test_unsupported_request_data_is_rejected_without_reflection(runtime_configuration_api, kind):
    client, identity, repository, telemetry = runtime_configuration_api
    headers = {"X-Fixture-Identity": "runtime"}
    path, content = PATH, b""
    if kind == "query":
        path += "?secret=synthetic-private-input"
    elif kind == "body":
        content = b"synthetic-private-input"
    else:
        headers["If-None-Match"] = "synthetic-private-input"
    response = client.request("GET", path, headers=headers, content=content)
    assert response.status_code == 422 and repository.reads == 0
    assert "synthetic-private" not in response.text


def test_revision_prompts_and_store_errors_do_not_enter_trace_events(runtime_configuration_api):
    client, identity, repository, telemetry = runtime_configuration_api
    assert client.get(PATH, headers={"X-Fixture-Identity": "runtime"}).status_code == 200
    repository.failure = "unavailable"
    assert client.get(PATH, headers={"X-Fixture-Identity": "runtime"}).status_code == 503
    capture = repr(telemetry.events) + repr(
        [(span.name, dict(span.attributes or {})) for span in telemetry.spans]
    )
    assert "synthetic-private" not in capture
    assert all(agent["prompt"] not in capture for agent in repository.snapshot["agents"])
