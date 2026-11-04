"""Management health API: `/healthz` liveness and `/readyz` readiness, mounted through Reflex.

Liveness never calls a dependency. Readiness reports 503 with one fixed reason
(`configuration_invalid` before startup finished, `dependency_unavailable` for a failed or slow
check) and the lifespan hook validates the mode and owns shutdown. Responses carry no dependency
detail, and the middleware records only approved route categories.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from management_app.metrics import METHODS, ManagementMetrics
from management_app.observability import Telemetry, sanitize_correlation_id
from management_app.services.ports import LocalDependencies, ReadinessPort
from management_app.settings import Settings


class ManagementStartup(Protocol):
    async def require(self, settings: Settings) -> None: ...

    async def shutdown(self) -> None: ...


def create_health_api(
    settings: Settings,
    dependencies: ReadinessPort | None = None,
    *,
    metrics: ManagementMetrics | None = None,
) -> FastAPI:
    api = FastAPI(title="Caller AI Management", docs_url=None, redoc_url=None)
    api.state.ready = False
    api.state.metrics = metrics
    dependency_port = (
        dependencies
        if dependencies is not None
        else LocalDependencies()
        if settings.mode == "local"
        else None
    )

    @api.get("/healthz")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "management",
                "mode": settings.mode,
                "version": "0.1.0",
            }
        )

    @api.get("/readyz")
    async def readiness() -> JSONResponse:
        ready = api.state.ready
        reason = "configuration_invalid"
        if ready:
            reason = "dependency_unavailable"
            try:
                ready = dependency_port is not None and await asyncio.wait_for(
                    dependency_port.ready(),
                    timeout=0.25,
                )
            except Exception:
                ready = False
            if not ready and api.state.metrics is not None:
                api.state.metrics.dependency_failure("readiness", "unavailable")
        return JSONResponse(
            {
                "status": "ready" if ready else "not_ready",
                "service": "management",
                "mode": settings.mode,
                "version": "0.1.0",
                "capabilities": {
                    "live_calls": False,
                    "management_configuration": bool(
                        ready and getattr(api.state, "management_configuration", False)
                    ),
                },
                "reason_codes": [] if ready else [reason],
            },
            status_code=200 if ready else 503,
        )

    @api.middleware("http")
    async def metadata(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        path = request.url.path
        route = path if path in {"/healthz", "/readyz"} else "unmatched"
        telemetry: Telemetry = api.state.telemetry
        with telemetry.span("http.request", {"http.route": route}, headers=request.headers):
            try:
                response = await call_next(request)
            except Exception:
                response = JSONResponse({"error": "internal_error"}, status_code=500)
            telemetry.event(
                "request",
                "failure" if response.status_code >= 500 else "success",
                correlation_id=correlation,
            )
        response.headers["X-Correlation-ID"] = correlation
        response.headers["X-Caller-Instance"] = settings.instance_id
        metrics_port: ManagementMetrics | None = api.state.metrics
        if metrics_port is not None:
            metrics_port.request(
                route=metrics_port.route_for(path),
                method=request.method if request.method in METHODS else "OTHER",
                status=response.status_code,
                seconds=min(3600, time.perf_counter() - started),
            )
        return response

    return api


@asynccontextmanager
async def lifecycle(api: FastAPI, settings: Settings) -> AsyncIterator[None]:
    startup: ManagementStartup | None = getattr(api.state, "management_startup", None)
    try:
        if startup is None:
            settings.validate_startup()
        else:
            await startup.require(settings)
    except BaseException:
        try:
            if startup is not None:
                await startup.shutdown()
        finally:
            telemetry_on_failure: Telemetry | None = getattr(api.state, "telemetry", None)
            if telemetry_on_failure is not None:
                telemetry_on_failure.close()
            metrics_on_failure: ManagementMetrics | None = api.state.metrics
            if metrics_on_failure is not None:
                metrics_on_failure.close()
        raise
    telemetry: Telemetry = getattr(api.state, "telemetry", None) or Telemetry(
        "management", settings.mode
    )
    api.state.telemetry = telemetry
    metrics: ManagementMetrics = api.state.metrics or ManagementMetrics(mode=settings.mode)
    api.state.metrics = metrics
    try:
        api.state.ready = True
        telemetry.event("startup", "success")
        yield
    finally:
        api.state.ready = False
        telemetry.event("shutdown", "success")
        telemetry.close()
        metrics.close()
        api.state.metrics = None
        if startup is not None:
            await startup.shutdown()
