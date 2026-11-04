"""Runtime configuration API: `GET /v2/runtime/configuration`, for the backend workload only.

Returns the active revision's runtime projection (no layout, no credential references) with its
ETag, or 304 when `If-None-Match` matches. The backend identity gets no other access to management
data, and failures are fixed `application/problem+json` codes.
"""

import asyncio
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse, Response

from management_app.adapters.configuration_repository import Publication, verified_revision
from management_app.api.capabilities import RuntimeIdentityPort
from management_app.api.configuration import ETAG
from management_app.api.problems import problem_response
from management_app.observability import Telemetry, sanitize_correlation_id
from management_app.services.configuration_publication import runtime_projection

ERROR_CODES = {
    401: "authentication_required",
    403: "permission_denied",
    422: "invalid_snapshot_request",
    503: "configuration_unavailable",
    504: "configuration_deadline_exceeded",
}


class RepositoryPort(Protocol):
    async def read_active(self) -> Publication | None: ...


def problem(status: int, correlation: str) -> JSONResponse:
    return problem_response(status, correlation, ERROR_CODES[status])


def create_router(
    *, repository: RepositoryPort, identity: RuntimeIdentityPort, telemetry: Telemetry
) -> APIRouter:
    if any(port is None for port in (repository, identity, telemetry)):
        raise ValueError("explicit_runtime_configuration_ports_required")
    router = APIRouter()

    @router.get("/v2/runtime/configuration")
    async def configuration(request: Request) -> Response:
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        with telemetry.span("http.request", headers=request.headers):
            try:
                async with asyncio.timeout(10):
                    await identity.require_runtime(request)
                    if request.query_params:
                        raise HTTPException(422, "unsupported_parameters")
                    async for chunk in request.stream():
                        if chunk:
                            raise HTTPException(422, "unexpected_snapshot_body")
                    conditions = request.headers.getlist("if-none-match")
                    if len(conditions) > 1 or (conditions and not ETAG.fullmatch(conditions[0])):
                        raise HTTPException(422, "invalid_snapshot_condition")
                    active = await repository.read_active()
                    if active is None:
                        raise HTTPException(503, "configuration_unavailable")
                    document, etag = active
                    if not ETAG.fullmatch(etag):
                        raise ValueError("invalid_pointer_etag")
                    snapshot = runtime_projection(verified_revision(document))
                    headers = {
                        "ETag": etag,
                        "Cache-Control": "private, no-cache",
                        "Vary": "Authorization",
                        "X-Correlation-ID": correlation,
                    }
                    telemetry.event("request", "success", correlation_id=correlation)
                    if conditions and conditions[0] == etag:
                        return Response(status_code=304, headers=headers)
                    return JSONResponse(snapshot, headers=headers)
            except HTTPException as error:
                status = error.status_code if error.status_code in ERROR_CODES else 503
            except TimeoutError:
                status = 504
            except Exception:
                status = 503
            telemetry.event("request", "failure", correlation_id=correlation)
            return problem(status, correlation)

    return router
