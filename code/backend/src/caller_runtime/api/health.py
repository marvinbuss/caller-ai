"""Minimal health probes, never external dependency discovery or readiness claims."""

import asyncio

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from caller_runtime import __version__

router = APIRouter()


@router.get("/healthz")
async def health(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "caller-runtime",
            "mode": request.app.state.settings.mode,
            "version": __version__,
        }
    )


@router.get("/readyz")
async def readiness(request: Request) -> JSONResponse:
    ready = request.app.state.ready
    reason = "configuration_invalid"
    if ready:
        reason = "dependency_unavailable"
        try:
            ready = bool(
                await asyncio.wait_for(request.app.state.dependencies.ready(), timeout=0.25)
            )
        except Exception:
            ready = False
        if not ready:
            request.app.state.metrics.dependency_failure("readiness", "unavailable")
        runtime = request.app.state.runtime
        if ready and runtime is not None and not runtime.history_ready():
            ready, reason = False, "history_store_unavailable"
            request.app.state.metrics.dependency_failure("history_store", "unavailable")
    return JSONResponse(
        {
            "status": "ready" if ready else "not_ready",
            "service": "caller-runtime",
            "mode": request.app.state.settings.mode,
            "version": __version__,
            "capabilities": {"live_calls": False, "management_configuration": False},
            "reason_codes": [] if ready else [reason],
        },
        status_code=200 if ready else 503,
    )
