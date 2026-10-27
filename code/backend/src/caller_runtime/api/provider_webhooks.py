"""Provider-neutral incoming-call admission; never mounted without explicit composition."""

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse


class ProviderPort(Protocol):
    async def verify_callback(
        self, headers: Mapping[str, str], body: bytes
    ) -> Mapping[str, str]: ...
    def supports_codec(self, codec: str) -> bool: ...
    async def answer(self, provider_call_ref: str) -> None: ...


class AdmissionPort(Protocol):
    async def admit(self, event: Mapping[str, str]) -> bool: ...
    async def read(self, session_id: str) -> Mapping[str, Any] | None: ...
    async def end(self, session_id: str) -> None: ...


def create_router(*, provider: ProviderPort, admission: AdmissionPort) -> APIRouter:
    """Require server-injected verified provider/admission ports; no credential discovery."""
    router = APIRouter()

    @router.post("/v1/provider/callbacks")
    async def callback(request: Request) -> JSONResponse:
        created_session: str | None = None
        try:
            async with asyncio.timeout(9.75):
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 65536:
                        return JSONResponse({"error": "callback_too_large"}, status_code=413)
                    body.extend(chunk)
                event = await provider.verify_callback(request.headers, bytes(body))
                required = {"event_id", "session_id", "provider_call_ref", "codec", "type"}
                if (
                    set(event) != required
                    or event["type"] != "incoming"
                    or any(
                        not isinstance(value, str) or not value.strip() or len(value) > 256
                        for value in event.values()
                    )
                ):
                    raise ValueError("invalid_provider_event")
                if not provider.supports_codec(event["codec"]):
                    return JSONResponse({"error": "unsupported_codec"}, status_code=415)
                current = await admission.read(event["session_id"])
                if current is not None:
                    if (
                        current.get("provider_call_ref") != event["provider_call_ref"]
                        or current.get("codec") != event["codec"]
                    ):
                        raise PermissionError("provider_call_binding_invalid")
                    if str(current.get("state", "")).lower() == "ended":
                        return JSONResponse({"status": "duplicate"}, status_code=202)
                if not await admission.admit(event):
                    return JSONResponse({"status": "duplicate"}, status_code=202)
                created_session = event["session_id"]
                await provider.answer(event["provider_call_ref"])
                return JSONResponse({"status": "accepted"}, status_code=202)
        except PermissionError:
            return JSONResponse({"error": "provider_identity_or_binding_invalid"}, status_code=403)
        except ValueError:
            return JSONResponse({"error": "invalid_provider_event"}, status_code=400)
        except Exception:
            if created_session is not None:
                try:
                    async with asyncio.timeout(0.25):
                        await admission.end(created_session)
                except Exception:
                    pass
            return JSONResponse({"error": "provider_admission_unavailable"}, status_code=503)

    return router
