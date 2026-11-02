"""Guarded non-test management wiring uses explicit approved resources, never fixture login."""

import asyncio
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from management_app.health import lifecycle


def test_owned_editor_fixture_requires_normal_manager_cookie(editor_test_app):
    async def scenario():
        async with (
            lifecycle(editor_test_app.api, editor_test_app.settings),
            editor_test_app.seed_editor(),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=editor_test_app.api),
                base_url=editor_test_app.origin,
            ) as client:
                assert (await client.get("/v2/configuration")).status_code == 401
                response = await client.get("/_test/login/manager-one")
                assert response.status_code == 303
                cookie = response.headers["set-cookie"].lower()
                assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
                assert (await client.get("/v2/configuration")).status_code == 200

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "target,path", [("editor", "/configuration"), ("test", "/configuration/test")]
)
def test_owned_editor_fixture_login_has_only_fixed_destinations(editor_test_app, target, path):
    async def scenario():
        async with (
            lifecycle(editor_test_app.api, editor_test_app.settings),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=editor_test_app.api),
                base_url=editor_test_app.origin,
            ) as client,
        ):
            response = await client.get("/_test/login/manager-one", params={"target": target})
            assert response.status_code == 303 and response.headers["location"] == path
            assert (await client.get("/_test/login/unknown-manager")).status_code == 404
            assert (
                await client.get(
                    "/_test/login/manager-one", params={"target": "https://bad.invalid"}
                )
            ).status_code == 422

    asyncio.run(scenario())


def test_owned_console_fixture_has_explicit_test_role_and_no_allocation_on_ticket(editor_test_app):
    async def scenario():
        async with (
            lifecycle(editor_test_app.api, editor_test_app.settings),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=editor_test_app.api),
                base_url=editor_test_app.origin,
            ) as client,
        ):
            await client.get("/_test/login/manager-one", params={"target": "test"})
            csrf = (await client.get("/auth/session")).json()["csrf"]
            response = await client.post(
                "/v1/test-tickets",
                json={"mode": "text", "requestId": "fixture-ticket"},
                headers={"Origin": editor_test_app.origin, "X-CSRF-Token": csrf},
            )
            assert (
                response.status_code == 200 and response.json()["socketPath"] == "/v1/test-sessions"
            )
            assert (
                editor_test_app.api.state.test_console_services.test_role == "Conversation.Tester"
            )
            assert response.headers["cache-control"] == "no-store"

    asyncio.run(scenario())


def test_owned_editor_fixture_keeps_origin_and_csrf_checks(editor_test_app):
    async def scenario():
        async with (
            lifecycle(editor_test_app.api, editor_test_app.settings),
            editor_test_app.seed_editor(),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=editor_test_app.api),
                base_url=editor_test_app.origin,
            ) as client:
                await client.get("/_test/login/manager-one")
                current = await client.get("/v2/configuration")
                csrf = (await client.get("/auth/session")).json()["csrf"]
                headers = {"If-Match": current.headers["etag"], "Origin": editor_test_app.origin}
                response = await client.put(
                    "/v2/configuration", json=editor_test_app.fixture["graph"], headers=headers
                )
                assert response.status_code == 403
                headers.update({"X-CSRF-Token": csrf, "Origin": "https://foreign.invalid"})
                response = await client.put(
                    "/v2/configuration", json=editor_test_app.fixture["graph"], headers=headers
                )
                assert response.status_code == 403
                assert (await client.get("/v2/configuration")).headers["etag"] == current.headers[
                    "etag"
                ]

    asyncio.run(scenario())


def test_management_composition_requires_explicit_authority_before_resource_creation():
    from management_app.management_app import create_managed_app
    from management_app.settings import Settings

    effects = []

    async def resources():
        effects.append("resource_creation")
        raise AssertionError("unauthorized_resource_creation")

    with pytest.raises(ValueError, match="explicit_management_composition_authority_required"):
        asyncio.run(create_managed_app(Settings(), resources=resources, authority=None))
    assert effects == []


