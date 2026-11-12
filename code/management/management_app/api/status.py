"""`GET /v1/status` for the browser: the runtime's status, cached for 60 seconds.

Editors and testers may read it; nobody else. `?refresh=1` skips the cache, at most once per
10 seconds per session; a faster refresh is served from the cache. When the runtime cannot be
reached the body says so and keeps the time of the last success, so the page can show both.
Each answer from the runtime (or a failure to get one) records a `status.check` event.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from management_app.observability import Telemetry
from management_app.security import IdentityService

PATH = "/v1/status"
CACHE_SECONDS = 60
REFRESH_SECONDS = 10
HEADERS = {"Cache-Control": "no-store"}


def instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def reachability(status: dict[str, Any] | None, model: str) -> str:
    if status is None:
        return "unknown"
    return "reachable" if status[model]["reachable"] else "unreachable"


def create_router(
    *,
    identity: IdentityService,
    fetch: Callable[[], Awaitable[dict[str, Any]]] | None,
    test_role: str | None,
    telemetry: Telemetry | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    on_status: Callable[[dict[str, Any]], None] | None = None,
) -> APIRouter:
    router = APIRouter()
    lock = asyncio.Lock()
    cached: dict[str, Any] = {}
    refreshed: dict[str, float] = {}
    last_success: list[str] = []

    async def check() -> dict[str, Any]:
        status: dict[str, Any] | None = None
        try:
            if fetch is None:
                raise PermissionError("runtime_status_unconfigured")
            status = await fetch()
        except asyncio.CancelledError:
            raise
        except Exception:
            status = None
        checked_at = instant(utc_now())
        runtime: dict[str, Any] = {"reachable": status is not None, "checkedAt": checked_at}
        if status is not None:
            last_success[:] = [checked_at]
            if on_status is not None:
                on_status(status)
        elif last_success:
            runtime["lastSuccessAt"] = last_success[0]
        if telemetry is not None:
            telemetry.record(
                "status.check",
                outcome="success" if status is not None else "failure",
                text=reachability(status, "text"),
                voice=reachability(status, "voice"),
            )
        return {"runtime": runtime, "status": status}

    @router.get(PATH)
    async def status(request: Request, refresh: str | None = None) -> JSONResponse:
        session = identity.browser_session(request, mutation=False)
        roles = session.principal.roles
        if identity.policy.manager_role not in roles and (
            test_role is None or test_role not in roles
        ):
            raise HTTPException(403, "permission_denied")
        now = monotonic()
        key = identity.sessions.digest(request.cookies.get(identity.cookie_name, ""))
        force = refresh == "1" and now - refreshed.get(key, -REFRESH_SECONDS) >= REFRESH_SECONDS
        if force:
            refreshed[key] = now
            for stale in [name for name, at in refreshed.items() if now - at > REFRESH_SECONDS]:
                refreshed.pop(stale, None)
        async with lock:
            if force or "body" not in cached or now - cached["at"] >= CACHE_SECONDS:
                cached["body"], cached["at"] = await check(), monotonic()
            body = cached["body"]
        return JSONResponse(body, headers=HEADERS)

    return router
