"""Explicit provider-neutral media boundary; default app and live calling remain disabled."""

import asyncio
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import APIRouter, WebSocket
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState

from caller_runtime.api.provider_webhooks import AdmissionPort
from caller_runtime.domain.cleanup import terminal_cleanup
from caller_runtime.metrics import PhysicalCallObservations


class MediaProviderPort(Protocol):
    async def verify_media(self, headers: Mapping[str, str], session_id: str) -> str: ...
    def supports_codec(self, codec: str) -> bool: ...
    async def decode_frame(self, frame: str | bytes) -> Mapping[str, Any]: ...
    async def hangup(self, provider_call_ref: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class MediaOutput:
    payload: str | bytes = field(repr=False)
    physical_owner_id: str
    connection_epoch: int
    agent_epoch: int
    duration_ms: float = 200


class AudioPort(Protocol):
    async def dispose(self, session_id: str, *, deadline: float) -> None:
        """Cancel owned work and purge its volatile context before returning."""
        ...

    async def receive(
        self, session_id: str, audio: bytes, metadata: Mapping[str, Any]
    ) -> tuple[MediaOutput, ...]:
        """Validate owner/epochs and process one bounded volatile frame without persistence."""
        ...


def create_router(
    *,
    provider: MediaProviderPort,
    admission: AdmissionPort,
    audio: AudioPort | None = None,
    maximum_connections: int = 1024,
    operation_budget: float = 10,
    observations: PhysicalCallObservations | None = None,
) -> APIRouter:
    """No audio port means explicit local transport validation only, not a working call agent."""
    if (
        provider is None
        or admission is None
        or (observations is not None and audio is None)
        or type(maximum_connections) is not int
        or not 1 <= maximum_connections <= 10000
        or type(operation_budget) not in (int, float)
        or not math.isfinite(operation_budget)
        or not 0 < operation_budget <= 10
    ):
        raise ValueError("explicit_media_ports_required")
    router = APIRouter()
    owned: set[str] = set()
    lock = asyncio.Lock()

    @router.websocket("/v1/calls/{session_id}/media")
    async def media(websocket: WebSocket, session_id: str) -> None:
        registered = False
        accepted = False
        observed = False
        provider_call_ref = ""
        code = 1011
        reason = "media_unavailable"
        packet_deadline: float | None = None
        try:
            async with asyncio.timeout(operation_budget):
                if websocket.query_params or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", session_id
                ):
                    raise PermissionError("media_binding_invalid")
                provider_call_ref = await provider.verify_media(websocket.headers, session_id)
                async with lock:
                    current = await admission.read(session_id)
                    if (
                        current is None
                        or current.get("session_id") != session_id
                        or current.get("provider_call_ref") != provider_call_ref
                        or str(current.get("state", "")).lower() == "ended"
                        or not isinstance(current.get("codec"), str)
                        or not provider.supports_codec(current["codec"])
                        or session_id in owned
                        or len(owned) >= maximum_connections
                    ):
                        raise PermissionError("media_binding_invalid")
                    metadata = dict(current)
                    owned.add(session_id)
                    registered = True
                await websocket.accept()
                accepted = True
                if observations is not None:
                    observations.opened(
                        session_id, metadata["physical_owner_id"], metadata["connection_epoch"]
                    )
                    observed = True
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                packet_deadline = asyncio.get_running_loop().time() + operation_budget
                raw = (
                    message.get("bytes")
                    if message.get("bytes") is not None
                    else message.get("text")
                )
                if not isinstance(raw, (str, bytes)):
                    code, reason = 1008, "invalid_media_frame"
                    break
                if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > 65536:
                    code, reason = 1009, "media_frame_too_large"
                    break
                try:
                    async with asyncio.timeout_at(
                        packet_deadline - min(0.25, operation_budget / 4)
                    ):
                        frame = await provider.decode_frame(raw)
                        if frame.get("type") == "stop" and set(frame) == {"type"}:
                            code, reason = 1000, "completed"
                            break
                        if frame.get("type") != "audio" or not isinstance(
                            frame.get("audio"), bytes
                        ):
                            raise ValueError("invalid_media_frame")
                        if len(frame["audio"]) > 65536:
                            raise ValueError("decoded_audio_too_large")
                        if audio is not None:
                            duration = frame.get("duration_ms")
                            if (
                                duration is None
                                or type(duration) not in (int, float)
                                or not 0 < duration <= 200
                            ):
                                raise ValueError("invalid_audio_duration")
                            latest = await admission.read(session_id)
                            fixed = (
                                "session_id",
                                "provider_call_ref",
                                "codec",
                                "physical_owner_id",
                                "connection_epoch",
                            )
                            if (
                                latest is None
                                or any(latest.get(key) != metadata.get(key) for key in fixed)
                                or str(latest.get("state", "")).lower() == "ended"
                                or not isinstance(latest.get("physical_owner_id"), str)
                                or type(latest.get("connection_epoch")) is not int
                                or type(latest.get("agent_epoch")) is not int
                            ):
                                raise PermissionError("obsolete_media_binding")
                            outputs = await audio.receive(session_id, frame["audio"], dict(latest))
                            if not isinstance(outputs, tuple) or len(outputs) > 1:
                                raise ValueError("media_output_buffer_exceeded")
                            for output in outputs:
                                current = await admission.read(session_id)
                                if (
                                    not isinstance(output, MediaOutput)
                                    or current is None
                                    or any(current.get(key) != metadata.get(key) for key in fixed)
                                ):
                                    raise PermissionError("obsolete_media_binding")
                                if (
                                    str(current.get("state", "")).lower() == "ended"
                                    or output.physical_owner_id != current.get("physical_owner_id")
                                    or type(output.connection_epoch) is not int
                                    or output.connection_epoch != current.get("connection_epoch")
                                    or type(output.agent_epoch) is not int
                                    or output.agent_epoch != current.get("agent_epoch")
                                ):
                                    continue
                                if (
                                    type(output.duration_ms) not in (int, float)
                                    or not 0 < output.duration_ms <= 200
                                    or not isinstance(output.payload, (str, bytes))
                                    or len(
                                        output.payload.encode("utf-8")
                                        if isinstance(output.payload, str)
                                        else output.payload
                                    )
                                    > 65536
                                ):
                                    raise ValueError("invalid_media_output")
                                if isinstance(output.payload, bytes):
                                    await websocket.send_bytes(output.payload)
                                else:
                                    await websocket.send_text(output.payload)
                except (ValueError, PermissionError):
                    code, reason = 1008, "media_policy_rejected"
                    break
                packet_deadline = None
        except (PermissionError, ValueError):
            if not accepted:
                await websocket.send_denial_response(
                    JSONResponse(
                        {"error": "media_not_authorized"},
                        status_code=403,
                        headers={"Cache-Control": "no-store"},
                    )
                )
                return
            code, reason = 1008, "media_policy_rejected"
        except WebSocketDisconnect:
            pass
        except Exception:
            if not accepted:
                await websocket.send_denial_response(
                    JSONResponse(
                        {"error": "media_unavailable"},
                        status_code=503,
                        headers={"Cache-Control": "no-store"},
                    )
                )
                return
        finally:
            if accepted:
                deadline = asyncio.get_running_loop().time() + min(5, operation_budget)
                if packet_deadline is not None:
                    deadline = min(deadline, packet_deadline)
                disposed = audio is None
                if audio is not None:
                    try:
                        remaining = max(0, deadline - asyncio.get_running_loop().time())
                        async with asyncio.timeout_at(deadline - min(0.05, remaining / 2)):
                            await audio.dispose(session_id, deadline=deadline)
                            disposed = True
                    except Exception:
                        pass

                async def hangup() -> bool:
                    return await provider.hangup(provider_call_ref)

                async def end(acknowledged: bool) -> None:
                    await admission.end(session_id)

                result = await terminal_cleanup(
                    context=None, hangup=hangup, end=end, deadline=deadline
                )
                if observed and observations is not None and result.hangup_acknowledged:
                    observations.closed(
                        session_id, metadata["physical_owner_id"], metadata["connection_epoch"]
                    )
                if code == 1000 and not (
                    disposed and result.hangup_acknowledged and result.terminal_confirmed
                ):
                    code, reason = 1011, "phone_closure_unconfirmed"
                if (
                    websocket.client_state != WebSocketState.DISCONNECTED
                    and websocket.application_state == WebSocketState.CONNECTED
                ):
                    try:
                        await websocket.close(code=code, reason=reason)
                    except (RuntimeError, WebSocketDisconnect):
                        pass
            if registered:
                owned.discard(session_id)

    return router
