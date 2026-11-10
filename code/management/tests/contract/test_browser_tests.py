"""Browser-test tickets and raw WebSockets must use verified cookie/Origin permission."""

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tests.unit.test_security import security as security
from tests.unit.test_security import signing_key as signing_key


def test_owned_manager_relay_enforces_stable_actor_and_five_occupied_slots(editor_host_factory):
    import asyncio
    import ssl
    from uuid import uuid4

    import httpx
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    from tests.conftest import configuration_graph_document

    actors = tuple("manager-" + str(number) for number in range(1, 7))
    with editor_host_factory(
        graph=configuration_graph_document(), identities=actors, mode="local"
    ) as host:
        trust = ssl.create_default_context(cafile=host["certificate"])

        async def scenario():
            sockets = []

            async def message_of(socket, kind):
                """Read until a message of this kind, skipping audio credits and agent changes."""
                while True:
                    value = json.loads(await asyncio.wait_for(socket.recv(), 5))
                    if value["type"] == kind:
                        return value
                    assert value["type"] in {"audio.credit", "agent.changed"}

            async def begin(actor, *, mode="speech", new_cookie=False):
                cookie = host["sessions"][actor][0]
                async with httpx.AsyncClient(
                    base_url=host["management"],
                    verify=trust,
                    trust_env=False,
                    timeout=1,
                    cookies={cookie["name"]: cookie["value"]},
                ) as client:
                    if new_cookie:
                        client.cookies.clear()
                        response = await client.get("/_test/login/" + actor)
                        assert response.status_code == 303
                    csrf = (await client.get("/auth/session")).json()["csrf"]
                    response = await client.post(
                        "/v1/console/tickets",
                        json={"mode": mode, "requestId": uuid4().hex},
                        headers={"Origin": host["ui"], "X-CSRF-Token": csrf},
                    )
                    assert response.status_code == 200
                    ticket = response.json()
                    cookie_header = "; ".join(
                        name + "=" + value for name, value in client.cookies.items()
                    )
                socket = await connect(
                    host["ui"].replace("https:", "wss:") + ticket["socketPath"],
                    ssl=trust,
                    origin=host["ui"],
                    subprotocols=["caller.console.v1"],
                    additional_headers={"Cookie": cookie_header},
                    proxy=None,
                    compression=None,
                    max_size=8192,
                    max_queue=1,
                    write_limit=12000,
                )
                sockets.append(socket)
                await socket.send(
                    json.dumps(
                        {
                            "type": "authenticate",
                            "permit": ticket["permit"],
                            "requestId": ticket["requestId"],
                        }
                    )
                )
                return socket

            try:
                first = await begin(actors[0])
                ready = json.loads(await asyncio.wait_for(first.recv(), 5))
                assert ready["type"] == "ready" and ready["agentId"] == "agent-welcome"
                denied = await begin(actors[0], mode="speech", new_cookie=True)
                with pytest.raises(ConnectionClosed) as failure:
                    await asyncio.wait_for(denied.recv(), 5)
                assert failure.value.rcvd.code == 1013
                assert failure.value.rcvd.reason == "manager_busy"
                await first.send(
                    json.dumps(
                        {
                            "type": "ping",
                            "sequence": 1,
                            "sessionId": ready["sessionId"],
                            "nonce": "original-still-owned",
                        }
                    )
                )
                assert (await message_of(first, "pong"))["nonce"] == "original-still-owned"
                active = [(first, ready, 2)]
                for actor in actors[1:5]:
                    socket = await begin(actor)
                    response = json.loads(await asyncio.wait_for(socket.recv(), 5))
                    assert response["type"] == "ready"
                    active.append((socket, response, 1))
                excess = await begin(actors[5])
                with pytest.raises(ConnectionClosed) as failure:
                    await asyncio.wait_for(excess.recv(), 5)
                assert failure.value.rcvd.code == 1013
                async with httpx.AsyncClient(verify=trust, trust_env=False, timeout=1) as client:
                    counts = (await client.get(host["runtime"] + "/_test/runtime-status")).json()
                    assert counts["occupied"] == 5 and counts["liveConnections"] == 5
                    for socket, response, sequence in active:
                        await socket.send(
                            json.dumps(
                                {
                                    "type": "stop",
                                    "sequence": sequence,
                                    "sessionId": response["sessionId"],
                                }
                            )
                        )
                        ended = await message_of(socket, "ended")
                        assert ended["cleanupStatus"] == "complete"
                    counts = (await client.get(host["runtime"] + "/_test/runtime-status")).json()
                    assert counts["occupied"] == 0 and counts["liveConnections"] == 5
                    # A text console session is one backend request per turn and keeps no slot.
                    text = await begin(actors[1], mode="text")
                    opened = await message_of(text, "ready")
                    assert opened["mode"] == "text" and opened["agentId"] == "agent-welcome"
                    for sequence, content in ((1, "First question"), (2, "Second question")):
                        await text.send(
                            json.dumps(
                                {
                                    "type": "text.submit",
                                    "sequence": sequence,
                                    "sessionId": opened["sessionId"],
                                    "text": content,
                                }
                            )
                        )
                        # The reply streams in small deltas, as the live model sends it.
                        reply = []
                        while True:
                            value = json.loads(await asyncio.wait_for(text.recv(), 5))
                            if value["type"] == "text.completed":
                                break
                            if value["type"] == "text.delta":
                                reply.append(value["text"])
                            else:
                                assert value["type"] == "agent.changed", value
                        assert "".join(reply) == "Synthetic shared reply"
                    await text.send(
                        json.dumps(
                            {"type": "stop", "sequence": 3, "sessionId": opened["sessionId"]}
                        )
                    )
                    assert (await message_of(text, "ended"))["cleanupStatus"] == "complete"
                    counts = (await client.get(host["runtime"] + "/_test/runtime-status")).json()
                    # Both turns continued one stored conversation and left no slot behind.
                    assert counts["conversations"] == 1 and counts["occupied"] == 0
                    assert counts["liveConnections"] == 5
                    fault = await client.post(
                        host["runtime"] + "/_test/unknown-prepare/on",
                        headers={"Authorization": "Bearer synthetic-browser-workload"},
                    )
                    assert fault.status_code == 200
                    unknown = await begin(actors[0])
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(unknown.recv(), 5)
                    uncertain = (await client.get(host["runtime"] + "/_test/runtime-status")).json()
                    assert uncertain["occupied"] == 1
                    assert uncertain["modelRequests"] == counts["modelRequests"] + 1
                    fault = await client.post(
                        host["runtime"] + "/_test/unknown-prepare/off",
                        headers={"Authorization": "Bearer synthetic-browser-workload"},
                    )
                    assert fault.status_code == 200
                    blocked = await begin(actors[0])
                    with pytest.raises(ConnectionClosed) as failure:
                        await asyncio.wait_for(blocked.recv(), 5)
                    assert failure.value.rcvd.code == 1013
                    retained = (await client.get(host["runtime"] + "/_test/runtime-status")).json()
                    assert retained["occupied"] == 1
                    assert retained["modelRequests"] == uncertain["modelRequests"]
            finally:
                await asyncio.gather(*(socket.close() for socket in sockets))

        asyncio.run(scenario())


