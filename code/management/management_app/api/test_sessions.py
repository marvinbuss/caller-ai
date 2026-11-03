"""Explicit cookie-authorized test tickets; no model allocation or ambient relay ports."""

import asyncio
import hashlib
import json
import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request, WebSocket
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState

from management_app.api.configuration import unique_object
from management_app.models.test_session import (
    Authenticate,
    StartPermit,
    TicketRequest,
    TicketResponse,
    utc,
)
from management_app.observability import Telemetry, sanitize_correlation_id
from management_app.security import IdentityService, ServerSession, TestPermitSigner

MAX_CONTROL_BYTES = 8192
ERROR_CODES = {
    401: "authentication_required",
    403: "test_permission_denied",
    409: "test_request_conflict",
    413: "test_control_too_large",
    422: "invalid_test_request",
    503: "test_unavailable",
    504: "test_deadline_exceeded",
}


@dataclass(slots=True)
class IssuedTicket:
    claims: StartPermit = field(repr=False)
    response: TicketResponse | None = field(repr=False)
    started_tick: float
    consumed: bool = False


class RelayPort(Protocol):
    async def run(
        self,
        socket: WebSocket,
        *,
        permit: StartPermit,
        signed_permit: str,
        validate_current: Callable[[], ServerSession],
        deadline: float,
    ) -> None: ...


def problem(status: int, correlation: str) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "X-Correlation-ID": correlation}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        {
            "type": "about:blank",
            "title": HTTPStatus(status).phrase,
            "status": status,
            "code": ERROR_CODES[status],
            "correlation_id": correlation,
        },
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )


