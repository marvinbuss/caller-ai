"""Spike S-14: an application with the tester role may read and test, and nothing more (FR-053)."""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from management_app.live import management_request
from tests.integration.test_live_composition import (  # noqa: F401  (fixtures and helpers)
    TENANT,
    compose,
    reflex_environment,
    runtime_token,
    settings_for,
)

ORIGIN = "https://management.example.azurecontainerapps.io"
TICKET = {"mode": "text", "requestId": "tester-app-1"}


def app_token(*roles: str, **changes: Any) -> str:
    return runtime_token(roles=list(roles), **changes)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    settings = settings_for(monkeypatch)
    managed = asyncio.run(compose(settings))
    with TestClient(managed.api, base_url=ORIGIN) as test_client:
        yield test_client


def test_an_application_with_the_tester_role_can_read_the_configuration(client) -> None:
    response = client.get("/v2/configuration", headers=bearer(app_token("Caller.Tester")))
    assert response.status_code == 200 and "etag" in response.headers


def test_the_same_application_can_never_publish(client) -> None:
    current = client.get("/v2/configuration", headers=bearer(app_token("Caller.Tester")))
    response = client.put(
        "/v2/configuration",
        json=current.json(),
        headers={**bearer(app_token("Caller.Tester")), "If-Match": current.headers["etag"]},
    )
    assert response.status_code in {401, 403}
    again = client.get("/v2/configuration", headers=bearer(app_token("Caller.Tester")))
    assert again.headers["etag"] == current.headers["etag"]


@pytest.mark.parametrize(
    "token",
    [
        app_token("Caller.Runtime"),
        app_token("Caller.Editor"),
        app_token(),
        app_token("Caller.Tester", idtyp="user"),
        app_token("Caller.Tester", scp="Configuration.ReadWrite"),
        app_token("Caller.Tester", aud="00000000-0000-4000-8000-0000000000ff"),
        app_token("Caller.Tester", tid="00000000-0000-4000-8000-0000000000ee"),
        app_token("Caller.Tester", exp=1),
        app_token("Caller.Tester", ver="1.0"),
    ],
)
def test_any_other_token_is_refused_with_a_fixed_status(client, token: str) -> None:
    response = client.get("/v2/configuration", headers=bearer(token))
    assert response.status_code in {401, 403}
    assert token not in response.text


def test_an_application_token_is_not_accepted_next_to_a_cookie(client) -> None:
    headers = {**bearer(app_token("Caller.Tester")), "Cookie": "__Host-caller-session=" + "a" * 43}
    assert client.get("/v2/configuration", headers=headers).status_code in {401, 403}


def test_an_application_with_the_tester_role_receives_a_ticket_without_a_browser(client) -> None:
    response = client.post(
        "/v1/console/tickets", json=TICKET, headers=bearer(app_token("Caller.Tester"))
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "text" and response.json()["permit"]


@pytest.mark.parametrize(
    "token",
    [
        app_token("Caller.Editor"),
        app_token("Caller.Runtime"),
        app_token("Caller.Tester", idtyp="user"),
        app_token("Caller.Tester", scp="Configuration.ReadWrite"),
    ],
)
def test_other_tokens_receive_no_ticket(client, token: str) -> None:
    response = client.post("/v1/console/tickets", json=TICKET, headers=bearer(token))
    assert response.status_code in {401, 403}


def test_a_foreign_origin_never_helps_an_application_ticket(client) -> None:
    headers = {**bearer(app_token("Caller.Tester")), "Origin": "https://foreign.example.net"}
    assert client.post("/v1/console/tickets", json=TICKET, headers=headers).status_code == 403


def test_the_bearer_path_does_not_widen_the_runtime_configuration_endpoint(client) -> None:
    response = client.get("/v2/runtime/configuration", headers=bearer(app_token("Caller.Tester")))
    assert response.status_code == 403


def test_the_request_binds_the_tester_role_from_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(monkeypatch)
    request = management_request(settings.live())
    assert request.tests is not None and request.tests.test_role == "Caller.Tester"
    assert request.policy.tenant_id == TENANT