@pytest.fixture
def browser_identity(security):
    from management_app.security import Principal

    identity, keys = security
    principal = Principal(
        "browser-test-manager",
        identity.policy.tenant_id,
        int(time.time()) + 900,
        (identity.policy.manager_role, "Configuration.Test"),
    )
    ticket = identity.sessions.issue(principal)
    application = FastAPI()

    @application.post("/ticket-authorization")
    async def authorize(request: Request):
        session = identity.require_test_manager(
            request, mutation=True, test_role="Configuration.Test"
        )
        return {"actor": session.principal.actor_id}

    with TestClient(application, base_url=identity.policy.origin) as client:
        yield client, identity, keys, ticket


def browser_headers(identity, ticket):
    return {
        "Origin": identity.policy.origin,
        "Cookie": identity.cookie_name + "=" + ticket.cookie,
        "X-CSRF-Token": ticket.csrf,
    }


def test_cookie_test_permission_needs_no_access_token_or_key_discovery(browser_identity):
    client, identity, keys, ticket = browser_identity
    response = client.post("/ticket-authorization", headers=browser_headers(identity, ticket))
    assert response.status_code == 200 and response.json() == {"actor": "browser-test-manager"}
    assert keys.calls == 0


@pytest.mark.parametrize(
    "fault",
    [
        "missing-origin",
        "null-origin",
        "foreign-origin",
        "missing-cookie",
        "csrf",
        "bearer",
        "logout",
        "role",
        "plaintext",
    ],
)
def test_browser_test_authorization_rejects_unverified_origin_cookie_role_or_transport(
    browser_identity, fault
):
    from management_app.security import Principal

    client, identity, keys, ticket = browser_identity
    headers = browser_headers(identity, ticket)
    if fault == "missing-origin":
        headers.pop("Origin")
    elif fault == "null-origin":
        headers["Origin"] = "null"
    elif fault == "foreign-origin":
        headers["Origin"] = "https://foreign.fixture.example"
    elif fault == "missing-cookie":
        headers.pop("Cookie")
    elif fault == "csrf":
        headers.pop("X-CSRF-Token")
    elif fault == "bearer":
        headers["Authorization"] = "Bearer synthetic-not-a-cookie"
    elif fault == "logout":
        identity.sessions.revoke(ticket.cookie)
    elif fault == "role":
        principal = Principal(
            "browser-test-manager",
            identity.policy.tenant_id,
            int(time.time()) + 900,
            (identity.policy.manager_role,),
        )
        ticket = identity.sessions.issue(principal)
        headers = browser_headers(identity, ticket)
    response = client.post(
        "http://management.example.invalid/ticket-authorization"
        if fault == "plaintext"
        else "/ticket-authorization",
        headers=headers,
    )
    assert response.status_code in {401, 403}
    assert keys.calls == 0


