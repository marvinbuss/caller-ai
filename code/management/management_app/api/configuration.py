"""Explicit authenticated configuration composition; never mounted by foundation startup."""

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse

from management_app.adapters.configuration_repository import (
    ConfigurationRejected,
    Document,
    Publication,
    graph_document,
    verified_revision,
)
from management_app.api.problems import problem_response
from management_app.observability import sanitize_correlation_id
from management_app.services.audit import AuditRecord
from management_app.services.configuration_validation import SNAPSHOT_LIMIT, encoded
from management_app.services.secret_detection import Issue

ETAG = re.compile(r'^"[A-Za-z0-9_-]{1,128}"$')
ERROR_CODES = {
    401: "authentication_required",
    403: "permission_denied",
    409: "configuration_conflict",
    413: "configuration_too_large",
    422: "invalid_configuration",
    428: "precondition_required",
    503: "configuration_unavailable",
    504: "configuration_deadline_exceeded",
}


class IdentityPort(Protocol):
    async def require_manager(self, request: Request) -> str: ...


class ValidationPort(Protocol):
    def validate(self, payload: Document) -> list[Issue]: ...


class RepositoryPort(Protocol):
    async def read_active(self) -> Publication | None: ...

    async def publish(
        self, payload: Document, *, if_match: str, actor_id: str
    ) -> Publication | None: ...


class AuditPort(Protocol):
    async def recover_publication(self, committed_revision: Document) -> bool: ...

    async def record(self, record: AuditRecord) -> bool: ...


def problem(status: int, correlation: str, issues: list[Issue] | None = None) -> JSONResponse:
    safe = [
        {
            "path": issue["path"],
            "code": issue["code"],
            "rule_id": issue["rule_id"],
            "policy_version": issue["policy_version"],
        }
        for issue in (issues or [])[:64]
    ]
    allowed = {
        "credential_literal_prohibited",
        "secret_reference_invalid",
        "secret_reference_unknown",
        "invalid_configuration",
        "configuration_too_large",
        "publication_too_large",
    }
    code = next((item["code"] for item in safe if item["code"] in allowed), ERROR_CODES[status])
    return problem_response(status, correlation, code, safe)


def unique_object(pairs: list[tuple[str, Any]]) -> Document:
    document: Document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate_json_property")
        document[key] = value
    return document


async def read_graph(request: Request) -> Document:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > SNAPSHOT_LIMIT:
            raise HTTPException(413, "configuration_too_large")
    try:
        document = json.loads(body, object_pairs_hook=unique_object)
        if not isinstance(document, dict):
            raise ValueError("graph_object_required")
        encoded(document)
        return document
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "invalid_configuration") from None


def revision_response(publication: Publication, correlation: str) -> JSONResponse:
    snapshot, etag = publication
    if not ETAG.fullmatch(etag):
        raise ValueError("invalid_storage_etag")
    revision = verified_revision(snapshot)
    return JSONResponse(
        revision,
        headers={"ETag": etag, "Cache-Control": "no-store", "X-Correlation-ID": correlation},
    )


def create_router(
    *,
    repository: RepositoryPort,
    identity: IdentityPort,
    validator: ValidationPort,
    audit: AuditPort,
) -> APIRouter:
    if any(port is None for port in (repository, identity, validator, audit)):
        raise ValueError("configuration_ports_required")
    router = APIRouter()

    async def denial(correlation: str, actor: str | None, deadline: float) -> None:
        try:
            record = AuditRecord(
                id="audit-" + uuid4().hex,
                timestamp=datetime.now(UTC),
                action="authorization.check",
                outcome="denied",
                actor_ref=actor or "anonymous",
                revision_id=None,
                correlation_id=correlation,
                reason_code="permission_denied",
            )
            async with asyncio.timeout_at(min(deadline, asyncio.get_running_loop().time() + 2)):
                await audit.record(record)
        except Exception:
            return

    @router.get("/v2/configuration")
    async def read(request: Request) -> JSONResponse:
        deadline = asyncio.get_running_loop().time() + 10
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        actor = None
        try:
            async with asyncio.timeout_at(deadline):
                actor = await identity.require_manager(request)
                if request.query_params:
                    raise HTTPException(422, "unsupported_configuration_parameters")
                current = await repository.read_active()
                if current is None or validator.validate(graph_document(current[0])):
                    raise ValueError("configuration_unavailable")
                return revision_response(current, correlation)
        except HTTPException as error:
            if error.status_code in (401, 403):
                await denial(correlation, actor, deadline)
            return problem(
                error.status_code if error.status_code in ERROR_CODES else 503, correlation
            )
        except TimeoutError:
            return problem(504, correlation)
        except Exception:
            return problem(503, correlation)

    @router.put("/v2/configuration")
    async def write(request: Request) -> JSONResponse:
        deadline = asyncio.get_running_loop().time() + 10
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        actor = None
        try:
            async with asyncio.timeout_at(deadline):
                actor = await identity.require_manager(request)
                if request.query_params:
                    raise HTTPException(422, "unsupported_configuration_parameters")
                values = request.headers.getlist("if-match")
                if not values:
                    raise HTTPException(428, "precondition_required")
                if len(values) != 1 or not ETAG.fullmatch(values[0]):
                    raise HTTPException(422, "invalid_configuration_condition")
                payload = await read_graph(request)
                issues = validator.validate(payload)
                if issues:
                    raise ConfigurationRejected(issues)
                published = await repository.publish(payload, if_match=values[0], actor_id=actor)
                if published is None:
                    raise HTTPException(409, "configuration_conflict")
                response = revision_response(published, correlation)
        except ConfigurationRejected as error:
            status = (
                413
                if any(
                    issue["code"] in {"configuration_too_large", "publication_too_large"}
                    for issue in error.issues
                )
                else 422
            )
            return problem(status, correlation, error.issues)
        except HTTPException as error:
            if error.status_code in (401, 403):
                await denial(correlation, actor, deadline)
            return problem(
                error.status_code if error.status_code in ERROR_CODES else 503, correlation
            )
        except TimeoutError:
            return problem(504, correlation)
        except Exception:
            return problem(503, correlation)
        try:
            async with asyncio.timeout_at(min(deadline, asyncio.get_running_loop().time() + 2)):
                await audit.recover_publication(published[0])
            response.headers["X-Audit-Status"] = "recorded"
        except Exception:
            response.headers["X-Audit-Status"] = "pending"
        return response

    return router