@pytest.fixture(scope="module")
def composition_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def composition_case(composition_key, capability_graph, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("__REFLEX_SKIP_COMPILE", "true")

    def reject_frontend_install(*arguments, **keywords):
        pytest.fail("backend_composition_must_not_prepare_frontend_packages")

    monkeypatch.setattr(
        "reflex.utils.js_runtimes.install_frontend_packages", reject_frontend_install
    )

    from management_app.adapters.entra_oidc import EntraSigningKeys, MsalCodeFlow
    from management_app.composition import CompositionGrant, ManagementRequest, ManagementResources
    from management_app.models.capabilities import ApprovedCapabilities
    from management_app.security import EntraPolicy
    from management_app.settings import Settings
    from tests.integration.test_configuration_persistence import ContainerDouble
    from tests.unit.test_security import (
        AUDIENCE,
        CLIENT,
        ORIGIN,
        RUNTIME_AUDIENCE,
        RUNTIME_CLIENT,
        TENANT,
    )

    settings = Settings(mode="live")
    request = ManagementRequest(
        environment_ref="management-fixture",
        configuration_ref="configuration-fixture",
        audit_ref="audit-fixture",
        identity_review_ref="identity-review-fixture",
        capability_review_ref="capability-review-fixture",
        audit_retention_seconds=86400,
        policy=EntraPolicy(
            tenant_id=TENANT,
            client_id=CLIENT,
            api_audience=AUDIENCE,
            origin=ORIGIN,
            manager_role="Configuration.Manager",
            manager_scope="Configuration.ReadWrite",
        ),
        runtime_audience=RUNTIME_AUDIENCE,
        runtime_role="Capabilities.Execute",
        runtime_clients=(RUNTIME_CLIENT,),
    )
    effects, audit_records = [], []
    container = ContainerDouble()
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(composition_key.public_key()))
    public.update({"kid": "fixture-signing-key", "use": "sig", "alg": "RS256"})

    class Authority:
        mode = "valid"

        async def authorize(self, request, *, fingerprint, deadline):
            effects.append("authorize")
            if self.mode == "denied":
                raise PermissionError("management_fixture_denied")
            if self.mode == "blocked":
                await asyncio.Event().wait()
            expiry = datetime.now(UTC) + timedelta(seconds=-1 if self.mode == "expired" else 60)
            return CompositionGrant("foreign" if self.mode == "foreign" else fingerprint, expiry)

    from msal import ConfidentialClientApplication

    from tests.unit.test_security import signed

    nonce = [""]

    class IdentityHttp:
        def get(self, url, **kwargs):
            assert str(url).startswith(f"https://login.microsoftonline.com/{TENANT}/")
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
                    "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
                    "issuer": request.policy.issuer,
                    "jwks_uri": f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys",
                },
            )

        def post(self, url, **kwargs):
            assert kwargs["data"]["code_verifier"]
            effects.append("oidc_exchange")
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "access_token": str(uuid4()),
                    "id_token": signed(composition_key, aud=CLIENT, scp=None, nonce=nonce[0]),
                },
            )

    class CodeApplication(ConfidentialClientApplication):
        def initiate_auth_code_flow(self, *arguments, **keywords):
            flow = super().initiate_auth_code_flow(*arguments, **keywords)
            nonce[0] = parse_qs(urlsplit(flow["auth_uri"]).query)["nonce"][0]
            return flow

    def code_application():
        return CodeApplication(
            client_id=CLIENT,
            client_credential=str(uuid4()),
            authority=f"https://login.microsoftonline.com/{TENANT}",
            instance_discovery=False,
            http_client=IdentityHttp(),
            exclude_scopes=["offline_access"],
        )

    def keys_response(outgoing):
        assert (
            str(outgoing.url) == f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
        )
        effects.append("signing_keys")
        return httpx.Response(200, json={"keys": [public]})

    keys_client = httpx.AsyncClient(transport=httpx.MockTransport(keys_response), trust_env=False)

    class Audit:
        async def create_item(self, *, body, retry_write):
            assert retry_write == 0
            audit_records.append(copy.deepcopy(body))
            return body

    class Secrets:
        async def resolve(self, reference):
            effects.append("credential_resolved")
            return "synthetic-composition-value"

    class Ready:
        async def ready(self):
            return True

    async def close():
        await keys_client.aclose()
        effects.append("closed")

    async def resolver(host):
        return ["93.184.216.34"]

    def public_response(request):
        effects.append("public_read")
        assert request.url.host == "93.184.216.34"
        payload = json.loads(request.content)
        if "id" not in payload:
            return httpx.Response(202)
        if payload["method"] == "initialize":
            result = {
                "protocolVersion": payload["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "synthetic-reviewed-server", "version": "1"},
            }
        elif payload["method"] == "tools/list":
            result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]}
        elif payload["method"] == "tools/call":
            assert payload["params"]["name"] == "lookup"
            effects.append("tool_called")
            result = {
                "content": [],
                "structuredContent": {
                    "name": "Synthetic public result",
                    "private": "Synthetic excluded result",
                },
                "isError": False,
            }
        else:
            pytest.fail("unexpected_mcp_method")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    authority = Authority()
    resolved = ManagementResources(
        fingerprint=settings.composition_fingerprint(request.model_dump(mode="json")),
        configuration_container=container,
        audit_container=Audit(),
        signing_keys=EntraSigningKeys(policy=request.policy, client=keys_client),
        oidc=MsalCodeFlow(policy=request.policy, application_factory=code_application),
        reviewed=ApprovedCapabilities.from_reviewed(capability_graph),
        secret_policy=json.loads(
            (Path(__file__).parents[3] / "contracts/secret-detection-policy.json").read_text()
        ),
        secret_aliases=frozenset({"fixture-connection"}),
        destinations={"fixture-public-endpoint": "https://public.example.invalid/rpc"},
        secrets=Secrets(),
        readiness=Ready(),
        close=close,
        resolver=resolver,
        network=httpx.MockTransport(public_response),
    )

    async def resources(value, *, fingerprint, deadline):
        assert value == request and fingerprint == resolved.fingerprint
        assert deadline > asyncio.get_running_loop().time()
        effects.append("resources")
        return resolved

    return settings, request, authority, resources, resolved, container, effects, audit_records