def permit_document():
    started = datetime(2026, 10, 30, 12, tzinfo=UTC)
    issued = started + timedelta(seconds=2)
    return {
        "iss": "https://management.example.invalid/test-permits",
        "aud": "runtime-fixture",
        "iat": int(issued.timestamp()),
        "exp": int(issued.timestamp()) + 30,
        "issuedAt": issued.isoformat().replace("+00:00", "Z"),
        "admissionStartedAt": started.isoformat().replace("+00:00", "Z"),
        "admissionDeadlineUtc": (started + timedelta(seconds=10))
        .isoformat()
        .replace("+00:00", "Z"),
        "tenantId": "tenant-fixture",
        "actorId": "manager-fixture",
        "mode": "speech",
        "managementSessionDigest": "1" * 64,
        "requestId": "request-fixture",
        "nonce": "2" * 64,
        "authExpiresAt": (started + timedelta(seconds=900)).isoformat().replace("+00:00", "Z"),
    }


def test_management_permit_preserves_runtime_claim_shape_and_original_deadline():
    from management_app.models.test_session import StartPermit, TicketRequest

    document = permit_document()
    permit = StartPermit.model_validate_json(json.dumps(document))
    assert permit.model_dump(mode="json", by_alias=True) == document
    assert permit.admission_deadline_utc - permit.admission_started_at == timedelta(seconds=10)
    assert len(permit.nonce_digest) == len(permit.actor_key) == 64
    assert (
        TicketRequest.model_validate_json('{"mode":"text","requestId":"request-fixture"}').mode
        == "text"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"admissionDeadlineUtc": "2026-10-30T12:00:12Z"},
        {"managementSessionDigest": ""},
        {"nonce": "foreign"},
        {"mode": "voice"},
        {"provider_url": "https://foreign.fixture.example"},
        {"iat": True},
    ],
)
def test_management_permit_rejects_reset_or_untrusted_claims(changes):
    from management_app.models.test_session import StartPermit

    with pytest.raises(ValueError):
        StartPermit.model_validate_json(json.dumps(permit_document() | changes))


@pytest.mark.parametrize(
    "field", ["tenantId", "actorId", "communityId", "revisionId", "providerUrl"]
)
def test_ticket_request_never_accepts_client_authority_fields(field):
    from management_app.models.test_session import TicketRequest

    with pytest.raises(ValueError):
        TicketRequest.model_validate_json(
            json.dumps({"mode": "text", "requestId": "request-fixture", field: "foreign"})
        )


def test_management_permit_is_parsed_identically_by_runtime_contract(monkeypatch):
    from pathlib import Path

    from management_app.models.test_session import StartPermit

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "backend/src"))
    from caller_runtime.domain.conversation import StartPermit as RuntimePermit

    encoded = json.dumps(permit_document())
    management, runtime = (
        StartPermit.model_validate_json(encoded),
        RuntimePermit.model_validate_json(encoded),
    )
    assert management.model_dump(mode="json", by_alias=True) == runtime.model_dump(
        mode="json", by_alias=True
    )
    assert (
        management.actor_key == runtime.actor_key
        and management.nonce_digest == runtime.nonce_digest
    )


@pytest.mark.parametrize(
    "field", ["permit", "prompt", "audio", "transcript", "messages", "provider_body"]
)
def test_console_snapshot_never_contains_content_or_permit(field):
    from management_app.models.test_session import ConsoleSnapshot

    with pytest.raises(ValueError):
        ConsoleSnapshot.model_validate_json(json.dumps({field: "synthetic-private-content"}))


