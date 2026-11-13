"""Relays one text console session to the backend, one HTTP turn at a time.

The browser keeps a single console socket open, but the backend serves a text turn per request:
each `text.submit` becomes a `POST /v1/conversations/text` and the streamed events are relayed back
to the browser in the console's socket envelope. The relay keeps only the `conversationId` of the
open test, so the backend (not the console) holds the history. A fresh single-use permit is minted
for every turn and never leaves the server. Failures surface the backend's public error code
and reason unchanged.

Inputs: the browser socket, the permit the console ticket was issued from and a callable that
re-validates the console session. Failure behavior: a turn failure is a non-terminal `error`
message; a lost console session, an expired window or an authentication failure ends the session.
"""

import asyncio
import hashlib
import json
import math
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import WebSocket

from management_app.adapters.backend_connection import (
    TurnPermits,
    compose_request,
    correlation_for,
)
from management_app.adapters.relay_frames import RelayLimit, parse_control
from management_app.models.test_session import StartPermit, utc
from management_app.observability import Telemetry
from management_app.security import ServerSession
from management_app.services.formatted_text import to_tree

MAX_REPLY_BYTES = 64 * 1024
MAX_TEXT_BYTES = 4096
MAX_LINE_BYTES = 8192
# A `text.formatted` message carries the whole reply's tree; the browser accepts it up to this size.
MAX_FORMATTED_BYTES = 192 * 1024
MAX_QUEUED_TURNS = 2
TURN_SECONDS = 30
SESSION_SECONDS = 600
IDLE_SECONDS = 120
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
PROGRESS_STATES = frozenset(
    {"still_working", "waiting_for_input", "completed", "failed", "cancellation_confirmed"}
)


def valid_progress(state: Any, text: Any) -> bool:
    """A progress update has a known state and 1-200 characters of text."""
    return state in PROGRESS_STATES and isinstance(text, str) and 1 <= len(text) <= 200


BOUNDS = {
    "controlBytes": 8192,
    "textBytes": 4096,
    "replyBytes": 65536,
    "captureBytes": 12000,
    "playbackBytes": 24000,
    "inputFrameBytes": 1920,
}
PUBLIC_CODES = frozenset(
    {
        "unauthorized",
        "forbidden",
        "settings_not_supported",
        "limit_exceeded",
        "capacity_unavailable",
        "invalid_request",
        "upstream_unavailable",
        "internal_error",
    }
)
# Failures that mean this console session can no longer make progress.
TERMINAL_CODES = frozenset({"unauthorized", "forbidden", "settings_not_supported"})


@dataclass(frozen=True, slots=True)
class ActiveRevision:
    """The published revision the console shows when a text session opens."""

    revision_id: str
    revision_number: int
    agent_id: str
    # `conversationSettings.idleTimeoutSeconds` of that revision, or the default.
    idle_seconds: int = IDLE_SECONDS


class TurnFailure(Exception):
    """A turn ended with a backend error, carrying its public code and reason."""

    def __init__(self, code: str, reason: str | None) -> None:
        super().__init__(code)
        self.code, self.reason = code, reason


def public_failure(value: Any) -> TurnFailure:
    """Validate a backend error body or event; anything unexpected becomes `internal_error`."""
    if isinstance(value, dict) and isinstance(value.get("error"), dict):
        value = value["error"]
    if (
        isinstance(value, dict)
        and value.get("code") in PUBLIC_CODES
        and set(value) <= {"code", "reason"}
        and (
            "reason" not in value
            or (
                isinstance(value["reason"], str)
                and re.fullmatch(r"[a-z][a-z_]{0,63}", value["reason"]) is not None
            )
        )
    ):
        return TurnFailure(value["code"], value.get("reason"))
    return TurnFailure("internal_error", None)


async def events(response: httpx.Response) -> AsyncIterator[tuple[str, Any]]:
    """Yield `(event, data)` pairs of a bounded server-sent-event body."""
    kind, data = "", ""
    async for line in response.aiter_lines():
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise RelayLimit("control_limit")
        if line == "":
            if kind or data:
                yield kind, json.loads(data) if data else None
            kind, data = "", ""
        elif line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            data = line[6:]
        else:
            raise PermissionError("invalid_test_control")
    if kind or data:
        yield kind, json.loads(data) if data else None