def test_management_lifecycle_binds_explicit_bounded_nonblocking_alert_sink(composition_case):
    from queue import Queue

    from management_app.management_app import create_managed_app

    settings, request, authority, resources, _resolved, _container, effects, _audit = (
        composition_case
    )
    pending = Queue(maxsize=1)
    attempted = []

    def enqueue(event):
        attempted.append(event)
        pending.put_nowait(event)

    async def approved(value, *, fingerprint, deadline):
        resolved = await resources(value, fingerprint=fingerprint, deadline=deadline)
        return replace(resolved, test_alert_sink=enqueue)

    async def scenario():
        managed = await create_managed_app(
            settings, request=request, authority=authority, resources=approved
        )
        async with lifecycle(managed.api, settings):
            telemetry = managed.api.state.telemetry
            with telemetry.span(
                "test.relay", headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}
            ):
                for _ in range(3):
                    telemetry.event(
                        "test.relay",
                        "failure",
                        mode="text",
                        reason="unavailable",
                        correlation_id="cross-service-fixture",
                    )
            telemetry.event(
                "test.alert",
                "failure",
                mode="speech",
                reason="cleanup_uncertain",
                correlation_id="cross-service-fixture",
            )
            assert pending.qsize() == 1 and len(attempted) == 2
            event = pending.get_nowait()
            assert event["service"] == "management" and event["event"] == "test.alert"
            assert event["correlation_id"] == "cross-service-fixture"
            assert event["trace_id"] == "1" * 32
            assert set(event) <= {
                "timestamp",
                "severity",
                "service",
                "environment",
                "event",
                "outcome",
                "correlation_id",
                "trace_id",
                "span_id",
                "mode",
                "reason_code",
            }
            event["reason_code"] = "detached-fixture"
            assert all(item.get("reason_code") != "detached-fixture" for item in telemetry.events)
        telemetry.event("test.alert", "failure", mode="text", reason="dependency_failures")
        assert len(attempted) == 2 and not telemetry.events and effects.count("closed") == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["denied", "expired", "foreign"])
