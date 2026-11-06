"""Live test console: tickets are signed with the configured permit key by a tester only."""

import asyncio
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from management_app.health import lifecycle
from management_app.security import COOKIE, Principal
from tests.integration.test_live_composition import (  # noqa: F401  (fixtures and helpers)
    PERMIT_KEY,
    compose,
    reflex_environment,
    settings_for,
)

ORIGIN = "https://management.example.azurecontainerapps.io"
TENANT = "00000000-0000-4000-8000-000000000002"
ACTOR = "00000000-0000-4000-8000-000000000041"


def signed_in(managed: Any, roles: tuple[str, ...]) -> tuple[str, str]:
    store = managed.api.state.management_identity.sessions
    ticket = store.issue(Principal(ACTOR, TENANT, int(time.time()) + 600, roles))
    return ticket.cookie, ticket.csrf


async def request_ticket(managed: Any, roles: tuple[str, ...]) -> httpx.Response:
    cookie, csrf = signed_in(managed, roles)
    transport = httpx.ASGITransport(app=managed.api)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        return await client.post(
            "/v1/console/tickets",
            json={"mode": "text", "requestId": "live-ticket-1"},
            headers={"Origin": ORIGIN, "Cookie": f"{COOKIE}={cookie}", "X-CSRF-Token": csrf},
        )


def test_a_tester_receives_a_permit_signed_with_the_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(monkeypatch)

    async def scenario() -> httpx.Response:
        managed = await compose(settings)
        async with lifecycle(managed.api, settings):
            return await request_ticket(managed, ("Caller.Editor", "Caller.Tester"))

    response = asyncio.run(scenario())
    assert response.status_code == 200
    permit = response.json()["permit"]
    assert jwt.get_unverified_header(permit) == {"alg": "RS256", "typ": "JWT", "kid": "key-1"}
    public = PERMIT_KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    claims = jwt.decode(
        permit,
        public,
        algorithms=["RS256"],
        audience="https://runtime.example.azurecontainerapps.io",
        issuer=ORIGIN,
    )
    assert claims["mode"] == "text" and claims["tenantId"] == TENANT and claims["actorId"] == ACTOR


@pytest.mark.parametrize("roles", [("Caller.Editor",), ("Caller.Tester",), ("Caller.Runtime",), ()])
def test_a_session_without_both_roles_gets_no_permit(
    monkeypatch: pytest.MonkeyPatch, roles: tuple[str, ...]
) -> None:
    settings = settings_for(monkeypatch)

    async def scenario() -> httpx.Response:
        managed = await compose(settings)
        async with lifecycle(managed.api, settings):
            try:
                return await request_ticket(managed, roles)
            except Exception as error:
                return httpx.Response(getattr(getattr(error, "detail", None), "status", 401))

    assert asyncio.run(scenario()).status_code in {401, 403}