def test_console_ready_requires_complete_trusted_session_and_revision_metadata():
    from management_app.models.test_session import ConsoleSnapshot

    with pytest.raises(ValueError):
        ConsoleSnapshot.model_validate_json('{"status":"ready","mode":"text"}')


@pytest.fixture
def permit_signer(signing_key):
    from management_app.models.test_session import StartPermit
    from management_app.security import TestPermitSigner

    permit = StartPermit.model_validate_json(json.dumps(permit_document()))
    clock = [permit.issued_at]
    signer = TestPermitSigner(
        issuer=permit.issuer,
        audience=permit.audience,
        key_id="management-test-key",
        signing_key=signing_key,
        verification_keys={"management-test-key": signing_key.public_key()},
        utc_now=lambda: clock[0],
        max_clock_skew_seconds=0,
    )
    return signer, permit, clock, signing_key


def test_fixed_signer_roundtrips_exact_claims_without_exposing_provider_credentials(permit_signer):
    import jwt

    signer, permit, clock, key = permit_signer
    raw = signer.sign(permit)
    assert jwt.get_unverified_header(raw) == {
        "alg": "RS256",
        "typ": "JWT",
        "kid": "management-test-key",
    }
    actual = signer.verify(
        raw,
        management_session_digest=permit.management_session_digest,
        tenant_id=permit.tenant_id,
        actor_id=permit.actor_id,
    )
    assert actual == permit and len(raw.encode()) <= 6144


@pytest.mark.parametrize(
    "fault",
    [
        "algorithm",
        "key-url",
        "unknown-key",
        "issuer",
        "audience",
        "cookie",
        "actor",
        "tenant",
        "deadline",
    ],
)
def test_fixed_signer_rejects_untrusted_key_claim_or_current_cookie_binding(permit_signer, fault):
    import jwt

    signer, permit, clock, key = permit_signer
    document = permit.model_dump(mode="json", by_alias=True)
    headers = {"kid": "management-test-key"}
    algorithm = "RS256"
    if fault == "algorithm":
        algorithm = "HS256"
    elif fault == "key-url":
        headers["jku"] = "https://foreign.fixture.example/keys"
    elif fault == "unknown-key":
        headers["kid"] = "foreign-key"
    elif fault in {"issuer", "audience"}:
        document["iss" if fault == "issuer" else "aud"] = "foreign-binding"
    elif fault == "deadline":
        clock[0] = permit.admission_deadline_utc
    raw = jwt.encode(
        document,
        "synthetic-hmac-key-not-used-by-verifier-32" if algorithm == "HS256" else key,
        algorithm=algorithm,
        headers=headers,
    )
    with pytest.raises(PermissionError, match="invalid_test_permit"):
        signer.verify(
            raw,
            management_session_digest="3" * 64
            if fault == "cookie"
            else permit.management_session_digest,
            tenant_id="foreign-tenant" if fault == "tenant" else permit.tenant_id,
            actor_id="foreign-manager" if fault == "actor" else permit.actor_id,
        )


@pytest.fixture
def ticket_api(browser_identity, signing_key, monkeypatch):
    from management_app.api.test_sessions import create_router
    from management_app.observability import Telemetry
    from management_app.security import Principal, TestPermitSigner

    client, identity, keys, original_ticket = browser_identity
    clock, ticks = [datetime(2026, 10, 30, 12, tzinfo=UTC)], [0.0]
    monkeypatch.setattr(identity.sessions, "clock", lambda: clock[0].timestamp())
    ticket = identity.sessions.issue(
        Principal(
            "browser-test-manager",
            identity.policy.tenant_id,
            int(clock[0].timestamp()) + 900,
            (identity.policy.manager_role, "Configuration.Test"),
        )
    )
    signer = TestPermitSigner(
        issuer="https://management.example.invalid/test-permits",
        audience="runtime-fixture",
        key_id="management-test-key",
        signing_key=signing_key,
        verification_keys={"management-test-key": signing_key.public_key()},
        utc_now=lambda: clock[0],
        max_clock_skew_seconds=0,
    )
    telemetry = Telemetry("management", "local")

    class Relay:
        delegate = None

        async def run(self, socket, **kwargs):
            if self.delegate is None:
                await socket.close(code=1013, reason="test_unavailable")
                return
            await self.delegate.run(socket, **kwargs)

    relay = Relay()
    client.app.state.test_relay = relay
    client.app.include_router(
        create_router(
            identity=identity,
            signer=signer,
            test_role="Configuration.Test",
            telemetry=telemetry,
            utc_now=lambda: clock[0],
            monotonic=lambda: ticks[0],
            max_clock_skew_seconds=0,
            relay=relay,
        )
    )
    try:
        yield client, identity, ticket, signer, clock, ticks, telemetry
    finally:
        telemetry.close()