def test_composition_denial_prevents_resource_creation_and_writes(composition_case, mode):
    from management_app.management_app import create_managed_app

    settings, request, authority, resources, _resolved, container, effects, _audit = (
        composition_case
    )
    authority.mode = mode
    with pytest.raises(PermissionError):
        asyncio.run(
            create_managed_app(settings, request=request, resources=resources, authority=authority)
        )
    assert effects == ["authorize"] and not container.items


@pytest.mark.parametrize("failure", ["resource_binding", "revoked_after_factory", "bootstrap"])
def test_composition_failure_closes_resources_without_fixture_fallback(composition_case, failure):
    from management_app.management_app import create_managed_app

    settings, request, authority, _factory, resolved, container, effects, _audit = composition_case

    async def resources(value, *, fingerprint, deadline):
        effects.append("resources")
        if failure == "revoked_after_factory":
            authority.mode = "denied"
        if failure == "bootstrap":
            container.fail_operation = 0
        return (
            replace(resolved, fingerprint="foreign") if failure == "resource_binding" else resolved
        )

    with pytest.raises((PermissionError, RuntimeError)):
        asyncio.run(
            create_managed_app(settings, request=request, resources=resources, authority=authority)
        )
    assert effects[-1] == "closed"
    assert not container.items and "credential_resolved" not in effects


def business_graph(current, template):
    from management_app.adapters.configuration_repository import graph_document

    existing = graph_document(current)
    graph = copy.deepcopy(template)
    by_role = {agent["role"]: agent for agent in existing["agents"]}
    replacements = {}
    for index, agent in enumerate(graph["agents"]):
        if agent["role"] != "ordinary":
            original = by_role[agent["role"]]
            replacements[agent["agentId"]] = original["agentId"]
            graph["agents"][index] = copy.deepcopy(original)
    for relationship in graph["relationships"]:
        for field in ("sourceAgentId", "targetAgentId"):
            relationship[field] = replacements.get(relationship[field], relationship[field])
    return graph


