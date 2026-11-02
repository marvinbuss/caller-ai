"""Actual signed JWTs and opaque sessions; no Entra discovery or test auth in production."""

import asyncio
import json
import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

TENANT = str(uuid4())
CLIENT = str(uuid4())
AUDIENCE = str(uuid4())
ACTOR = str(uuid4())
ORIGIN = "https://management.example.invalid"
RUNTIME_AUDIENCE = str(uuid4())
RUNTIME_CLIENT = str(uuid4())


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def security(signing_key):
    from management_app.security import EntraPolicy, IdentityService, SessionStore, TokenVerifier

    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    public.update({"kid": "fixture-signing-key", "use": "sig", "alg": "RS256"})

    class Keys:
        calls = 0

        async def signing_keys(self):
            self.calls += 1
            return {"keys": [public]}

    policy = EntraPolicy(
        tenant_id=TENANT,
        client_id=CLIENT,
        api_audience=AUDIENCE,
        origin=ORIGIN,
        manager_role="Configuration.Manager",
        manager_scope="Configuration.ReadWrite",
    )
    keys = Keys()
    verifier = TokenVerifier(policy=policy, keys=keys)
    sessions = SessionStore(policy=policy)
    return IdentityService(policy=policy, verifier=verifier, sessions=sessions), keys


def signed(signing_key, **changes):
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "tid": TENANT,
        "aud": AUDIENCE,
        "oid": ACTOR,
        "sub": "fixture-subject",
        "iat": now - 5,
        "nbf": now - 5,
        "exp": now + 300,
        "ver": "2.0",
        "roles": ["Configuration.Manager"],
        "scp": "Configuration.ReadWrite",
    }
    claims.update(changes)
    return jwt.encode(
        claims, signing_key, algorithm="RS256", headers={"kid": "fixture-signing-key"}
    )


def http_client(identity):
    app = FastAPI()

    @app.api_route("/private", methods=["GET", "PUT"])
    async def private(request: Request):
        actor = await identity.require_manager(request)
        return {"actor_ref": actor}

    return TestClient(app, base_url=ORIGIN)


def test_real_signed_access_token_authorizes_exact_manager(security, signing_key) -> None:
    identity, keys = security
    with http_client(identity) as client:
        response = client.get(
            "/private", headers={"Authorization": "Bearer " + signed(signing_key)}
        )
    assert response.status_code == 200 and response.json() == {"actor_ref": ACTOR}
    assert keys.calls == 1


@pytest.mark.parametrize(
    "mutation",
    [None, "manager-audience", "manager-role", "delegated", "user", "wrong-client", "expired"],
)
def test_runtime_identity_requires_distinct_app_only_audience_and_allowed_client(
    security, signing_key, mutation
):
    from management_app.security import RuntimeIdentity

    identity, keys = security
    runtime = RuntimeIdentity(
        verifier=identity.verifier,
        audience=RUNTIME_AUDIENCE,
        role="Capabilities.Execute",
        allowed_client_ids=frozenset({RUNTIME_CLIENT}),
    )
    changes = {
        "aud": RUNTIME_AUDIENCE,
        "roles": ["Capabilities.Execute"],
        "scp": None,
        "idtyp": "app",
        "azp": RUNTIME_CLIENT,
    }
    changes.update(
        {
            "manager-audience": {"aud": AUDIENCE},
            "manager-role": {"roles": ["Configuration.Manager"]},
            "delegated": {"scp": "Configuration.ReadWrite"},
            "user": {"idtyp": "user"},
            "wrong-client": {"azp": str(uuid4())},
            "expired": {"exp": int(time.time()) - 60},
        }.get(mutation, {})
    )
    token = signed(signing_key, **changes)
    application = FastAPI()

    @application.post("/execute")
    async def execute(request: Request):
        return {"actor": await runtime.require_runtime(request)}

    with TestClient(application, base_url=ORIGIN) as client:
        response = client.post("/execute", headers={"Authorization": "Bearer " + token})
    assert response.status_code == (
        200 if mutation is None else 403 if mutation == "manager-role" else 401
    )
    assert token not in response.text