def test_ticket_post_has_fixed_path_short_signed_scope_and_no_provider_credentials(ticket_api):
    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    response = client.post(
        "/v1/console/tickets",
        json={"mode": "text", "requestId": "request-one"},
        headers=browser_headers(identity, ticket),
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    result = response.json()
    assert set(result) == {
        "permit",
        "mode",
        "requestId",
        "socketPath",
        "expiresAt",
        "admissionDeadlineUtc",
    }
    assert result["socketPath"] == "/v1/console/sessions" and result["mode"] == "text"
    claims = signer.verify(
        result["permit"],
        management_session_digest=identity.sessions.digest(ticket.cookie),
        tenant_id=identity.policy.tenant_id,
        actor_id="browser-test-manager",
    )
    assert claims.admission_started_at == clock[0] and claims.admission_deadline_utc == clock[
        0
    ] + timedelta(seconds=10)
    assert claims.expires_epoch - claims.issued_epoch == 30
    assert claims.management_session_digest == identity.sessions.digest(ticket.cookie)
    assert result["permit"] not in json.dumps(telemetry.events)


def test_ticket_authorization_time_uses_original_receipt_deadline_not_new_budget(
    ticket_api, monkeypatch
):
    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    started = clock[0]
    authorize = identity.require_test_manager
    charged = []

    def delayed(request, **kwargs):
        if not charged:
            clock[0] += timedelta(seconds=7)
            ticks[0] += 7
            charged.append(True)
        return authorize(request, **kwargs)

    monkeypatch.setattr(identity, "require_test_manager", delayed)
    response = client.post(
        "/v1/console/tickets",
        json={"mode": "speech", "requestId": "delayed-request"},
        headers=browser_headers(identity, ticket),
    )
    assert response.status_code == 200
    permit = signer.verify(
        response.json()["permit"],
        management_session_digest=identity.sessions.digest(ticket.cookie),
        tenant_id=identity.policy.tenant_id,
        actor_id="browser-test-manager",
    )
    assert (
        permit.admission_started_at == started
        and permit.admission_deadline_utc == started + timedelta(seconds=10)
    )
    assert permit.issued_at == started + timedelta(seconds=7)


def test_duplicate_ticket_delivery_never_resets_nonce_or_original_startup_time(ticket_api):
    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    body = {"mode": "text", "requestId": "same-request"}
    first = client.post("/v1/console/tickets", json=body, headers=browser_headers(identity, ticket))
    clock[0] += timedelta(seconds=2)
    ticks[0] += 2
    repeated = client.post(
        "/v1/console/tickets", json=body, headers=browser_headers(identity, ticket)
    )
    assert first.status_code == repeated.status_code == 200 and repeated.json() == first.json()
    clock[0] += timedelta(seconds=8)
    ticks[0] += 8
    expired = client.post(
        "/v1/console/tickets", json=body, headers=browser_headers(identity, ticket)
    )
    assert expired.status_code == 504
    assert expired.json()["code"] == "test_deadline_exceeded"


def test_ticket_late_authorization_never_signs_or_extends_startup(ticket_api, monkeypatch):
    from unittest.mock import Mock

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    authorize = identity.require_test_manager

    def delayed(request, **kwargs):
        clock[0] += timedelta(seconds=10)
        ticks[0] += 10
        return authorize(request, **kwargs)

    sign = Mock(wraps=signer.sign)
    monkeypatch.setattr(identity, "require_test_manager", delayed)
    monkeypatch.setattr(signer, "sign", sign)
    response = client.post(
        "/v1/console/tickets",
        json={"mode": "speech", "requestId": "late-request"},
        headers=browser_headers(identity, ticket),
    )
    assert response.status_code == 504 and response.json()["code"] == "test_deadline_exceeded"
    sign.assert_not_called()


def test_ticket_rechecks_logout_before_signing_after_request_processing(ticket_api, monkeypatch):
    from unittest.mock import Mock

    from management_app.models.test_session import TicketRequest

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    parse = TicketRequest.model_validate_json

    def parse_and_revoke(content, **kwargs):
        result = parse(content, **kwargs)
        identity.sessions.revoke(ticket.cookie)
        return result

    sign = Mock(wraps=signer.sign)
    monkeypatch.setattr(TicketRequest, "model_validate_json", parse_and_revoke)
    monkeypatch.setattr(signer, "sign", sign)
    response = client.post(
        "/v1/console/tickets",
        json={"mode": "text", "requestId": "revoked-request"},
        headers=browser_headers(identity, ticket),
    )
    assert response.status_code == 401
    sign.assert_not_called()


@pytest.mark.parametrize(
    "fault",
    ["missing-origin", "null-origin", "foreign-origin", "missing-cookie", "bearer", "plaintext"],
)
def test_raw_test_socket_denies_bad_origin_cookie_or_transport_before_upgrade(ticket_api, fault):
    from starlette.websockets import WebSocketDisconnect

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    headers = browser_headers(identity, ticket)
    headers.pop("X-CSRF-Token")
    if fault == "missing-origin":
        headers.pop("Origin")
    elif fault == "null-origin":
        headers["Origin"] = "null"
    elif fault == "foreign-origin":
        headers["Origin"] = "https://foreign.fixture.example"
    elif fault == "missing-cookie":
        headers.pop("Cookie")
    elif fault == "bearer":
        headers["Authorization"] = "Bearer synthetic-client-token"
    with pytest.raises(WebSocketDisconnect) as failure:
        with client.websocket_connect(
            "ws://management.example.invalid/v1/console/sessions"
            if fault == "plaintext"
            else "wss://management.example.invalid/v1/console/sessions",
            subprotocols=["caller.console.v1"],
            headers=headers,
        ):
            pytest.fail("unverified_raw_socket_upgraded")
    assert failure.value.code == 1008


def test_raw_test_socket_verifies_first_permit_against_issued_cookie_before_any_relay(ticket_api):
    from starlette.websockets import WebSocketDisconnect

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    headers = browser_headers(identity, ticket)
    issued = client.post(
        "/v1/console/tickets", json={"mode": "text", "requestId": "socket-request"}, headers=headers
    ).json()
    headers.pop("X-CSRF-Token")
    with client.websocket_connect(
        "wss://management.example.invalid/v1/console/sessions",
        subprotocols=["caller.console.v1"],
        headers=headers,
    ) as socket:
        assert socket.accepted_subprotocol == "caller.console.v1"
        socket.send_json(
            {"type": "authenticate", "permit": issued["permit"], "requestId": "foreign-request"}
        )
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 1008


def test_raw_test_socket_rejects_late_first_message_despite_unexpired_signed_permit(ticket_api):
    from starlette.websockets import WebSocketDisconnect

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    headers = browser_headers(identity, ticket)
    issued = client.post(
        "/v1/console/tickets", json={"mode": "text", "requestId": "late-socket"}, headers=headers
    ).json()
    headers.pop("X-CSRF-Token")
    with client.websocket_connect(
        "wss://management.example.invalid/v1/console/sessions",
        subprotocols=["caller.console.v1"],
        headers=headers,
    ) as socket:
        clock[0] += timedelta(seconds=10)
        ticks[0] += 10
        socket.send_json(
            {"type": "authenticate", "permit": issued["permit"], "requestId": "late-socket"}
        )
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 1008


def test_signed_ticket_handoff_is_one_use_current_cookie_and_original_remaining_budget(ticket_api):
    import asyncio

    from fastapi import HTTPException
    from starlette.websockets import WebSocketDisconnect

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    headers = browser_headers(identity, ticket)
    issued = client.post(
        "/v1/console/tickets",
        json={"mode": "text", "requestId": "handoff-request"},
        headers=headers,
    ).json()
    calls = []

    class Relay:
        async def run(self, socket, **kwargs):
            calls.append(kwargs)
            current = kwargs["validate_current"]()
            assert current.principal.actor_id == "browser-test-manager"
            assert 0 < kwargs["deadline"] - asyncio.get_running_loop().time() <= 8
            identity.sessions.revoke(ticket.cookie)
            with pytest.raises(HTTPException):
                kwargs["validate_current"]()
            await socket.close(code=1000, reason="controlled-finished")

    client.app.state.test_relay.delegate = Relay()
    clock[0] += timedelta(seconds=2)
    ticks[0] += 2
    headers.pop("X-CSRF-Token")
    with client.websocket_connect(
        "wss://management.example.invalid/v1/console/sessions",
        subprotocols=["caller.console.v1"],
        headers=headers,
    ) as socket:
        socket.send_json(
            {"type": "authenticate", "permit": issued["permit"], "requestId": "handoff-request"}
        )
        with pytest.raises(WebSocketDisconnect) as ended:
            socket.receive_json()
        assert ended.value.code == 1000
    assert len(calls) == 1 and calls[0]["signed_permit"] == issued["permit"]
    assert calls[0]["permit"].admission_deadline_utc == clock[0] + timedelta(seconds=8)


def test_consumed_ticket_cannot_be_redelivered_or_relayed_again_with_still_valid_cookie(ticket_api):
    from starlette.websockets import WebSocketDisconnect

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    headers = browser_headers(identity, ticket)
    body = {"mode": "text", "requestId": "consumed-request"}
    issued = client.post("/v1/console/tickets", json=body, headers=headers).json()
    calls = []

    class Relay:
        async def run(self, socket, **kwargs):
            kwargs["validate_current"]()
            calls.append(True)
            await socket.close(code=1000, reason="controlled-finished")

    client.app.state.test_relay.delegate = Relay()
    ws_headers = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
    with client.websocket_connect(
        "wss://management.example.invalid/v1/console/sessions",
        subprotocols=["caller.console.v1"],
        headers=ws_headers,
    ) as socket:
        socket.send_json(
            {"type": "authenticate", "permit": issued["permit"], "requestId": body["requestId"]}
        )
        with pytest.raises(WebSocketDisconnect) as ended:
            socket.receive_json()
        assert ended.value.code == 1000
    assert client.post("/v1/console/tickets", json=body, headers=headers).status_code == 409
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(
            "wss://management.example.invalid/v1/console/sessions",
            subprotocols=["caller.console.v1"],
            headers=ws_headers,
        ):
            pytest.fail("consumed_ticket_socket_upgraded")
    assert rejected.value.code == 1008 and calls == [True]


def test_ticket_gateway_uses_real_bounded_voice_relay_without_credential_echo(ticket_api):
    from starlette.websockets import WebSocketDisconnect

    from tests.unit.test_test_console import build_active_relay_case

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    (
        relay,
        fake_browser,
        runtime,
        permit,
        credentials,
        sent,
        displayed,
        relay_clock,
        relay_ticks,
        envelope,
    ) = build_active_relay_case()
    client.app.state.test_relay.delegate = relay
    headers = browser_headers(identity, ticket)
    issued = client.post(
        "/v1/console/tickets",
        json={"mode": "speech", "requestId": "real-relay-request"},
        headers=headers,
    ).json()
    headers.pop("X-CSRF-Token")
    received = []
    with client.websocket_connect(
        "wss://management.example.invalid/v1/console/sessions",
        subprotocols=["caller.console.v1"],
        headers=headers,
    ) as socket:
        socket.send_json(
            {"type": "authenticate", "permit": issued["permit"], "requestId": "real-relay-request"}
        )
        ready = socket.receive_json()
        assert ready["type"] == "ready" and ready["mode"] == "speech"
        credit = socket.receive_json()
        received.append(credit)
        assert credit["type"] == "audio.credit" and credit["availableSamples"] == 480
        socket.send_json({"type": "stop", "sequence": 1, "sessionId": ready["sessionId"]})
        assert socket.receive_json()["type"] == "ended"
        with pytest.raises(WebSocketDisconnect) as ended:
            socket.receive_json()
        assert ended.value.code == 1000
    assert json.loads(sent[0])["permit"] == issued["permit"]
    assert json.loads(sent[1])["type"] == "stop"
    assert "synthetic-workload-token" not in json.dumps(received)
    assert issued["permit"] not in json.dumps(received) + json.dumps(telemetry.events)
    assert client.portal is not None
    client.portal.call(relay.close)


def test_raw_gateway_pre_auth_is_two_per_verified_manager_and_releases_on_disconnect(ticket_api):
    from contextlib import ExitStack

    from starlette.websockets import WebSocketDisconnect

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    headers = browser_headers(identity, ticket)
    response = client.post(
        "/v1/console/tickets",
        json={"mode": "text", "requestId": "pending-request"},
        headers=headers,
    )
    assert response.status_code == 200
    headers.pop("X-CSRF-Token")
    with ExitStack() as stack:
        sockets = [
            stack.enter_context(
                client.websocket_connect(
                    "wss://management.example.invalid/v1/console/sessions",
                    subprotocols=["caller.console.v1"],
                    headers=headers,
                )
            )
            for _ in range(2)
        ]
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                "wss://management.example.invalid/v1/console/sessions",
                subprotocols=["caller.console.v1"],
                headers=headers,
            ):
                pytest.fail("third_manager_pre_auth_socket_upgraded")
        assert rejected.value.code == 1013
        for socket in sockets:
            socket.close()
    with client.websocket_connect(
        "wss://management.example.invalid/v1/console/sessions",
        subprotocols=["caller.console.v1"],
        headers=headers,
    ) as recovered:
        assert recovered.accepted_subprotocol == "caller.console.v1"
        recovered.close()