def test_composed_cosmos_app_authenticates_publishes_preserves_conflicts_and_runtime_contract(
    composition_case, composition_key, capability_graph
):
    from management_app.health import lifecycle
    from management_app.management_app import create_managed_app
    from tests.unit.test_security import RUNTIME_AUDIENCE, RUNTIME_CLIENT, signed

    settings, request, authority, resources, _resolved, container, effects, audit_records = (
        composition_case
    )

    async def exercise():
        application = await create_managed_app(
            settings, request=request, resources=resources, authority=authority
        )
        api = application.api
        assert effects.index("authorize") < effects.index("resources")
        paths = set(api.openapi()["paths"])
        assert {
            "/v2/configuration",
            "/v2/runtime/configuration",
            "/v1/runtime/capabilities/execute",
            "/auth/login",
        } <= paths
        assert not any(path.startswith("/_test") for path in paths)
        assert api.state.editor_services.guard.identity is api.state.management_identity
        async with lifecycle(api, settings):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url=request.policy.origin
            ) as client:
                assert (await client.get("/v2/configuration")).status_code == 401
                manager = {"Authorization": "Bearer " + signed(composition_key)}
                current = await client.get("/v2/configuration", headers=manager)
                assert current.status_code == 200
                graph = business_graph(current.json(), capability_graph)
                saved = await client.put(
                    "/v2/configuration",
                    headers={**manager, "If-Match": current.headers["etag"]},
                    json=graph,
                )
                assert saved.status_code == 200 and saved.headers["etag"] != current.headers["etag"]
                assert (
                    await client.put(
                        "/v2/configuration",
                        headers={**manager, "If-Match": current.headers["etag"]},
                        json=graph,
                    )
                ).status_code == 409
                assert (
                    await client.get("/v2/configuration", headers=manager)
                ).json() == saved.json()
                assert (
                    await client.get("/v2/runtime/configuration", headers=manager)
                ).status_code == 401
                runtime = {
                    "Authorization": "Bearer "
                    + signed(
                        composition_key,
                        aud=RUNTIME_AUDIENCE,
                        roles=["Capabilities.Execute"],
                        scp=None,
                        idtyp="app",
                        azp=RUNTIME_CLIENT,
                    )
                }
                selected = await client.get("/v2/runtime/configuration", headers=runtime)
                assert selected.status_code == 200 and selected.json()["id"] == saved.json()["id"]
                assert (await client.get("/v2/configuration", headers=runtime)).status_code == 401
                ready = await client.get("/readyz")
                assert ready.status_code == 200
                assert ready.json()["capabilities"] == {
                    "live_calls": False,
                    "management_configuration": True,
                }
                assert audit_records and "credential_resolved" not in effects
                assert "security-authority" in container.items
                attempt = {
                    "requestId": "composition-attempt",
                    "sessionId": "composition-session",
                    "agentId": "agent-alpha",
                    "agentEpoch": 1,
                    "pinnedRevisionId": saved.json()["id"],
                    "toolId": "tool-public",
                    "actionId": "lookup",
                    "inputs": {},
                    "deadlineAt": (datetime.now(UTC) + timedelta(seconds=10)).isoformat(),
                }
                execute_path = "/v1/runtime/capabilities/execute"
                assert (
                    await client.post(execute_path, headers=manager, json=attempt)
                ).status_code == 401
                executed = await client.post(execute_path, headers=runtime, json=attempt)
                assert executed.status_code == 200 and executed.json()["result"] == {
                    "name": "Synthetic public result"
                }
                assert effects.count("tool_called") == effects.count("credential_resolved") == 1
                current_authority = await api.state.management_repository.read_authority()
                revoked = await client.put(
                    "/v1/security/revocations",
                    headers={**manager, "If-Match": current_authority["_etag"]},
                    json={
                        "disabledToolIds": ["tool-public"],
                        "disabledConnectionIds": [],
                        "revokedAssignments": [],
                    },
                )
                assert revoked.status_code == 200
                attempt["requestId"] = "composition-revoked-attempt"
                assert (
                    await client.post(execute_path, headers=runtime, json=attempt)
                ).status_code == 403
                assert effects.count("tool_called") == effects.count("credential_resolved") == 1
                assert (await client.post("/unreviewed-ingress")).status_code == 403
        assert effects[-1] == "closed" and api.state.ready is False

    asyncio.run(exercise())


def test_composed_oidc_session_drives_reflex_drafts_conflicts_and_revocation(
    composition_case, configuration_graph
):
    from fastapi import HTTPException

    from management_app.health import lifecycle
    from management_app.management_app import create_managed_app
    from management_app.security import COOKIE
    from management_app.state.configuration_editor import EditorDraft

    settings, request, authority, resources, _resolved, container, effects, _audit = (
        composition_case
    )

    async def exercise():
        managed = await create_managed_app(
            settings, request=request, resources=resources, authority=authority
        )
        editor = managed.api.state.editor_services
        async with lifecycle(managed.api, settings):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=managed), base_url=request.policy.origin
            ) as client:
                assert (await client.get("/auth/session")).status_code == 401
                login = await client.get("/auth/login")
                assert login.status_code == 303
                query = parse_qs(urlsplit(login.headers["location"]).query)
                assert query["code_challenge_method"] == ["S256"]
                callback = {"state": query["state"][0], "code": "synthetic-composition-code"}
                assert (await client.post("/auth/callback", data=callback)).status_code == 303
                assert (await client.post("/auth/callback", data=callback)).status_code == 401
                assert "oidc_exchange" in effects and "signing_keys" in effects
                cookie = client.cookies.get(COOKIE)
                sid, token = "composition-sid", "composition-state-token"
                cleaned = editor.guard.connect(
                    sid,
                    {
                        "QUERY_STRING": "token=" + token,
                        "asgi.scope": {
                            "type": "http",
                            "method": "GET",
                            "path": "/_event",
                            "headers": [
                                (b"origin", request.policy.origin.encode()),
                                (b"cookie", (COOKIE + "=" + cookie).encode()),
                            ],
                        },
                    },
                )
                assert all(header[0] != b"cookie" for header in cleaned["asgi.scope"]["headers"])
                with pytest.raises(HTTPException):
                    await editor.request(sid, "foreign-state-token", "GET")
                current = await editor.request(sid, token, "GET")
                assert current.status_code == 200
                graph = business_graph(current.json(), configuration_graph)
                first = EditorDraft.from_revision(
                    graph, current.headers["etag"], reviewed=editor.reviewed
                )
                second = EditorDraft.from_revision(
                    graph, current.headers["etag"], reviewed=editor.reviewed
                )
                first.edit_agent("agent-alpha", "prompt", "Synthetic first draft")
                second.edit_agent("agent-alpha", "prompt", "Synthetic preserved second draft")
                saved = await editor.request(sid, token, "PUT", graph=first.graph, etag=first.etag)
                assert saved.status_code == 200
                first.committed(saved.json(), saved.headers["etag"])
                unsaved = copy.deepcopy(second.graph)
                conflict = await editor.request(
                    sid, token, "PUT", graph=second.graph, etag=second.etag
                )
                assert conflict.status_code == 409
                second.rejected(conflict.status_code, conflict.json())
                assert (
                    second.conflict
                    and second.graph == unsaved
                    and second.etag == current.headers["etag"]
                )
                assert (await editor.request(sid, token, "GET")).json() == saved.json()
                session = await client.get("/auth/session")
                logged_out = await client.post(
                    "/auth/logout",
                    headers={
                        "Origin": request.policy.origin,
                        "X-CSRF-Token": session.json()["csrf"],
                    },
                )
                assert logged_out.status_code == 204
                before = copy.deepcopy(container.items)
                with pytest.raises(HTTPException):
                    await editor.request(sid, token, "PUT", graph=second.graph, etag=first.etag)
                assert container.items == before
                editor.guard.disconnect(sid)
        assert effects[-1] == "closed"

    asyncio.run(exercise())


