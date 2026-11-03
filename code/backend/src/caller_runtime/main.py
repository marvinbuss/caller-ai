"""Credential-free FastAPI composition root; no live adapters are registered."""

import time
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from caller_runtime.adapters.agents_sdk import initialize_sdk
from caller_runtime.api.dev_transport import router as dev_router
from caller_runtime.api.health import router as health_router
from caller_runtime.composition import TestRuntimeComposition
from caller_runtime.domain.ports import LocalDependencies, ReadinessPort
from caller_runtime.metrics import METHODS, RuntimeMetrics
from caller_runtime.observability import Telemetry, sanitize_correlation_id
from caller_runtime.services.configuration_activation import ConfigurationActivation
from caller_runtime.settings import Settings


def create_app(
    settings: Settings | None = None,
    *,
    probe_ack: Callable[[str], dict[str, str | int]] | None = None,
    dependencies: ReadinessPort | None = None,
    metrics: RuntimeMetrics | None = None,
    configuration_activation: ConfigurationActivation | None = None,
    test_runtime: TestRuntimeComposition | None = None,
    test_alert_sink: Callable[[dict[str, str]], None] | None = None,
) -> FastAPI:
    configuration = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configuration.validate_startup()
        telemetry = Telemetry("caller-runtime", configuration.mode, alert_sink=test_alert_sink)
        application.state.telemetry = telemetry
        metrics_port = metrics if metrics is not None else RuntimeMetrics(mode=configuration.mode)
        application.state.metrics = metrics_port
        scope = AsyncExitStack()
        try:
            initialize_sdk(configuration, telemetry)
            if configuration_activation is not None:
                await scope.enter_async_context(configuration_activation.running())
            if test_runtime is not None:
                await scope.enter_async_context(test_runtime.running(telemetry))
            application.state.ready = True
            telemetry.event("startup", "success")
            yield
        finally:
            application.state.ready = False
            try:
                await scope.aclose()
            finally:
                telemetry.event("shutdown", "success")
                telemetry.close()
                metrics_port.close()

    application = FastAPI(
        title="Caller AI Runtime", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    application.state.settings = configuration
    application.state.dependencies = (
        dependencies
        if dependencies is not None
        else LocalDependencies()
        if configuration.mode == "local"
        else None
    )
    application.state.ready = False
    application.state.active_probes = 0
    application.state.probe_ack = probe_ack
    application.state.configuration_activation = configuration_activation
    application.state.test_runtime = test_runtime
    application.include_router(health_router)
    if configuration.mode == "local":
        application.include_router(dev_router)
    if test_runtime is not None:
        application.include_router(test_runtime.router())

    @application.middleware("http")
    async def metadata(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        correlation = sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
        path = request.url.path
        route = path if path in {"/healthz", "/readyz"} else "unmatched"
        telemetry: Telemetry = application.state.telemetry
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
        response.headers["X-Caller-Instance"] = configuration.instance_id
        metrics_port: RuntimeMetrics = application.state.metrics
        metrics_port.request(
            route=metrics_port.route_for(path),
            method=request.method if request.method in METHODS else "OTHER",
            status=response.status_code,
            seconds=min(3600, time.perf_counter() - started),
        )
        return response

    return application


app = create_app()