def test_raw_gateway_pre_auth_has_ten_global_slots_and_no_runtime_allocation(ticket_api):
    from contextlib import ExitStack

    from starlette.websockets import WebSocketDisconnect

    from management_app.security import Principal

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    headers = []
    for number in range(6):
        scoped = identity.sessions.issue(
            Principal(
                "pre-auth-" + str(number),
                identity.policy.tenant_id,
                int(clock[0].timestamp()) + 900,
                (identity.policy.manager_role, "Configuration.Test"),
            )
        )
        current = browser_headers(identity, scoped)
        response = client.post(
            "/v1/console/tickets",
            json={"mode": "text", "requestId": "pre-" + str(number)},
            headers=current,
        )
        assert response.status_code == 200
        current.pop("X-CSRF-Token")
        headers.append(current)
    with ExitStack() as stack:
        sockets = [
            stack.enter_context(
                client.websocket_connect(
                    "wss://management.example.invalid/v1/console/sessions",
                    subprotocols=["caller.console.v1"],
                    headers=headers[number // 2],
                )
            )
            for number in range(10)
        ]
        with pytest.raises(WebSocketDisconnect) as failure:
            with client.websocket_connect(
                "wss://management.example.invalid/v1/console/sessions",
                subprotocols=["caller.console.v1"],
                headers=headers[5],
            ):
                pytest.fail("eleventh_pre_auth_socket_upgraded")
        assert failure.value.code == 1013 and client.app.state.test_relay.delegate is None
        for socket in sockets:
            socket.close()
    with client.websocket_connect(
        "wss://management.example.invalid/v1/console/sessions",
        subprotocols=["caller.console.v1"],
        headers=headers[5],
    ) as recovered:
        assert recovered.accepted_subprotocol == "caller.console.v1"
        recovered.close()


def test_raw_gateway_logout_before_first_message_cannot_invoke_relay(ticket_api):
    from starlette.websockets import WebSocketDisconnect

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    headers = browser_headers(identity, ticket)
    issued = client.post(
        "/v1/console/tickets", json={"mode": "text", "requestId": "logout-pending"}, headers=headers
    ).json()
    calls = []

    class Forbidden:
        async def run(self, socket, **kwargs):
            calls.append(True)
            pytest.fail("revoked_cookie_invoked_relay")

    client.app.state.test_relay.delegate = Forbidden()
    headers.pop("X-CSRF-Token")
    with client.websocket_connect(
        "wss://management.example.invalid/v1/console/sessions",
        subprotocols=["caller.console.v1"],
        headers=headers,
    ) as socket:
        identity.sessions.revoke(ticket.cookie)
        socket.send_json(
            {"type": "authenticate", "permit": issued["permit"], "requestId": "logout-pending"}
        )
        with pytest.raises(WebSocketDisconnect) as rejected:
            socket.receive_json()
        assert rejected.value.code == 1008
    assert not calls


def test_raw_gateway_first_message_uses_only_remaining_signed_startup_budget(ticket_api):
    import time

    from starlette.websockets import WebSocketDisconnect

    client, identity, ticket, signer, clock, ticks, telemetry = ticket_api
    headers = browser_headers(identity, ticket)
    assert (
        client.post(
            "/v1/console/tickets",
            json={"mode": "text", "requestId": "nearly-expired"},
            headers=headers,
        ).status_code
        == 200
    )
    clock[0] += timedelta(seconds=9.95)
    ticks[0] += 9.95
    headers.pop("X-CSRF-Token")
    started = time.monotonic()
    with client.websocket_connect(
        "wss://management.example.invalid/v1/console/sessions",
        subprotocols=["caller.console.v1"],
        headers=headers,
    ) as socket:
        with pytest.raises(WebSocketDisconnect) as ended:
            socket.receive_json()
        assert ended.value.code == 1008
    assert time.monotonic() - started < 0.5
