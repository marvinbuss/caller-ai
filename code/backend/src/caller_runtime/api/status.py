"""`GET /v1/status`: model reachability, supported voices and conversation limits for management.

The caller is the management workload only, verified the same way as the conversation endpoints.
The body names hosts, the text deployment, the region and the voice model, never a key, token or
full URL. Reachability comes from `StatusProbe`, so repeated requests do not hit the models.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from caller_runtime.adapters.status_probe import StatusProbe
from caller_runtime.api.connection import WorkloadIdentity, public_error, public_error_for
from caller_runtime.domain.configuration import IDLE_SECONDS, MAX_OUTPUT_TOKENS
from caller_runtime.domain.voices import CATALOG

PATH = "/v1/status"
HEADERS = {"Cache-Control": "no-store"}
LIVE_MODEL = "gpt-live-1"
AUTH_SECONDS = 5


@dataclass(frozen=True, slots=True)
class StatusBindings:
    text_host: str
    text_deployment: str
    live_host: str
    live_region: str
    max_output_tokens: int
    voice: str


async def status_body(probe: StatusProbe, bindings: StatusBindings) -> dict[str, Any]:
    (text_ok, text_at), (voice_ok, voice_at) = await asyncio.gather(
        probe.check("text"), probe.check("voice")
    )
    text_outcome, text_call = probe.last_call("text")
    voice_outcome, voice_call = probe.last_call("voice")
    return {
        "text": {
            "endpointHost": bindings.text_host,
            "deployment": bindings.text_deployment,
            "reachable": text_ok,
            "checkedAt": text_at,
            "lastCallOutcome": text_outcome,
            "lastCallAt": text_call,
        },
        "voice": {
            "endpointHost": bindings.live_host,
            "region": bindings.live_region,
            "model": LIVE_MODEL,
            "reachable": voice_ok,
            "checkedAt": voice_at,
            "lastCallOutcome": voice_outcome,
            "lastCallAt": voice_call,
        },
        "supportedVoices": [
            {
                "id": voice.id,
                "name": voice.name,
                "language": voice.language,
                "accent": voice.accent,
                "presentation": voice.presentation,
            }
            for voice in CATALOG
        ],
        "defaults": {
            "voice": bindings.voice,
            "maxOutputTokens": bindings.max_output_tokens,
            "idleTimeoutSeconds": IDLE_SECONDS[1],
        },
        "limits": {
            "maxOutputTokens": min(bindings.max_output_tokens, MAX_OUTPUT_TOKENS),
            "idleTimeoutSeconds": {"min": IDLE_SECONDS[0], "max": IDLE_SECONDS[1]},
        },
    }


def create_router(
    *,
    workload_identity: WorkloadIdentity,
    probe: Callable[[], StatusProbe],
    bindings: StatusBindings,
    body: Callable[[StatusProbe, StatusBindings], Awaitable[dict[str, Any]]] = status_body,
) -> APIRouter:
    if workload_identity is None or probe is None or bindings is None:
        raise ValueError("explicit_status_endpoint_ports_required")
    router = APIRouter()

    @router.get(PATH)
    async def status(request: Request) -> JSONResponse:
        try:
            if request.url.scheme != "https":
                raise PermissionError("insecure_transport")
            async with asyncio.timeout(AUTH_SECONDS):
                principal = await workload_identity.require_runtime(request)
            if not isinstance(principal, str) or not principal.strip() or len(principal) > 128:
                raise PermissionError("unverified_workload")
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            failure = public_error_for(error)
            if failure.code not in {"unauthorized", "forbidden"}:
                failure = public_error("unverified_workload")
            return JSONResponse(failure.body(), status_code=failure.status, headers=HEADERS)
        try:
            return JSONResponse(await body(probe(), bindings), headers=HEADERS)
        except asyncio.CancelledError:
            raise
        except Exception:
            return JSONResponse(
                {"error": {"code": "status_unavailable"}}, status_code=503, headers=HEADERS
            )

    return router