def test_composed_secret_resolution_rejects_unreviewed_references_before_delegation():
    from management_app.composition import ApprovedSecrets

    references = []

    class Secrets:
        async def resolve(self, reference):
            references.append(reference)
            return "synthetic-approved-value"

    async def exercise():
        source = ApprovedSecrets(Secrets(), frozenset({"approved-alias"}))
        for candidate in (
            "unapproved-literal",
            "secret://foreign-alias",
            "https://vault.example.invalid",
        ):
            with pytest.raises(PermissionError):
                await source.resolve(candidate)
        assert not references
        assert await source.resolve("secret://approved-alias") == "synthetic-approved-value"
        assert references == ["secret://approved-alias"]

    asyncio.run(exercise())


def test_composition_rechecks_authority_at_startup(composition_case):
    from management_app.health import lifecycle
    from management_app.management_app import create_managed_app

    settings, request, authority, resources, _resolved, _container, effects, _audit = (
        composition_case
    )

    async def exercise():
        application = await create_managed_app(
            settings, request=request, resources=resources, authority=authority
        )
        authority.mode = "denied"
        with pytest.raises(PermissionError):
            async with lifecycle(application.api, settings):
                pytest.fail("unauthorized_management_startup")
        assert effects[-1] == "closed"
        assert application.api.state.ready is False

    asyncio.run(exercise())


def test_denied_startup_disposes_observability_even_if_resource_close_fails(
    composition_case, monkeypatch
):
    from management_app.health import lifecycle
    from management_app.management_app import create_managed_app

    settings, request, authority, _factory, resolved, _container, effects, _audit = composition_case

    async def close():
        await resolved.close()
        raise RuntimeError("sensitive-provider-shutdown-details")

    async def resources(*arguments, **keywords):
        return replace(resolved, close=close)

    async def exercise():
        managed = await create_managed_app(
            settings, request=request, resources=resources, authority=authority
        )
        telemetry, metrics = managed.api.state.telemetry, managed.api.state.metrics
        close_telemetry, close_metrics = telemetry.close, metrics.close
        closed = []

        def telemetry_closed():
            close_telemetry()
            closed.append("telemetry")

        def metrics_closed():
            close_metrics()
            closed.append("metrics")

        monkeypatch.setattr(telemetry, "close", telemetry_closed)
        monkeypatch.setattr(metrics, "close", metrics_closed)
        authority.mode = "denied"
        with pytest.raises(RuntimeError, match="^management_resources_close_unavailable$"):
            async with lifecycle(managed.api, settings):
                pytest.fail("unauthorized_management_startup")
        assert closed == ["telemetry", "metrics"]
        assert effects.count("closed") == 1 and managed.api.state.ready is False

    asyncio.run(exercise())