class TextRelay:
    def __init__(
        self,
        *,
        origin: str,
        audience: str,
        credential: Any,
        permits: TurnPermits,
        active_revision: Callable[[], Awaitable[ActiveRevision]],
        utc_now: Callable[[], datetime],
        skew: float,
        transport: httpx.AsyncBaseTransport | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self._origin, self._audience, self._credential = origin, audience, credential
        self._permits, self._active_revision = permits, active_revision
        self._utc_now, self._skew = utc_now, skew
        self._transport, self._telemetry = transport, telemetry

    async def run(
        self,
        browser: WebSocket,
        *,
        permit: StartPermit,
        validate_current: Callable[[], ServerSession],
        deadline: float,
        current: Callable[[], object],
    ) -> None:
        now = utc(self._utc_now())
        revision = await self._active_revision()
        # Why: the console session window is its own; the browser requires expiry within 10 minutes.
        expires = min(
            now + timedelta(seconds=SESSION_SECONDS),
            permit.auth_expires_at - timedelta(seconds=self._skew + 1),
        )
        if expires <= now:
            raise PermissionError("test_auth_expired")
        session_id = "console-" + secrets.token_hex(8)
        correlation = correlation_for(permit.request_id)
        sequence = 0
        conversation_id: str | None = None
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=MAX_QUEUED_TURNS)
        ended = asyncio.Event()
        seen: dict[int, str] = {}
        client_sequence = 0

        async def send(kind: str, **fields: Any) -> None:
            nonlocal sequence
            sequence += 1
            message = {"type": kind, "sessionId": session_id, "sequence": sequence, "generation": 1}
            async with asyncio.timeout(1):
                await browser.send_text(json.dumps(message | fields, separators=(",", ":")))

        async def finish(code: str = "stopped", status: str = "complete") -> None:
            await send("ended", code=code, cleanupStatus=status, correlationId=correlation)
            await browser.close(code=1000, reason="stopped")
            ended.set()

        async def failure(error: TurnFailure) -> None:
            if error.code in TERMINAL_CODES:
                await browser.close(code=1008, reason=error.code)
                ended.set()
                return
            fields: dict[str, Any] = {
                "code": error.code,
                "cleanupStatus": "active",
                "correlationId": correlation,
            }
            if error.reason is not None:
                fields["reason"] = error.reason
            await send("error", **fields)

        async def formatted(message_id: str, text: str) -> None:
            """Send the reply's allowlisted tree once; without one the plain-text row stays."""
            tree = to_tree(text) if text.strip() else None
            if tree is None or (
                # A single plain paragraph looks the same as the plain-text row already shown.
                len(tree["children"]) == 1
                and isinstance(tree["children"][0], dict)
                and tree["children"][0]["type"] == "p"
                and all(isinstance(child, str) for child in tree["children"][0]["children"])
            ):
                return
            size = len(json.dumps(tree, separators=(",", ":")).encode("utf-8"))
            if size > MAX_FORMATTED_BYTES - 256:
                return
            await send("text.formatted", messageId=message_id, tree=tree)

        async def turn(text: str | None) -> None:
            nonlocal conversation_id
            current()
            token = await self._credential.get_token(f"api://{self._audience}/.default")
            if not token.token or not math.isfinite(token.expires_on):
                raise PermissionError("test_workload_expired")
            claims, signed = self._permits.mint(permit)
            request = compose_request(
                "text",
                origin=self._origin,
                token=token.token,
                signed_permit=signed,
                request_id=claims.request_id,
                text=text,
                conversation_id=conversation_id,
                correlation_id=correlation,
            )
            del token, signed
            async with httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(TURN_SECONDS, connect=10),
            ) as client:
                async with client.stream(
                    "POST",
                    request.url,
                    headers=request.headers,
                    content=json.dumps(request.message, separators=(",", ":")),
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        try:
                            raise public_failure(json.loads(body[:2048]))
                        except ValueError:
                            raise TurnFailure("internal_error", None) from None
                    reply = 0
                    texts: dict[str, list[str]] = {}
                    async for kind, data in events(response):
                        current()
                        if kind == "conversation":
                            if (
                                not isinstance(data, dict)
                                or set(data) != {"conversationId"}
                                or not isinstance(data["conversationId"], str)
                                or IDENTIFIER.fullmatch(data["conversationId"]) is None
                            ):
                                raise PermissionError("invalid_test_control")
                            conversation_id = data["conversationId"]
                        elif kind in {"text.delta", "text.completed"}:
                            if (
                                not isinstance(data, dict)
                                or set(data) != {"messageId", "text"}
                                or not isinstance(data["messageId"], str)
                                or IDENTIFIER.fullmatch(data["messageId"]) is None
                                or not isinstance(data["text"], str)
                                or len(data["text"].encode("utf-8")) > MAX_TEXT_BYTES
                            ):
                                raise PermissionError("invalid_test_control")
                            reply += len(data["text"].encode("utf-8"))
                            if reply > MAX_REPLY_BYTES:
                                raise PermissionError("test_reply_limit")
                            await send(kind, **data)
                            texts.setdefault(data["messageId"], []).append(data["text"])
                            if kind == "text.completed":
                                await formatted(
                                    data["messageId"], "".join(texts.pop(data["messageId"]))
                                )
                        elif kind == "agent.changed":
                            if not isinstance(data, dict) or set(data) != {
                                "agentId",
                                "communityId",
                            }:
                                raise PermissionError("invalid_test_control")
                            await send(kind, **data)
                        elif kind == "progress":
                            if (
                                not isinstance(data, dict)
                                or set(data) != {"messageId", "state", "text"}
                                or not isinstance(data["messageId"], str)
                                or IDENTIFIER.fullmatch(data["messageId"]) is None
                                or not valid_progress(data["state"], data["text"])
                            ):
                                raise PermissionError("invalid_test_control")
                            await send(kind, **data)
                        elif kind == "error":
                            raise public_failure(data)
                        else:
                            raise PermissionError("invalid_test_control")

        async def attempt(text: str | None) -> None:
            try:
                async with asyncio.timeout(TURN_SECONDS + 5):
                    await turn(text)
            except TurnFailure as error:
                await failure(error)
            except (TimeoutError, httpx.HTTPError):
                await failure(TurnFailure("upstream_unavailable", "runtime_unavailable"))

        async def worker() -> None:
            # The open request comes first, so the greeting precedes any early user message,
            # which waits in the queue and is answered right after it.
            await attempt(None)
            while not ended.is_set():
                text = await queue.get()
                if text is None:
                    return
                await attempt(text)

        async def reader() -> None:
            nonlocal client_sequence
            while True:
                message = await browser.receive()
                if message["type"] == "websocket.disconnect":
                    return
                raw = message.get("text")
                if not isinstance(raw, str):
                    raise PermissionError("invalid_test_control")
                value = parse_control(raw)
                fields = {
                    "text.submit": {"text"},
                    "stop": set(),
                    "ping": {"nonce"},
                    "pong": {"nonce"},
                }
                kind = value.get("type")
                if (
                    kind not in fields
                    or set(value) != {"type", "sessionId", "sequence"} | fields[kind]
                    or value["sessionId"] != session_id
                    or type(value["sequence"]) is not int
                    or not 1 <= value["sequence"] <= 2**32 - 1
                ):
                    raise PermissionError("invalid_test_control")
                digest = hashlib.sha256(
                    json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if value["sequence"] in seen:
                    if seen[value["sequence"]] != digest:
                        raise PermissionError("invalid_test_sequence")
                    continue
                if value["sequence"] != client_sequence + 1:
                    raise PermissionError("invalid_test_sequence")
                client_sequence = value["sequence"]
                seen[client_sequence] = digest
                if len(seen) > 64:
                    seen.pop(next(iter(seen)))
                current()
                if kind == "text.submit":
                    text = value["text"]
                    if (
                        not isinstance(text, str)
                        or not text.strip()
                        or len(text.encode("utf-8")) > MAX_TEXT_BYTES
                    ):
                        raise PermissionError("invalid_test_control")
                    try:
                        queue.put_nowait(text)
                    except asyncio.QueueFull:
                        await failure(TurnFailure("capacity_unavailable", "work_busy"))
                elif kind == "ping":
                    nonce = value["nonce"]
                    if (
                        not isinstance(nonce, str)
                        or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", nonce) is None
                    ):
                        raise PermissionError("invalid_test_control")
                    await send("pong", nonce=nonce)
                elif kind == "stop":
                    return

        async def watch() -> None:
            while not ended.is_set():
                current()
                if utc(self._utc_now()) + timedelta(seconds=self._skew) >= expires:
                    raise PermissionError("test_auth_expired")
                try:
                    async with asyncio.timeout(0.9):
                        await ended.wait()
                except TimeoutError:
                    continue

        async with asyncio.timeout_at(deadline):
            await send(
                "ready",
                mode="text",
                revisionId=revision.revision_id,
                revisionNumber=revision.revision_number,
                agentId=revision.agent_id,
                expiresAt=expires.isoformat(),
                idleExpiresAt=min(
                    expires, now + timedelta(seconds=revision.idle_seconds)
                ).isoformat(),
                bounds=BOUNDS,
            )
        if self._telemetry is not None:
            self._telemetry.event(
                "test.relay", "success", mode="text", reason="ready", correlation_id=correlation
            )
        worker_task = asyncio.create_task(worker())
        tasks = {worker_task, asyncio.create_task(watch()), asyncio.create_task(reader())}
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if not ended.is_set():
                # The browser asked to stop: let an in-flight turn finish before the session ends,
                # so the backend slot is released before the console reports a clean end.
                async with asyncio.timeout(TURN_SECONDS):
                    await queue.put(None)
                    await worker_task
                await finish()
        finally:
            ended.set()
            for task in tasks:
                task.cancel()
            # Not gather: it re-raises a child's bare CancelledError, which hides a server cancel.
            await asyncio.wait(tasks)
