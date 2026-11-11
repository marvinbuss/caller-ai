"""Sign-in return paths, role-aware sessions, the session profile and session extension."""

import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.unit.test_security import (  # noqa: F401 - shared fixtures
    CLIENT,
    ORIGIN,
    TENANT,
    security,
    signed,
    signing_key,
)

TESTER = "Conversation.Tester"


def sign_in_app(identity, signing_key, *, roles, telemetry=None, **claims):
    from management_app.security import LoginFlow, create_auth_router

    class Oidc:
        async def begin(self, state):
            return LoginFlow(
                url=f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state={state}",
                state=state,
                private=None,
            )

        async def finish(self, flow, response):
            token = signed(
                signing_key, aud=CLIENT, nonce="fixture-nonce", scp=None, roles=roles, **claims
            )
            return token, "fixture-nonce"

    app = FastAPI()
    app.include_router(
        create_auth_router(identity=identity, oidc=Oidc(), test_role=TESTER, telemetry=telemetry)
    )
    return app


def complete(client, return_to=None):
    started = client.get(
        "/auth/login",
        params={"returnTo": return_to} if return_to is not None else None,
        follow_redirects=False,
    )
    state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
    return client.post(
        "/auth/callback", data={"state": state, "code": str(uuid4())}, follow_redirects=False
    )


@pytest.mark.parametrize(
    "return_to,location",
    [
        (None, "/"),
        ("/status", "/status"),
        ("/communities", "/communities"),
        ("/settings", "/settings"),
        ("/test", "/test"),
        ("https://evil.example/status", "/"),
        ("//evil.example", "/"),
        ("\\\\evil.example", "/"),
        ("/status?next=//evil", "/"),
        ("/status#x", "/"),
        ("/login", "/"),
        ("/unknown", "/"),
    ],
)
def test_callback_returns_only_to_an_allowed_page(security, signing_key, return_to, location):
    identity, _keys = security
    app = sign_in_app(identity, signing_key, roles=["Configuration.Manager"])
    with TestClient(app, base_url=ORIGIN) as client:
        response = complete(client, return_to)
    assert response.status_code == 303 and response.headers["Location"] == location


@pytest.mark.parametrize(
    "roles,expected_roles,pages",
    [
        (
            ["Configuration.Manager", TESTER],
            ["editor", "tester"],
            ["dashboard", "communities", "test", "status", "settings"],
        ),
        (["Configuration.Manager"], ["editor"], ["dashboard", "communities", "status", "settings"]),
        ([TESTER], ["tester"], ["dashboard", "status"]),
        ([], [], []),
        (None, [], []),
    ],
)
def test_any_role_mix_signs_in_and_the_session_reports_its_profile(
    security, signing_key, roles, expected_roles, pages
):
    identity, _keys = security
    claims = {"name": "Example Editor", "preferred_username": "editor@example.invalid"}
    app = sign_in_app(identity, signing_key, roles=roles, **claims)
    with TestClient(app, base_url=ORIGIN) as client:
        assert complete(client).status_code == 303
        body = client.get("/auth/session").json()
    assert body["roles"] == expected_roles and body["pages"] == pages
    assert body["displayName"] == "Example Editor"
    assert body["account"] == "editor@example.invalid"
    assert body["expiresAt"].endswith("Z") and body["tokenExpiresAt"].endswith("Z")
    assert body["expiresAt"] <= body["tokenExpiresAt"]


def test_tester_only_switch_ships_off_and_opens_the_test_page(security, signing_key, monkeypatch):
    from management_app import security as module

    assert module.TESTER_ONLY_TESTS is False
    identity, _keys = security
    app = sign_in_app(identity, signing_key, roles=[TESTER])
    with TestClient(app, base_url=ORIGIN) as client:
        complete(client)
        assert client.get("/auth/session").json()["pages"] == ["dashboard", "status"]
        monkeypatch.setattr(module, "TESTER_ONLY_TESTS", True)
        assert client.get("/auth/session").json()["pages"] == ["dashboard", "test", "status"]


@pytest.mark.parametrize("roles", [[TESTER], []])
def test_configuration_access_still_requires_the_editor_role(security, signing_key, roles):
    from tests.unit.test_security import http_client

    identity, _keys = security
    principal = identity.sessions.issue(
        __import__("management_app.security", fromlist=["Principal"]).Principal(
            str(uuid4()), TENANT, int(time.time()) + 300, tuple(roles)
        )
    )
    with http_client(identity) as client:
        client.cookies.set(identity.cookie_name, principal.cookie)
        assert client.get("/private").status_code == 403


def test_session_extend_requires_csrf_and_is_capped_by_the_token(security, signing_key):
    from management_app.security import Principal, create_auth_router

    identity, _keys = security
    now = [1000.0]
    identity.sessions.clock = lambda: now[0]
    ticket = identity.sessions.issue(
        Principal(str(uuid4()), TENANT, 1000 + 1200, ("Configuration.Manager",))
    )
    app = FastAPI()
    app.include_router(create_auth_router(identity=identity, oidc=None))
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set(identity.cookie_name, ticket.cookie)
        assert client.post("/auth/session/extend", headers={"Origin": ORIGIN}).status_code == 403
        now[0] = 1600.0
        headers = {"Origin": ORIGIN, "X-CSRF-Token": ticket.csrf}
        extended = client.post("/auth/session/extend", headers=headers)
        assert extended.status_code == 200
        # now + 15 minutes would be 2500; the sign-in token ends at 2200.
        assert extended.json()["expiresAt"] == extended.json()["tokenExpiresAt"]
        now[0] = 2300.0
        expired = client.post("/auth/session/extend", headers=headers)
        assert expired.status_code == 401 and expired.json()["detail"] == "session_expired"


def test_session_extend_moves_expiry_to_fifteen_minutes_from_now(security):
    from management_app.security import Principal, create_auth_router

    identity, _keys = security
    now = [1000.0]
    identity.sessions.clock = lambda: now[0]
    ticket = identity.sessions.issue(
        Principal(str(uuid4()), TENANT, 1000 + 7200, ("Configuration.Manager",))
    )
    app = FastAPI()
    app.include_router(create_auth_router(identity=identity, oidc=None))
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set(identity.cookie_name, ticket.cookie)
        now[0] = 1600.0
        extended = client.post(
            "/auth/session/extend", headers={"Origin": ORIGIN, "X-CSRF-Token": ticket.csrf}
        )
    assert extended.status_code == 200
    assert identity.sessions.resolve(ticket.cookie).expires_at == 1600.0 + 900


def test_frontend_events_carry_no_names_accounts_or_tokens(security, signing_key):
    from management_app.observability import Telemetry

    identity, _keys = security
    telemetry = Telemetry("management", "local")
    claims = {"name": "Private Person", "preferred_username": "private@example.invalid"}
    app = sign_in_app(identity, signing_key, roles=[TESTER], telemetry=telemetry, **claims)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/auth/session", params={"page": "status"}).status_code == 401
        complete(client)
        client.get("/auth/session", params={"page": "status"})
        client.get("/auth/session", params={"page": "communities"})
        client.get("/auth/session", params={"page": "<script>"})
    events = [event for event in telemetry.events if event["event"] != "request"]
    assert [(event["event"], event["outcome"], event.get("page")) for event in events] == [
        ("ui.page_view", "login", "status"),
        ("auth.sign_in", "success", None),
        ("ui.page_view", "shown", "status"),
        ("ui.page_view", "role_required", "communities"),
    ]
    text = repr(events)
    assert "Private" not in text and "private@" not in text and "eyJ" not in text
    telemetry.close()
