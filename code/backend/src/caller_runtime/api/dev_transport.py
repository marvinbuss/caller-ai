"""Local-only bounded synthetic probe transport; never accepts audio or model work."""

from typing import Literal

from fastapi import APIRouter, WebSocket
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from caller_runtime.observability import Telemetry

router = APIRouter()
MAX_BYTES = 64 * 1024


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["probe"]
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def acknowledgment(request_id: str) -> dict[str, str | int]:
    return {"type": "probe_ack", "request_id": request_id, "protocol_version": 1}


@router.websocket("/v1/dev/transport")
async def transport(websocket: WebSocket) -> None:
    await websocket.accept()
    application = websocket.app
    application.state.active_probes += 1
    telemetry: Telemetry = application.state.telemetry
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            raw = message.get("text")
            if raw is None:
                await websocket.close(code=1008, reason="invalid_probe")
                return
            if len(raw.encode("utf-8")) > MAX_BYTES:
                await websocket.close(code=1009, reason="message_too_large")
                return
            try:
                probe = Probe.model_validate_json(raw)
            except ValidationError:
                await websocket.close(code=1008, reason="invalid_probe")
                return
            with telemetry.span("probe.receive"):
                factory = application.state.probe_ack or acknowledgment
                await websocket.send_json(factory(probe.request_id))
                telemetry.event("probe", "success")
    except WebSocketDisconnect:
        pass
    except Exception:
        telemetry.event("failure", "failure")
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011, reason="internal_error")
    finally:
        application.state.active_probes -= 1
