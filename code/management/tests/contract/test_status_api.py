"""Management `GET /v1/status`: editor or tester only, wraps the runtime body, cached."""

# ruff: noqa: F811 - pytest injects the fixtures imported from test_security

import asyncio
import time
from uuid import uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken
from fastapi import FastAPI
from fastapi.testclient import TestClient

from management_app.adapters.runtime_status_client import RuntimeStatusClient
from management_app.api.status import create_router
from management_app.observability import Telemetry
from management_app.security import COOKIE, Principal
from tests.unit.test_security import (  # noqa: F401 - shared fixtures
    ORIGIN,
    TENANT,
    security,
    signing_key,
)

TESTER = "Conversation.Tester"
RUNTIME_BODY = {
    "text": {
        "endpointHost": "caller-int-foundry.openai.azure.com",
        "deployment": "gpt-6-1-sol",
        "reachable": True,
        "checkedAt": "2026-11-11T10:00:00Z",
        "lastCallOutcome": "success",
        "lastCallAt": "2026-11-11T09:58:12Z",
    },
    "voice": {
        "endpointHost": "caller-int-foundry.openai.azure.com",
        "region": "swedencentral",
        "model": "gpt-live-1",
        "reachable": False,
        "checkedAt": "2026-11-11T10:00:00Z",
        "lastCallOutcome": None,
        "lastCallAt": None,
    },
    "supportedVoices": [
        {
            "id": "marin",
            "name": "Marin",
            "language": "English",
            "accent": None,
            "presentation": None,
        }
    ],
    "defaults": {"voice": "marin", "maxOutputTokens": 1024, "idleTimeoutSeconds": 120},
    "limits": {"maxOutputTokens": 1024, "idleTimeoutSeconds": {"min": 30, "max": 120}},
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Runtime:
    def __init__(self) -> None:
        self.calls = 0
        self.up = True

    async def fetch(self) -> dict:
        self.calls += 1
        if not self.up:
            raise PermissionError("runtime_status_unavailable")
        return RUNTIME_BODY


def build(identity, *, telemetry=None, test_role=TESTER):
    runtime, clock = Runtime(), Clock()
    app = FastAPI()
    app.include_router(
        create_router(
            identity=identity,
            fetch=runtime.fetch,
            test_role=test_role,
            telemetry=telemetry,
            monotonic=clock,
        )
    )
    return TestClient(app, base_url=ORIGIN), runtime, clock


def signed_in(identity, client, *roles: str) -> None:
    ticket = identity.sessions.issue(
        Principal(str(uuid4()), TENANT, int(time.time()) + 900, tuple(roles))
    )
    client.cookies.set(COOKIE, ticket.cookie)


@pytest.mark.parametrize(
    "roles", [("Configuration.Manager",), (TESTER,), ("Configuration.Manager", TESTER)]
)
def test_editor_or_tester_may_read_status(security, roles) -> None:
    identity, _keys = security
    client, _runtime, _clock = build(identity)
    signed_in(identity, client, *roles)
    response = client.get("/v1/status", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["runtime"] == {"reachable": True, "checkedAt": body["runtime"]["checkedAt"]}
    assert body["status"] == RUNTIME_BODY


def test_other_roles_and_anonymous_are_refused(security) -> None:
    identity, _keys = security
    client, runtime, _clock = build(identity)
    assert client.get("/v1/status").status_code == 401
    signed_in(identity, client, "Some.Other")
    assert client.get("/v1/status").status_code == 403
    assert runtime.calls == 0


def test_tester_is_refused_when_tests_are_not_configured(security) -> None:
    identity, _keys = security
    client, _runtime, _clock = build(identity, test_role=None)
    signed_in(identity, client, TESTER)
    assert client.get("/v1/status").status_code == 403


def test_cross_site_reads_are_refused(security) -> None:
    identity, _keys = security
    client, _runtime, _clock = build(identity)
    signed_in(identity, client, "Configuration.Manager")
    assert client.get("/v1/status", headers={"Origin": "https://evil.example"}).status_code == 403


def test_unreachable_runtime_reports_the_last_success(security) -> None:
    identity, _keys = security
    client, runtime, clock = build(identity)
    signed_in(identity, client, "Configuration.Manager")
    first = client.get("/v1/status").json()
    runtime.up = False
    clock.now += 61
    body = client.get("/v1/status").json()
    assert body["status"] is None
    assert body["runtime"]["reachable"] is False
    assert body["runtime"]["lastSuccessAt"] == first["runtime"]["checkedAt"]


def test_never_reached_runtime_has_no_last_success(security) -> None:
    identity, _keys = security
    client, runtime, _clock = build(identity)
    runtime.up = False
    signed_in(identity, client, TESTER)
    body = client.get("/v1/status").json()
    assert body == {
        "runtime": {"reachable": False, "checkedAt": body["runtime"]["checkedAt"]},
        "status": None,
    }


def test_cache_holds_for_sixty_seconds(security) -> None:
    identity, _keys = security
    client, runtime, clock = build(identity)
    signed_in(identity, client, "Configuration.Manager")
    client.get("/v1/status")
    clock.now += 59
    client.get("/v1/status")
    assert runtime.calls == 1
    clock.now += 2
    client.get("/v1/status")
    assert runtime.calls == 2


def test_refresh_bypasses_the_cache_at_most_once_per_ten_seconds_per_session(security) -> None:
    identity, _keys = security
    client, runtime, clock = build(identity)
    signed_in(identity, client, "Configuration.Manager")
    client.get("/v1/status")
    client.get("/v1/status", params={"refresh": "1"})
    assert runtime.calls == 2
    clock.now += 5
    client.get("/v1/status", params={"refresh": "1"})
    assert runtime.calls == 2
    clock.now += 5
    client.get("/v1/status", params={"refresh": "1"})
    assert runtime.calls == 3
    # Another session has its own allowance.
    other = TestClient(client.app, base_url=ORIGIN)
    signed_in(identity, other, TESTER)
    other.get("/v1/status", params={"refresh": "1"})
    assert runtime.calls == 4


def test_each_runtime_check_records_a_status_check_event(security) -> None:
    identity, _keys = security
    telemetry = Telemetry("management", "local")
    client, runtime, clock = build(identity, telemetry=telemetry)
    signed_in(identity, client, "Configuration.Manager")
    client.get("/v1/status")
    runtime.up = False
    clock.now += 61
    client.get("/v1/status")
    events = [event for event in telemetry.events if event["event"] == "status.check"]
    assert [(event["outcome"], event["text"], event["voice"]) for event in events] == [
        ("success", "reachable", "unreachable"),
        ("failure", "unknown", "unknown"),
    ]


class Credential:
    def __init__(self) -> None:
        self.scopes: list[tuple[str, ...]] = []

    async def get_token(self, *scopes: str) -> AccessToken:
        self.scopes.append(scopes)
        return AccessToken("workload-token", int(time.time()) + 900)


def client_for(handler) -> tuple[RuntimeStatusClient, Credential]:
    credential = Credential()
    return (
        RuntimeStatusClient(
            origin="runtime.example",
            audience="runtime-audience",
            credential=credential,
            transport=httpx.MockTransport(handler),
        ),
        credential,
    )


def test_client_sends_the_workload_token_and_checks_the_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=RUNTIME_BODY)

    client, credential = client_for(handler)
    assert asyncio.run(client.fetch()) == RUNTIME_BODY
    assert str(seen[0].url) == "https://runtime.example/v1/status"
    assert seen[0].headers["authorization"] == "Bearer workload-token"
    assert credential.scopes == [("api://runtime-audience/.default",)]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"error": {"code": "status_unavailable"}}),
        httpx.Response(200, json={"text": {}}),
        httpx.Response(200, json={**RUNTIME_BODY, "secret": "x"}),
        httpx.Response(
            200,
            json={
                **RUNTIME_BODY,
                "text": {**RUNTIME_BODY["text"], "endpointHost": "https://host/path"},
            },
        ),
        httpx.Response(200, content=b"x" * (64 * 1024 + 1)),
    ],
)
def test_client_refuses_failures_and_unexpected_bodies(response) -> None:
    client, _credential = client_for(lambda request: response)
    with pytest.raises((PermissionError, ValueError)):
        asyncio.run(client.fetch())
