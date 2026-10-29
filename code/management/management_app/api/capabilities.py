"""Explicit capability API composition; never mounted by foundation startup."""

import asyncio
import json
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, Literal, Protocol
from uuid import uuid4

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
from management_app.services.audit import AuditRecord, validate_retention
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

    async def read_revocation_audit(self, identifier: str) -> dict[str, Any]: ...

    async def acknowledge_revocation_audit(
        self, identifier: str, *, retention_seconds: int
    ) -> None: ...


class SecurityAuditPort(Protocol):
    async def record(self, record: AuditRecord) -> bool: ...

    async def recover_security(self, committed_record: dict[str, Any]) -> bool: ...


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
    audit: SecurityAuditPort,
    audit_retention_seconds: int,
) -> APIRouter:
    validate_retention(audit_retention_seconds)
    if any(
        port is None
        for port in (gateway, repository, runtime_identity, manager_identity, telemetry, audit)
    ):
        raise ValueError("explicit_capability_ports_required")
    router = APIRouter()

    async def audited_denial(
        status: int,
        correlation: str,
        actor: str | None,
        deadline: float,
        revision_id: str | None = None,
        action: Literal["capability.denied", "security.revoke"] = "capability.denied",
    ) -> JSONResponse:
        response = problem(status, correlation)
        if status not in {401, 403, 412, 413, 422, 428}:
            return response
        try:
            async with asyncio.timeout_at(deadline):
                await audit.record(
                    AuditRecord(
                        id="audit-" + uuid4().hex,
                        timestamp=datetime.now(UTC),
                        action=action if actor is not None else "authorization.check",
                        outcome="denied",
                        actor_ref=actor if actor is not None else "anonymous",
                        revision_id=revision_id,
                        correlation_id=correlation,
                        reason_code="permission_denied"
                        if status in {401, 403}
                        else "request_rejected",
                    )
                )
            response.headers["X-Audit-Status"] = "recorded"
        except Exception:
            response.headers["X-Audit-Status"] = "pending"
        return response

    @router.post("/v1/runtime/capabilities/execute")
    async def execute(request: Request) -> JSONResponse:
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        deadline = asyncio.get_running_loop().time() + 10
        actor = None
        verified_revision = None
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
                verified_revision = error.revision_id
            except HTTPException as error:
                status = error.status_code if error.status_code in ERROR_CODES else 503
            except TimeoutError:
                status = 504
            except Exception:
                status = 503
            telemetry.event("request", "failure", correlation_id=correlation)
            return await audited_denial(status, correlation, actor, deadline, verified_revision)

    @router.put("/v1/security/revocations")
    async def revoke(request: Request) -> JSONResponse:
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        deadline = asyncio.get_running_loop().time() + 10
        actor = None
        with telemetry.span("http.request", headers=request.headers):
            try:
                async with asyncio.timeout_at(deadline - 2):
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
                    if type(result.get("policyVersion")) is not int or result["policyVersion"] < 2:
                        raise RuntimeError("invalid_authority_response")
                audit_status = "pending"
                try:
                    async with asyncio.timeout_at(deadline):
                        record = await repository.read_revocation_audit(
                            "audit-security-" + str(result["policyVersion"])
                        )
                        await audit.recover_security(record)
                        await repository.acknowledge_revocation_audit(
                            record["id"], retention_seconds=audit_retention_seconds
                        )
                    audit_status = "recorded"
                except Exception:
                    pass
                telemetry.event("request", "success", correlation_id=correlation)
                return JSONResponse(
                    {"policyVersion": result["policyVersion"]},
                    headers={
                        "ETag": result["_etag"],
                        "Cache-Control": "no-store",
                        "X-Correlation-ID": correlation,
                        "X-Audit-Status": audit_status,
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
            return await audited_denial(
                status, correlation, actor, deadline, action="security.revoke"
            )

    return router