def create_router(
    *,
    identity: IdentityService,
    signer: TestPermitSigner,
    test_role: str,
    telemetry: Telemetry,
    utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float],
    max_clock_skew_seconds: float,
    relay: RelayPort | None = None,
) -> APIRouter:
    if (
        any(port is None for port in (identity, signer, telemetry, utc_now, monotonic))
        or not isinstance(test_role, str)
        or not test_role
        or len(test_role) > 128
        or type(max_clock_skew_seconds) not in (int, float)
        or not math.isfinite(max_clock_skew_seconds)
        or not 0 <= max_clock_skew_seconds <= 1
    ):
        raise ValueError("explicit_browser_test_ports_required")
    router = APIRouter()
    issued: dict[tuple[str, str, str], IssuedTicket] = {}
    observed: tuple[datetime, float] | None = None
    pending = 0
    pending_by_actor: dict[str, int] = {}

    def clock() -> tuple[datetime, float]:
        nonlocal observed
        now, tick = utc(utc_now()), monotonic()
        if not math.isfinite(tick):
            raise HTTPException(503, "test_clock_unavailable")
        if observed is not None:
            previous, prior_tick = observed
            elapsed = (now - previous).total_seconds()
            if (
                elapsed < 0
                or tick < prior_tick
                or abs(elapsed - (tick - prior_tick)) > max_clock_skew_seconds
            ):
                raise HTTPException(503, "test_clock_unavailable")
        observed = now, tick
        return now, tick

    def remaining(started: datetime, started_tick: float) -> tuple[datetime, float]:
        now, tick = clock()
        budget = (
            min((started + timedelta(seconds=10) - now).total_seconds(), 10 - (tick - started_tick))
            - max_clock_skew_seconds
        )
        if budget <= 0:
            raise HTTPException(504, "test_deadline_exceeded")
        return now, budget

    def purge(now: datetime) -> None:
        for key, record in tuple(issued.items()):
            if record.claims.auth_expires_at <= now:
                issued.pop(key, None)
            elif now.timestamp() >= record.claims.expires_epoch:
                record.response = None

    @router.post("/v1/test-tickets")
    async def ticket(request: Request) -> JSONResponse:
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        mode = "text"
        try:
            started, started_tick = clock()
            deadline = asyncio.get_running_loop().time() + 10
            async with asyncio.timeout_at(deadline):
                session = identity.require_test_manager(request, mutation=True, test_role=test_role)
                now, budget = remaining(started, started_tick)
                deadline = min(deadline, asyncio.get_running_loop().time() + budget)
                if request.query_params:
                    raise HTTPException(422, "invalid_test_request")
                body = bytearray()
                async with asyncio.timeout_at(deadline):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > MAX_CONTROL_BYTES:
                            raise HTTPException(413, "test_control_too_large")
                        body.extend(chunk)
                import json

                try:
                    document = json.loads(body, object_pairs_hook=unique_object)
                    control = TicketRequest.model_validate_json(
                        json.dumps(document, allow_nan=False)
                    )
                except Exception:
                    raise HTTPException(422, "invalid_test_request") from None
                mode = control.mode
                now, _budget = remaining(started, started_tick)
                session = identity.require_test_manager(request, mutation=True, test_role=test_role)
                now, _budget = remaining(started, started_tick)
                purge(now)
                principal = session.principal
                session_digest = identity.sessions.digest(request.cookies[identity.cookie_name])
                key = principal.tenant_id, principal.actor_id, control.request_id
                previous = issued.get(key)
                if previous is not None:
                    if (
                        previous.claims.management_session_digest != session_digest
                        or previous.claims.mode != mode
                        or previous.consumed
                    ):
                        raise HTTPException(409, "test_request_conflict")
                    remaining(previous.claims.admission_started_at, previous.started_tick)
                    if previous.response is None:
                        raise HTTPException(504, "test_deadline_exceeded")
                    return JSONResponse(
                        previous.response.model_dump(mode="json", by_alias=True),
                        headers={"Cache-Control": "no-store", "X-Correlation-ID": correlation},
                    )
                if len(issued) >= 100:
                    raise HTTPException(503, "test_ticket_capacity")
                expiry = datetime.fromtimestamp(min(session.expires_at, principal.expires_at), UTC)
                claims = StartPermit(
                    iss=signer.issuer,
                    aud=signer.audience,
                    iat=int(now.timestamp()),
                    exp=min(int(now.timestamp()) + 30, int(expiry.timestamp())),
                    issuedAt=now,
                    admissionStartedAt=started,
                    admissionDeadlineUtc=started + timedelta(seconds=10),
                    tenantId=principal.tenant_id,
                    actorId=principal.actor_id,
                    mode=mode,
                    managementSessionDigest=session_digest,
                    requestId=control.request_id,
                    nonce=secrets.token_hex(32),
                    authExpiresAt=expiry,
                )
                raw = signer.sign(claims)
                remaining(started, started_tick)
                response = TicketResponse(
                    permit=raw,
                    mode=mode,
                    requestId=control.request_id,
                    socketPath="/v1/test-sessions",
                    expiresAt=datetime.fromtimestamp(claims.expires_epoch, UTC),
                    admissionDeadlineUtc=claims.admission_deadline_utc,
                )
                issued[key] = IssuedTicket(claims, response, started_tick)
                return JSONResponse(
                    response.model_dump(mode="json", by_alias=True),
                    headers={"Cache-Control": "no-store", "X-Correlation-ID": correlation},
                )
        except HTTPException as error:
            status = error.status_code if error.status_code in ERROR_CODES else 503
        except TimeoutError:
            status = 504
        except Exception:
            status = 503
        telemetry.event(
            "test.admission",
            "failure",
            mode=mode,
            reason="deadline"
            if status == 504
            else "denied"
            if status in {401, 403, 409}
            else "unavailable",
        )
        return problem(status, correlation)

    @router.websocket("/v1/test-sessions")
    async def test_socket(socket: WebSocket) -> None:
        nonlocal pending
        actor_key: str | None = None
        counted = False
        try:
            if (
                socket.scope.get("scheme") != "wss"
                or socket.query_params
                or socket.scope.get("subprotocols", []).count("caller.test.v1") != 1
            ):
                raise PermissionError("test_permission_denied")
            session = identity.require_test_manager(socket, mutation=False, test_role=test_role)
            principal = session.principal
            actor_key = hashlib.sha256(
                (principal.tenant_id + "\0" + principal.actor_id).encode()
            ).hexdigest()
            if pending >= 10 or pending_by_actor.get(actor_key, 0) >= 2:
                await socket.close(code=1013, reason="test_pressure")
                return
            pending += 1
            pending_by_actor[actor_key] = pending_by_actor.get(actor_key, 0) + 1
            counted = True
            now, _tick = clock()
            session_digest = identity.sessions.digest(socket.cookies[identity.cookie_name])
            candidates = [
                record
                for record in issued.values()
                if record.claims.actor_key == actor_key
                and record.claims.management_session_digest == session_digest
                and not record.consumed
                and record.claims.admission_deadline_utc > now
            ]
            if not candidates:
                raise PermissionError("unissued_test_permit")
            remaining_bounds = [
                remaining(record.claims.admission_started_at, record.started_tick)[1]
                for record in candidates
            ]
            first_deadline = asyncio.get_running_loop().time() + min(3, min(remaining_bounds))
            await socket.accept(
                subprotocol="caller.test.v1", headers=[(b"cache-control", b"no-store")]
            )
            async with asyncio.timeout_at(first_deadline):
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                raw = message.get("text")
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CONTROL_BYTES:
                    raise PermissionError("invalid_test_authentication")
                auth = Authenticate.model_validate_json(
                    json.dumps(json.loads(raw, object_pairs_hook=unique_object), allow_nan=False)
                )
                session = identity.require_test_manager(socket, mutation=False, test_role=test_role)
                principal = session.principal
                permit = signer.verify(
                    auth.permit,
                    management_session_digest=session_digest,
                    tenant_id=principal.tenant_id,
                    actor_id=principal.actor_id,
                )
                key = principal.tenant_id, principal.actor_id, auth.request_id
                record = issued.get(key)
                if (
                    record is None
                    or record.consumed
                    or record.claims != permit
                    or permit.request_id != auth.request_id
                ):
                    raise PermissionError("unissued_test_permit")
                now, budget = remaining(permit.admission_started_at, record.started_tick)
                record.consumed = True
                record.response = None
            pending -= 1
            pending_by_actor[actor_key] -= 1
            if not pending_by_actor[actor_key]:
                pending_by_actor.pop(actor_key, None)
            counted = False
            if relay is None:
                await socket.close(code=1013, reason="test_unavailable")
                return

            def validate_current() -> ServerSession:
                current = identity.require_test_manager(socket, mutation=False, test_role=test_role)
                if (
                    current.principal.tenant_id != permit.tenant_id
                    or current.principal.actor_id != permit.actor_id
                    or identity.sessions.digest(socket.cookies[identity.cookie_name])
                    != permit.management_session_digest
                ):
                    raise PermissionError("test_identity_changed")
                return current

            operation = relay.run(
                socket,
                permit=permit,
                signed_permit=auth.permit,
                validate_current=validate_current,
                deadline=asyncio.get_running_loop().time() + budget,
            )
            message.clear()
            raw = ""
            del auth
            await operation
        except WebSocketDisconnect:
            pass
        except (HTTPException, PermissionError, ValueError):
            if socket.application_state != WebSocketState.DISCONNECTED:
                await socket.close(code=1008, reason="test_permission_denied")
        except TimeoutError:
            if socket.application_state != WebSocketState.DISCONNECTED:
                await socket.close(code=1008, reason="test_authentication_deadline")
        except Exception:
            if socket.application_state != WebSocketState.DISCONNECTED:
                await socket.close(code=1011, reason="test_unavailable")
        finally:
            if counted:
                pending -= 1
                if actor_key is not None:
                    pending_by_actor[actor_key] -= 1
                    if not pending_by_actor[actor_key]:
                        pending_by_actor.pop(actor_key, None)

    return router