def test_composition_cancellation_before_authorization_has_no_effects(composition_case):
    from management_app.management_app import create_managed_app

    settings, request, authority, resources, _resolved, container, effects, _audit = (
        composition_case
    )
    authority.mode = "blocked"

    async def exercise():
        task = asyncio.create_task(
            create_managed_app(settings, request=request, resources=resources, authority=authority)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert effects == ["authorize"] and not container.items


def test_composition_cancellation_during_bootstrap_closes_resources_once(
    composition_case, monkeypatch
):
    from management_app.management_app import create_managed_app

    settings, request, authority, resources, _resolved, container, effects, _audit = (
        composition_case
    )

    async def exercise():
        entered = asyncio.Event()

        async def blocked_batch(*arguments, **keywords):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(container, "execute_item_batch", blocked_batch)
        task = asyncio.create_task(
            create_managed_app(settings, request=request, resources=resources, authority=authority)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert effects[-1] == "closed" and effects.count("closed") == 1
    assert not container.items


@pytest.mark.parametrize("boundary", ["authority", "factory"])
def test_composition_hides_authority_and_factory_diagnostics(composition_case, boundary):
    from management_app.management_app import create_managed_app

    settings, request, authority, resources, _resolved, _container, _effects, _audit = (
        composition_case
    )

    class FailedAuthority:
        async def authorize(self, *arguments, **keywords):
            raise RuntimeError("sensitive-composition-provider-details")

    async def failed_resources(*arguments, **keywords):
        raise RuntimeError("sensitive-composition-provider-details")

    with pytest.raises((PermissionError, RuntimeError)) as failure:
        asyncio.run(
            create_managed_app(
                settings,
                request=request,
                resources=failed_resources if boundary == "factory" else resources,
                authority=FailedAuthority() if boundary == "authority" else authority,
            )
        )
    assert "sensitive-composition-provider-details" not in str(failure.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("capacity", 6),
        ("capacity", 4),
        ("retention_seconds", 301),
        ("runtime_endpoint", "http://runtime.invalid"),
        ("max_clock_skew_seconds", 2),
    ],
)
def test_browser_test_composition_rejects_unreviewable_bounds(field, value):
    from management_app.composition import ManagementTestRequest

    document = dict(
        runtime_endpoint="https://runtime.fixture.example",
        runtime_audience=str(uuid4()),
        issuer="https://management.fixture.example",
        permit_audience="browser-tests",
        test_role="Conversation.Tester",
        permission_review_ref="permission-reviewed",
        signing_review_ref="signing-reviewed",
        runtime_review_ref="runtime-reviewed",
        capacity=5,
        retention_seconds=300,
        max_clock_skew_seconds=0,
    )
    with pytest.raises(ValueError):
        ManagementTestRequest(**(document | {field: value}))


def test_browser_test_composition_requires_explicit_resources_before_bootstrap(composition_case):
    from management_app.composition import ManagementTestRequest
    from management_app.management_app import create_managed_app

    settings, request, authority, _factory, resolved, container, effects, _audit = composition_case
    reviewed = ManagementTestRequest(
        runtime_endpoint="https://runtime.fixture.example",
        runtime_audience=str(uuid4()),
        issuer="https://management.fixture.example",
        permit_audience="browser-tests",
        test_role="Conversation.Tester",
        permission_review_ref="permission-reviewed",
        signing_review_ref="signing-reviewed",
        runtime_review_ref="runtime-reviewed",
        capacity=5,
        retention_seconds=300,
        max_clock_skew_seconds=0,
    )
    bound = request.model_copy(update={"tests": reviewed})

    async def resources(value, *, fingerprint, deadline):
        assert value == bound
        effects.append("resource_creation")
        return replace(resolved, fingerprint=fingerprint)

    with pytest.raises(PermissionError):
        asyncio.run(
            create_managed_app(settings, request=bound, resources=resources, authority=authority)
        )
    assert not container.items and "closed" in effects and "credential_resolved" not in effects


def test_explicit_test_composition_exposes_guarded_ticket_without_model_or_workload_io(
    composition_case, composition_key
):
    from management_app.composition import ManagementTestRequest, ManagementTestResources
    from management_app.management_app import create_managed_app
    from management_app.security import Principal

    settings, request, authority, _factory, resolved, _container, effects, _audit = composition_case
    reviewed = ManagementTestRequest(
        runtime_endpoint="https://runtime.fixture.example",
        runtime_audience=str(uuid4()),
        issuer="https://management.fixture.example",
        permit_audience="browser-tests",
        test_role="Conversation.Tester",
        permission_review_ref="permission-reviewed",
        signing_review_ref="signing-reviewed",
        runtime_review_ref="runtime-reviewed",
        capacity=5,
        retention_seconds=300,
        max_clock_skew_seconds=0.1,
    )
    bound = request.model_copy(update={"tests": reviewed})

    class Credential:
        async def get_token(self, *scopes):
            pytest.fail("visiting_console_or_issuing_ticket_must_not_get_workload_token")

    async def resources(value, *, fingerprint, deadline):
        assert value == bound
        return replace(
            resolved,
            fingerprint=fingerprint,
            tests=ManagementTestResources(
                key_id="fixture-key",
                signing_key=composition_key,
                verification_keys={"fixture-key": composition_key.public_key()},
                credential=Credential(),
            ),
        )

    async def scenario():
        managed = await create_managed_app(
            settings, request=bound, resources=resources, authority=authority
        )
        try:
            identity = managed.api.state.management_identity
            ticket = identity.sessions.issue(
                Principal(
                    "manager-fixture",
                    request.policy.tenant_id,
                    int(datetime.now(UTC).timestamp()) + 600,
                    (request.policy.manager_role, reviewed.test_role),
                )
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=managed.asgi), base_url=request.policy.origin
            ) as client:
                denied = await client.post(
                    "/v1/test-tickets",
                    json={"mode": "text", "requestId": "denied"},
                    headers={
                        "Origin": request.policy.origin,
                        "Cookie": "unrelated=fixture",
                        "X-CSRF-Token": "a" * 64,
                    },
                )
                assert denied.status_code == 401
                response = await client.post(
                    "/v1/test-tickets",
                    json={"mode": "text", "requestId": "fixture-request"},
                    headers={
                        "Cookie": identity.cookie_name + "=" + ticket.cookie,
                        "Origin": request.policy.origin,
                        "X-CSRF-Token": ticket.csrf,
                    },
                )
                assert (
                    response.status_code == 200
                    and response.json()["socketPath"] == "/v1/test-sessions"
                )
                assert response.headers["cache-control"] == "no-store"
                assert managed.api.state.management_startup.test_relay._active == 0
        finally:
            await managed.api.state.management_startup.shutdown()
            managed.api.state.telemetry.close()
            managed.api.state.metrics.close()
        assert "credential_resolved" not in effects and effects.count("closed") == 1

    asyncio.run(scenario())


def test_managed_factory_returns_initialized_protected_reflex_boundary(composition_case):
    from management_app.composition import ManagedApplication
    from management_app.management_app import create_managed_app
    from management_app.security import ProtectedReflexBoundary, SecureEventNamespace

    settings, request, authority, resources, _resolved, _container, _effects, _audit = (
        composition_case
    )

    async def exercise():
        managed = await create_managed_app(
            settings, request=request, resources=resources, authority=authority
        )
        assert isinstance(managed, ManagedApplication)
        assert isinstance(managed.asgi, ProtectedReflexBoundary)
        assert any(
            isinstance(handler, SecureEventNamespace)
            for handler in managed.reflex_app.sio.namespace_handlers.values()
        )
        await managed.api.state.management_startup.shutdown()
        managed.api.state.telemetry.close()
        managed.api.state.metrics.close()

    asyncio.run(exercise())
