"""The text conversation endpoint: one request is one turn, streamed as server-sent events.

The workload credential rides in `Authorization` and the signed start permit in the body, so a
failure before the first event is a plain JSON error and a later one is a final `error` event.
The caller never sends history; the backend continues the owner's stored conversation.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from fastapi import APIRouter, Request
from pydantic import Field, ValidationError
from starlette.responses import JSONResponse, Response, StreamingResponse

from caller_runtime.adapters.strict_json import unique_object
from caller_runtime.api.connection import (
    MAX_TEXT_BYTES,
    PermitVerifier,
    PublicError,
    WorkloadIdentity,
    parse_settings,
    public_error,
    public_error_for,
    verify_permit,
)
from caller_runtime.domain.conversation import Identifier, Metadata, StartPermit
from caller_runtime.services.text_conversation import TurnResult

PATH = "/v1/conversations/text"
MAX_REQUEST_BYTES = 16 * 1024
FIRST_STAGE_SECONDS = 3
MAX_IN_FLIGHT = 10
MAX_IN_FLIGHT_PER_ACTOR = 2
HEADERS = {"Cache-Control": "no-store"}
STREAM_HEADERS = HEADERS | {"X-Accel-Buffering": "no"}


class TextService(Protocol):
    async def run(
        self,
        permit: StartPermit,
        *,
        text: str,
        conversation_id: str | None,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> TurnResult: ...


class TextRequest(Metadata):
    permit: str = Field(min_length=1, max_length=6144, repr=False)
    request_id: Identifier = Field(alias="requestId")
    conversation_id: Identifier | None = Field(default=None, alias="conversationId")
    text: str = Field(min_length=1, repr=False)
    settings: Any = None


def failure(error: PublicError) -> JSONResponse:
    return JSONResponse(error.body(), status_code=error.status, headers=HEADERS)


async def read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_REQUEST_BYTES):
        raise PermissionError("control_limit")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REQUEST_BYTES:
            raise PermissionError("control_limit")
    return bytes(body)


def parse_request(body: bytes) -> TextRequest:
    try:
        document = json.loads(body, object_pairs_hook=unique_object)
        if not isinstance(document, dict):
            raise ValueError("invalid_control")
        value = TextRequest.model_validate(document)
    except (ValueError, ValidationError):
        raise PermissionError("invalid_control") from None
    if not value.text.strip():
        raise PermissionError("invalid_work_input")
    if len(value.text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise PermissionError("control_limit")
    return value


def sse(kind: str, data: dict[str, Any]) -> bytes:
    document = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return f"event: {kind}\ndata: {document}\n\n".encode()


def error_frame(error: PublicError) -> bytes:
    return sse("error", error.body()["error"])


def frame(event: dict[str, Any]) -> bytes:
    if event["type"] == "error":
        return error_frame(public_error(str(event.get("code"))))
    data = {key: value for key, value in event.items() if key not in {"type", "generation"}}
    return sse(event["type"], data)


def create_router(
    *,
    service: TextService,
    workload_identity: WorkloadIdentity,
    permits: PermitVerifier,
) -> APIRouter:
    if any(port is None for port in (service, workload_identity, permits)):
        raise ValueError("explicit_text_endpoint_ports_required")
    router = APIRouter()
    in_flight = 0
    by_actor: dict[str, int] = {}

    @router.post(PATH)
    async def text_turn(request: Request) -> Response:
        nonlocal in_flight
        if request.url.scheme != "https" or request.query_params:
            return failure(public_error("insecure_transport"))
        if in_flight >= MAX_IN_FLIGHT:
            return failure(public_error("work_busy"))
        in_flight += 1
        actor: str | None = None

        def release() -> None:
            nonlocal in_flight
            in_flight -= 1
            if actor is not None:
                remaining = by_actor.get(actor, 1) - 1
                if remaining > 0:
                    by_actor[actor] = remaining
                else:
                    by_actor.pop(actor, None)

        try:
            async with asyncio.timeout(FIRST_STAGE_SECONDS):
                principal = await workload_identity.require_runtime(request)
                if not isinstance(principal, str) or not principal.strip() or len(principal) > 128:
                    raise PermissionError("unverified_workload")
                if not request.headers.get("content-type", "").startswith("application/json"):
                    raise PermissionError("invalid_control")
                payload = parse_request(await read_body(request))
                permit = await verify_permit(permits, payload.permit)
                if permit.request_id != payload.request_id:
                    raise PermissionError("invalid_test_permit")
                if permit.mode != "text":
                    raise PermissionError("mode_mismatch")
                parse_settings(payload.settings)
                if by_actor.get(permit.actor_key, 0) >= MAX_IN_FLIGHT_PER_ACTOR:
                    raise PermissionError("work_busy")
                actor = permit.actor_key
                by_actor[actor] = by_actor.get(actor, 0) + 1
        except BaseException as error:
            release()
            if isinstance(error, asyncio.CancelledError):
                raise
            return failure(public_error_for(error))

        events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=64)
        gone = False
        outcome: list[PublicError] = []

        async def emit(event: dict[str, Any]) -> None:
            if gone:
                return
            try:
                await asyncio.wait_for(events.put(event), 1)
            except TimeoutError:
                raise PermissionError("work_busy") from None

        async def run() -> None:
            try:
                await service.run(
                    permit, text=payload.text, conversation_id=payload.conversation_id, emit=emit
                )
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                outcome.append(public_error_for(error))
            finally:
                try:
                    events.put_nowait(None)
                except asyncio.QueueFull:
                    pass

        task = asyncio.create_task(run())
        task.add_done_callback(lambda _task: release())
        first = await events.get()
        if first is None and outcome:
            return failure(outcome[0])

        async def stream() -> AsyncIterator[bytes]:
            nonlocal gone
            try:
                item = first
                while item is not None:
                    yield frame(item)
                    item = await events.get()
                if outcome:
                    yield error_frame(outcome[0])
            finally:
                gone = True

        return StreamingResponse(stream(), media_type="text/event-stream", headers=STREAM_HEADERS)

    return router
