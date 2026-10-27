"""Explicit capability API composition; never mounted by foundation startup."""

import asyncio
import json
from http import HTTPStatus
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from starlette.responses import JSONResponse

from management_app.api.configuration import ETAG, unique_object
from management_app.models.permissions import (
    CapabilityAttempt,
    PermissionConflict,
    RevocationRequest,
)
from management_app.observability import Telemetry, sanitize_correlation_id
from management_app.services.capability_gateway import GatewayError
from management_app.services.configuration_validation import encoded

ERROR_CODES = {
    401: "authentication_required",
    403: "permission_denied",
    412: "permission_conflict",
    413: "capability_request_too_large",
    422: "invalid_capability_request",
    428: "precondition_required",
    503: "capability_unavailable",
    504: "capability_deadline_exceeded",
}


class RuntimeIdentityPort(Protocol):
    async def require_runtime(self, request: Request) -> str: ...


class ManagerIdentityPort(Protocol):
    async def require_manager(self, request: Request) -> str: ...


class GatewayPort(Protocol):
    async def execute(self, request: CapabilityAttempt, *, actor_id: str) -> dict[str, Any]: ...


class RevocationRepository(Protocol):
    async def revoke_capabilities(
        self, revocations: dict[str, Any], *, if_match: str, actor_id: str
    ) -> dict[str, Any]: ...


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


async def payload(request: Request) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, "unsupported_parameters")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 64 * 1024:
            raise HTTPException(413, "capability_request_too_large")
    try:
        document = json.loads(data, object_pairs_hook=unique_object)
        if not isinstance(document, dict):
            raise ValueError("object_required")
        encoded(document)
        return document
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "invalid_capability_request") from None


def create_router(
    *,
    gateway: GatewayPort,
    repository: RevocationRepository,
    runtime_identity: RuntimeIdentityPort,
    manager_identity: ManagerIdentityPort,
    telemetry: Telemetry,
) -> APIRouter:
    if any(
        port is None
        for port in (gateway, repository, runtime_identity, manager_identity, telemetry)
    ):
        raise ValueError("explicit_capability_ports_required")
    router = APIRouter()

    @router.post("/v1/runtime/capabilities/execute")
    async def execute(request: Request) -> JSONResponse:
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        with telemetry.span("http.request", headers=request.headers):
            try:
                async with asyncio.timeout(10):
                    actor = await runtime_identity.require_runtime(request)
                    document = await payload(request)
                    try:
                        attempt = CapabilityAttempt.model_validate_json(encoded(document))
                    except ValidationError:
                        raise HTTPException(422, "invalid_capability_request") from None
                    result = await gateway.execute(attempt, actor_id=actor)
                    telemetry.event("request", "success", correlation_id=correlation)
                    return JSONResponse(
                        result,
                        headers={"Cache-Control": "no-store", "X-Correlation-ID": correlation},
                    )
            except GatewayError as error:
                status = error.status
            except HTTPException as error:
                status = error.status_code if error.status_code in ERROR_CODES else 503
            except TimeoutError:
                status = 504
            except Exception:
                status = 503
            telemetry.event("request", "failure", correlation_id=correlation)
            return problem(status, correlation)

    @router.put("/v1/security/revocations")
    async def revoke(request: Request) -> JSONResponse:
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        with telemetry.span("http.request", headers=request.headers):
            try:
                async with asyncio.timeout(10):
                    actor = await manager_identity.require_manager(request)
                    etags = request.headers.getlist("if-match")
                    if not etags:
                        raise HTTPException(428, "precondition_required")
                    if len(etags) != 1 or not ETAG.fullmatch(etags[0]):
                        raise HTTPException(412, "permission_conflict")
                    document = await payload(request)
                    try:
                        revocations = RevocationRequest.model_validate_json(encoded(document))
                    except ValidationError:
                        raise HTTPException(422, "invalid_revocations") from None
                    result = await repository.revoke_capabilities(
                        revocations.model_dump(by_alias=True), if_match=etags[0], actor_id=actor
                    )
                    if not ETAG.fullmatch(result["_etag"]):
                        raise RuntimeError("invalid_authority_response")
                    telemetry.event("request", "success", correlation_id=correlation)
                    return JSONResponse(
                        {"policyVersion": result["policyVersion"]},
                        headers={
                            "ETag": result["_etag"],
                            "Cache-Control": "no-store",
                            "X-Correlation-ID": correlation,
                        },
                    )
            except PermissionConflict:
                status = 412
            except HTTPException as error:
                status = error.status_code if error.status_code in ERROR_CODES else 503
            except TimeoutError:
                status = 504
            except Exception:
                status = 503
            telemetry.event("request", "failure", correlation_id=correlation)
            return problem(status, correlation)

    return router
