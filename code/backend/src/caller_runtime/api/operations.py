"""Explicit internal operator API; foundation health never mounts drain controls."""

import asyncio
import re
from http import HTTPStatus
from typing import Any, Literal, Protocol

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse

from caller_runtime.observability import Telemetry, sanitize_correlation_id
from caller_runtime.services.drain import DrainService

ERROR_CODES = {
    401: "authentication_required",
    403: "drain_permission_denied",
    404: "drain_owner_unknown",
    422: "invalid_drain_request",
    503: "drain_control_unavailable",
    504: "drain_deadline_exceeded",
}


class OperatorIdentityPort(Protocol):
    async def require_operator(self, request: Request) -> str:
        """Authenticate a least-privileged internal operator, never a model/header assertion."""
        ...


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
        headers=headers,
        media_type="application/problem+json",
    )


def create_router(
    *, drain: DrainService, identity: OperatorIdentityPort, telemetry: Telemetry
) -> APIRouter:
    if any(port is None for port in (drain, identity, telemetry)):
        raise ValueError("explicit_internal_operations_ports_required")
    router = APIRouter()

    async def handle(
        request: Request, owner_id: str, operation: Literal["start", "status", "finish"]
    ) -> JSONResponse:
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        with telemetry.span("http.request", headers=request.headers):
            try:
                async with asyncio.timeout(10):
                    actor = await identity.require_operator(request)
                    if (
                        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", owner_id)
                        or request.query_params
                    ):
                        raise HTTPException(422, "invalid_drain_request")
                    async for chunk in request.stream():
                        if chunk:
                            raise HTTPException(422, "unexpected_drain_body")
                    outcome = None
                    if operation == "start":
                        outcome = await drain.begin(owner_id, principal=actor)
                    elif operation == "finish":
                        outcome = await drain.finish(owner_id, principal=actor)
                    body: dict[str, Any] = {
                        "ownerId": owner_id,
                        "state": await drain.state(owner_id),
                        "canTerminate": await drain.can_terminate(owner_id),
                    }
                    if outcome is not None:
                        body["outcome"] = outcome
                    telemetry.event("request", "success", correlation_id=correlation)
                    return JSONResponse(
                        body, headers={"Cache-Control": "no-store", "X-Correlation-ID": correlation}
                    )
            except HTTPException as error:
                status = error.status_code if error.status_code in ERROR_CODES else 503
            except PermissionError:
                status = 403
            except KeyError:
                status = 404
            except TimeoutError:
                status = 504
            except Exception:
                status = 503
            telemetry.event("request", "failure", correlation_id=correlation)
            return problem(status, correlation)

    @router.post("/v1/operations/drain/{owner_id}")
    async def begin(request: Request, owner_id: str) -> JSONResponse:
        return await handle(request, owner_id, "start")

    @router.get("/v1/operations/drain/{owner_id}")
    async def state(request: Request, owner_id: str) -> JSONResponse:
        return await handle(request, owner_id, "status")

    @router.post("/v1/operations/drain/{owner_id}/complete")
    async def finish(request: Request, owner_id: str) -> JSONResponse:
        return await handle(request, owner_id, "finish")

    return router