@pytest.mark.parametrize(
    "mutation",
    [
        "issuer",
        "tenant",
        "audience",
        "expired",
        "future",
        "missing-exp",
        "signature",
        "algorithm",
        "id-token",
        "wrong-version",
    ],
)
def test_invalid_tokens_fail_without_private_diagnostics(security, signing_key, mutation) -> None:
    identity, keys = security
    changes = {
        "issuer": {"iss": "https://example.invalid/untrusted"},
        "tenant": {"tid": str(uuid4())},
        "audience": {"aud": CLIENT},
        "expired": {"exp": int(time.time()) - 120},
        "future": {"nbf": int(time.time()) + 300},
        "missing-exp": {"exp": None},
        "id-token": {"scp": None, "nonce": "fixture-nonce"},
        "wrong-version": {"ver": "1.0"},
    }
    token = signed(signing_key, **changes.get(mutation, {}))
    if mutation == "signature":
        token = signed(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    elif mutation == "algorithm":
        token = jwt.encode({"exp": int(time.time()) + 60}, str(uuid4()), algorithm="HS256")
    with http_client(identity) as client:
        response = client.get("/private", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401
    assert token not in response.text and ACTOR not in response.text


@pytest.mark.parametrize(
    "roles", [[], ["configuration.manager"], ["Runtime.Reader"], "Configuration.Manager"]
)
def test_missing_manager_role_is_denied(security, signing_key, roles) -> None:
    identity, keys = security
    with http_client(identity) as client:
        response = client.get(
            "/private", headers={"Authorization": "Bearer " + signed(signing_key, roles=roles)}
        )
    assert response.status_code == 403


def test_cookie_session_is_opaque_http_only_and_requires_csrf_on_mutation(
    security, signing_key
) -> None:
    identity, keys = security
    principal = asyncio.run(identity.verifier.verify_access(signed(signing_key)))
    ticket = identity.sessions.issue(principal)
    assert signed(signing_key) not in repr(ticket)
    with http_client(identity) as client:
        client.cookies.set(identity.cookie_name, ticket.cookie)
        assert client.get("/private").status_code == 200
        assert client.put("/private", headers={"Origin": ORIGIN}).status_code == 403
        assert (
            client.put(
                "/private",
                headers={
                    "Origin": "https://untrusted.example.invalid",
                    "X-CSRF-Token": ticket.csrf,
                },
            ).status_code
            == 403
        )
        assert (
            client.put(
                "/private", headers={"Origin": ORIGIN, "X-CSRF-Token": ticket.csrf}
            ).status_code
            == 200
        )
    assert ticket.cookie not in repr(identity.sessions) and ticket.csrf not in repr(
        identity.sessions
    )


def test_session_revocation_and_token_expiry_are_checked_again(security, signing_key) -> None:
    identity, keys = security
    principal = asyncio.run(identity.verifier.verify_access(signed(signing_key)))
    ticket = identity.sessions.issue(principal)
    identity.sessions.revoke(ticket.cookie)
    with http_client(identity) as client:
        client.cookies.set(identity.cookie_name, ticket.cookie)
        assert client.get("/private").status_code == 401


def test_bearer_and_cookie_cannot_select_different_identities(security, signing_key) -> None:
    identity, keys = security
    ticket = identity.sessions.issue(
        asyncio.run(identity.verifier.verify_access(signed(signing_key)))
    )
    with http_client(identity) as client:
        client.cookies.set(identity.cookie_name, ticket.cookie)
        response = client.get(
            "/private", headers={"Authorization": "Bearer " + signed(signing_key)}
        )
    assert response.status_code == 401


def test_no_environment_or_header_identity_bypass(security, monkeypatch) -> None:
    identity, keys = security
    monkeypatch.setenv("CALLER_TEST_IDENTITY", ACTOR)
    with http_client(identity) as client:
        response = client.get(
            "/private", headers={"X-Actor-ID": ACTOR, "X-Role": "Configuration.Manager"}
        )
    assert response.status_code == 401 and keys.calls == 0


def test_browser_token_requires_nonce_and_web_client_audience(security, signing_key) -> None:
    identity, keys = security
    token = signed(signing_key, aud=CLIENT, nonce="fixture-nonce", scp=None)
    principal = asyncio.run(identity.verifier.verify_identity(token, nonce="fixture-nonce"))
    assert principal.actor_id == ACTOR
    with pytest.raises(HTTPException) as denied:
        asyncio.run(identity.verifier.verify_identity(token, nonce="wrong-nonce"))
    assert denied.value.status_code == 401


def test_session_cookie_flags_and_expiration_are_enforced(security, signing_key) -> None:
    from starlette.responses import Response

    from management_app.security import SessionStore

    identity, keys = security
    now = [time.time()]
    store = SessionStore(policy=identity.policy, clock=lambda: now[0])
    principal = asyncio.run(identity.verifier.verify_access(signed(signing_key)))
    ticket = store.issue(principal)
    response = Response()
    store.set_cookie(response, ticket)
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "secure" in header and "samesite=strict" in header
    assert "domain=" not in header and "path=/" in header
    now[0] = principal.expires_at + 1
    with pytest.raises(HTTPException):
        store.resolve(ticket.cookie)


def test_fixed_tenant_jwks_endpoint_is_bounded_and_cached(security, signing_key) -> None:
    import httpx

    from management_app.adapters.entra_oidc import EntraSigningKeys

    identity, keys = security
    calls = []

    async def scenario():
        def reply(request):
            calls.append(str(request.url))
            assert request.headers.get("Authorization") is None
            return httpx.Response(200, json={"keys": [{"kid": "fixture"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            provider = EntraSigningKeys(policy=identity.policy, client=client)
            first = await provider.signing_keys()
            first["keys"].clear()
            assert (await provider.signing_keys())["keys"] == [{"kid": "fixture"}]

    asyncio.run(scenario())
    assert calls == [f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"]


@pytest.mark.parametrize("behavior", ["redirect", "oversize", "failure"])
def test_jwks_errors_never_follow_redirects_or_reuse_stale_data(security, behavior) -> None:
    import httpx

    from management_app.adapters.entra_oidc import EntraSigningKeys

    identity, keys = security

    async def scenario():
        def reply(request):
            if behavior == "redirect":
                return httpx.Response(
                    302, headers={"Location": "https://untrusted.example.invalid/keys"}
                )
            if behavior == "oversize":
                return httpx.Response(200, content=b"x" * (64 * 1024 + 1))
            return httpx.Response(500, text="synthetic-private-provider-details")

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            provider = EntraSigningKeys(policy=identity.policy, client=client)
            with pytest.raises(RuntimeError, match="^identity_authority_unavailable$"):
                await provider.signing_keys()

    asyncio.run(scenario())


def test_oidc_callback_requires_matching_cookie_state_and_nonce_then_is_single_use(
    security, signing_key
) -> None:
    from urllib.parse import parse_qs, urlsplit

    from management_app.security import LoginFlow, create_auth_router

    identity, keys = security

    class Oidc:
        exchanges = 0

        async def begin(self, state):
            return LoginFlow(
                url=f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state={state}",
                state=state,
                private={"nonce": "fixture-nonce"},
            )

        async def finish(self, flow, response):
            self.exchanges += 1
            return signed(signing_key, aud=CLIENT, nonce="fixture-nonce", scp=None), "fixture-nonce"

    oidc = Oidc()
    app = FastAPI()
    app.include_router(create_auth_router(identity=identity, oidc=oidc))
    with TestClient(app, base_url=ORIGIN) as client:
        started = client.get("/auth/login", follow_redirects=False)
        assert started.status_code == 303 and started.headers["Cache-Control"] == "no-store"
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        body = {"state": state, "code": str(uuid4())}
        rejected = client.post("/auth/callback", data={**body, "state": "incorrect-state"})
        assert rejected.status_code == 401 and oidc.exchanges == 0
        started = client.get("/auth/login", follow_redirects=False)
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        body["state"] = state
        accepted = client.post("/auth/callback", data=body, follow_redirects=False)
        assert accepted.status_code == 303 and accepted.headers["Location"] == "/configuration"
        assert identity.cookie_name in client.cookies
        assert "id_token" not in accepted.text and "access_token" not in accepted.text
        assert client.post("/auth/callback", data=body, follow_redirects=False).status_code == 401
        assert oidc.exchanges == 1


def test_reflex_guard_strips_credentials_and_denies_cross_actor_state_reuse(
    security, signing_key
) -> None:
    from management_app.security import ReflexEventGuard

    identity, keys = security
    first = identity.sessions.issue(
        asyncio.run(identity.verifier.verify_access(signed(signing_key)))
    )
    second = identity.sessions.issue(
        asyncio.run(identity.verifier.verify_access(signed(signing_key, oid=str(uuid4()))))
    )
    guard = ReflexEventGuard(identity=identity)
    state_token = str(uuid4())

    def environ(cookie):
        return {
            "QUERY_STRING": "token=" + state_token,
            "HTTP_COOKIE": identity.cookie_name + "=" + cookie,
            "asgi.scope": {
                "type": "websocket",
                "headers": [
                    (b"origin", ORIGIN.encode()),
                    (b"cookie", (identity.cookie_name + "=" + cookie).encode()),
                    (b"authorization", b"private-fixture-header"),
                    (b"user-agent", b"fixture-browser"),
                ],
            },
        }

    sanitized = guard.connect("sid-one", environ(first.cookie))
    assert "HTTP_COOKIE" not in sanitized
    headers = dict(sanitized["asgi.scope"]["headers"])
    assert b"cookie" not in headers and b"authorization" not in headers
    assert guard.authorize("sid-one", state_token) == ACTOR
    with pytest.raises(HTTPException):
        guard.connect("sid-two", environ(second.cookie))
    identity.sessions.revoke(first.cookie)
    with pytest.raises(HTTPException):
        guard.authorize("sid-one", state_token)


def test_session_csrf_is_stable_across_tabs(security, signing_key) -> None:
    from management_app.security import create_auth_router

    identity, keys = security
    ticket = identity.sessions.issue(
        asyncio.run(identity.verifier.verify_access(signed(signing_key)))
    )
    app = FastAPI()
    app.include_router(create_auth_router(identity=identity, oidc=None))
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set(identity.cookie_name, ticket.cookie)
        first, second = client.get("/auth/session"), client.get("/auth/session")
    assert first.status_code == second.status_code == 200
    assert first.json()["csrf"] == second.json()["csrf"] == ticket.csrf


@pytest.mark.parametrize("wrong_nonce", [False, True])
def test_actual_msal_code_flow_uses_pkce_and_validates_nonce(
    security, signing_key, wrong_nonce
) -> None:
    from urllib.parse import parse_qs, urlsplit

    import httpx
    from msal import ConfidentialClientApplication

    from management_app.adapters.entra_oidc import MsalCodeFlow

    identity, keys = security
    expected_nonce = [""]

    class Http:
        def get(self, url, **kwargs):
            assert str(url).startswith(f"https://login.microsoftonline.com/{TENANT}/")
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
                    "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
                    "issuer": identity.policy.issuer,
                    "jwks_uri": f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys",
                },
            )

        def post(self, url, **kwargs):
            assert kwargs["data"]["code_verifier"]
            nonce = "wrong-nonce" if wrong_nonce else expected_nonce[0]
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "access_token": str(uuid4()),
                    "id_token": signed(signing_key, aud=CLIENT, scp=None, nonce=nonce),
                },
            )

    def application():
        return ConfidentialClientApplication(
            client_id=CLIENT,
            client_credential=str(uuid4()),
            authority=f"https://login.microsoftonline.com/{TENANT}",
            instance_discovery=False,
            http_client=Http(),
            exclude_scopes=["offline_access"],
        )

    async def scenario():
        provider = MsalCodeFlow(policy=identity.policy, application_factory=application)
        flow = await provider.begin("fixture-state")
        query = parse_qs(urlsplit(flow.url).query)
        assert query["code_challenge_method"] == ["S256"] and query["code_challenge"][0]
        assert query["response_mode"] == ["form_post"]
        expected_nonce[0] = query["nonce"][0]
        if wrong_nonce:
            with pytest.raises(HTTPException) as denied:
                await provider.finish(flow, {"state": flow.state, "code": str(uuid4())})
            assert denied.value.status_code == 401
        else:
            token, nonce = await provider.finish(flow, {"state": flow.state, "code": str(uuid4())})
            assert (await identity.verifier.verify_identity(token, nonce=nonce)).actor_id == ACTOR

    asyncio.run(scenario())


def test_actual_reflex_namespace_redacts_headers_before_enqueue_and_rechecks_revocation(
    security, signing_key, monkeypatch, caplog
) -> None:
    from unittest.mock import AsyncMock

    from reflex import constants
    from reflex.app import EventNamespace, EventProcessor

    from management_app.management_app import create_app
    from management_app.security import (
        EventAuthorizationMiddleware,
        ReflexEventGuard,
        SecureEventNamespace,
    )

    identity, keys = security
    ticket = identity.sessions.issue(
        asyncio.run(identity.verifier.verify_access(signed(signing_key)))
    )
    application = create_app()
    asgi = application()
    enqueue = AsyncMock()
    monkeypatch.setattr(EventProcessor, "enqueue", enqueue)
    delegate = next(
        namespace
        for namespace in application.sio.namespace_handlers.values()
        if isinstance(namespace, EventNamespace)
    )
    guard = ReflexEventGuard(identity=identity)
    secured = SecureEventNamespace(delegate=delegate, guard=guard)
    application.sio.register_namespace(secured)
    token = str(uuid4())
    environ = {
        "QUERY_STRING": "token=" + token,
        "asgi.scope": {
            "type": "websocket",
            "headers": [
                (b"origin", ORIGIN.encode()),
                (b"cookie", (identity.cookie_name + "=" + ticket.cookie).encode()),
            ],
        },
    }

    async def scenario():
        assert (
            await secured.trigger_event(
                "connect",
                "malformed-sid",
                environ,
                {constants.CompileVars.CONNECT_AUTH_EVENT: "sensitive-fixture-bootstrap"},
            )
            is False
        )
        assert "sensitive-fixture-bootstrap" not in caplog.text
        await secured.trigger_event("connect", "fixture-sid", environ, None)
        await secured.trigger_event(
            "event",
            "fixture-sid",
            {
                "name": "fixture.refresh",
                "payload": {},
                "router_data": {
                    "pathname": "/",
                    "headers": {"authorization": "untrusted-client-header"},
                },
                "token": "forged-token",
            },
        )
        assert enqueue.await_count == 1
        event = enqueue.await_args.args[1]
        assert event.router_data[constants.RouteVar.CLIENT_TOKEN] == token
        assert "cookie" not in event.router_data["headers"]
        assert "authorization" not in event.router_data["headers"]
        middleware = EventAuthorizationMiddleware(guard=guard)
        assert await middleware.preprocess(application, None, event) is None
        identity.sessions.revoke(ticket.cookie)
        blocked = await middleware.preprocess(application, None, event)
        assert blocked is not None and blocked.final and not blocked.delta
        await secured.trigger_event(
            "event", "fixture-sid", {"name": "fixture.refresh", "payload": {}}
        )
        assert enqueue.await_count == 1
        await secured.trigger_event("disconnect", "fixture-sid", "fixture-cleanup")

    with TestClient(asgi):
        asyncio.run(scenario())


def test_protected_reflex_boundary_denies_uploads_without_dispatching(security) -> None:
    from management_app.security import ProtectedReflexBoundary

    identity, keys = security
    app = FastAPI()

    @app.post("/_upload")
    async def forbidden():
        pytest.fail("Unreviewed alternate event dispatch must not execute")

    with TestClient(ProtectedReflexBoundary(app)) as client:
        assert client.post("/_upload", content=b"fixture").status_code == 403


def test_identity_transport_rejects_an_unapproved_destination_before_network(security) -> None:
    from management_app.adapters.entra_oidc import BoundedEntraHttp

    identity, keys = security
    with pytest.raises(RuntimeError, match="^identity_authority_unavailable$"):
        BoundedEntraHttp(policy=identity.policy).post("https://untrusted.example.invalid/token")


def test_concurrent_login_starts_reserve_capacity_before_provider_wait(security) -> None:
    import httpx

    from management_app.security import LoginFlow, create_auth_router

    identity, keys = security

    class Oidc:
        async def begin(self, state):
            await asyncio.sleep(0)
            return LoginFlow(
                url=f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state={state}",
                state=state,
                private=None,
            )

    async def scenario():
        app = FastAPI()
        app.include_router(create_auth_router(identity=identity, oidc=Oidc()))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            outcomes = await asyncio.gather(*(client.get("/auth/login") for _ in range(105)))
        assert sum(response.status_code == 303 for response in outcomes) == 100
        assert sum(response.status_code == 503 for response in outcomes) == 5

    asyncio.run(scenario())


def test_callback_deadline_includes_slow_request_body(security) -> None:
    import httpx

    from management_app.security import LoginFlow, create_auth_router

    identity, keys = security

    class Oidc:
        async def begin(self, state):
            return LoginFlow(
                url=f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state={state}",
                state=state,
                private=None,
            )

    async def never_finishes():
        yield b"state="
        await asyncio.Event().wait()

    async def scenario():
        app = FastAPI()
        app.include_router(
            create_auth_router(identity=identity, oidc=Oidc(), operation_budget=0.03)
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            assert (await client.get("/auth/login")).status_code == 303
            response = await asyncio.wait_for(
                client.post(
                    "/auth/callback",
                    content=never_finishes(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ),
                0.3,
            )
            assert response.status_code == 503

    asyncio.run(scenario())


def test_real_identity_validation_publication_and_audit_roundtrip(
    security, signing_key, configuration_graph, validator
) -> None:
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.api.configuration import create_router
    from management_app.services.audit import AuditService

    identity, keys = security
    repository = LocalConfigurationRepository(validator=validator)
    asyncio.run(repository.initialize(configuration_graph, actor_id=ACTOR))
    audit = LocalAuditRepository()
    app = FastAPI()
    app.include_router(
        create_router(
            repository=repository,
            identity=identity,
            validator=validator,
            audit=AuditService(repository=audit),
        )
    )
    headers = {"Authorization": "Bearer " + signed(signing_key)}
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/v2/configuration").status_code == 401
        current = client.get("/v2/configuration", headers=headers)
        assert current.status_code == 200
        configuration_graph["agents"][3]["prompt"] = "Synthetic verified manager edit"
        saved = client.put(
            "/v2/configuration",
            json=configuration_graph,
            headers={**headers, "If-Match": current.headers["ETag"]},
        )
        assert saved.status_code == 200 and saved.json()["createdBy"] == ACTOR
        assert saved.headers["X-Audit-Status"] == "recorded"
        assert (
            client.put(
                "/v2/configuration",
                json=configuration_graph,
                headers={**headers, "If-Match": current.headers["ETag"]},
            ).status_code
            == 409
        )
        assert client.get("/v2/configuration", headers=headers).json() == saved.json()
    publications = [
        record for record in audit.records.values() if record["action"] == "configuration.publish"
    ]
    assert len(publications) == 1


def test_real_api_credential_rejection_is_private_and_never_published(
    security, signing_key, configuration_graph, validator, caplog
) -> None:
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.api.configuration import create_router
    from management_app.services.audit import AuditService

    identity, keys = security
    repository = LocalConfigurationRepository(validator=validator)
    initial = asyncio.run(repository.initialize(configuration_graph, actor_id=ACTOR))
    audit = LocalAuditRepository()
    app = FastAPI()
    app.include_router(
        create_router(
            repository=repository,
            identity=identity,
            validator=validator,
            audit=AuditService(repository=audit),
        )
    )
    candidate = str(uuid4())
    configuration_graph["agents"][3]["prompt"] = "=".join(("Password", candidate))
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.put(
            "/v2/configuration",
            json=configuration_graph,
            headers={"Authorization": "Bearer " + signed(signing_key), "If-Match": initial[1]},
        )
    assert (
        response.status_code == 422 and response.json()["code"] == "credential_literal_prohibited"
    )
    assert candidate not in response.text and candidate not in caplog.text
    assert asyncio.run(repository.read_active()) == initial
    assert not audit.records


def test_identity_components_must_share_one_explicit_policy(security) -> None:
    from management_app.security import IdentityService

    identity, keys = security
    different = identity.policy.model_copy(update={"tenant_id": str(uuid4())})
    with pytest.raises(ValueError, match="^identity_policy_mismatch$"):
        IdentityService(policy=different, verifier=identity.verifier, sessions=identity.sessions)
