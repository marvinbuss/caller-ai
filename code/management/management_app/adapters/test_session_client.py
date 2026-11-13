"""Bounded relay between the browser console socket and the backend conversation endpoints.

Voice is relayed frame by frame to `WS /v1/conversations/voice`; text is relayed turn by turn
through `text_relay.py`. The relay executes no policy and no model and discovers no credential:
it forwards what the browser sends after checking sequence, size and generation bounds, and it
ends the session as soon as the console session or the permit window is no longer valid.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
import ssl
import struct
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from azure.core.credentials import AccessToken
from fastapi import HTTPException, WebSocket
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from management_app.adapters.backend_connection import (
    TurnPermits,
    compose_request,
    correlation_for,
)
from management_app.adapters.backend_errors import BACKEND_CLOSE_CODES, PUBLIC_CODES, BackendRefusal
from management_app.adapters.relay_frames import RelayFrames, RelayLimit, parse_control
from management_app.adapters.text_relay import ActiveRevision, TextRelay, valid_progress
from management_app.models.test_session import ConsoleSnapshot, StartPermit, utc
from management_app.observability import Telemetry
from management_app.security import ServerSession


class WorkloadCredential(Protocol):
    async def get_token(self, *scopes: str) -> AccessToken: ...


class RuntimeSocket(Protocol):
    async def send(self, data: str | bytes) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


class NoRedirectConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception | str:
        return exc


async def direct_socket(url: str, **options: Any) -> RuntimeSocket:
    return cast(RuntimeSocket, await NoRedirectConnect(url, **options))


class TestSessionClient:
    __test__ = False

    def __init__(
        self,
        *,
        endpoint: str,
        audience: str,
        approved_hosts: frozenset[str],
        credential: WorkloadCredential,
        utc_now: Callable[[], datetime],
        monotonic: Callable[[], float],
        max_clock_skew_seconds: float,
        socket_factory: Callable[..., Awaitable[RuntimeSocket]] = direct_socket,
        telemetry: Telemetry | None = None,
        permits: TurnPermits | None = None,
        active_revision: Callable[[], Awaitable[ActiveRevision]] | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            parts = urlsplit(endpoint)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.hostname not in approved_hosts
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or parts.username is not None
                or parts.password is not None
                or parts.port not in {None, 443}
                or "\\" in endpoint
                or any(ord(character) < 33 for character in endpoint)
                or any(port is None for port in (credential, utc_now, monotonic, socket_factory))
                or type(max_clock_skew_seconds) not in (int, float)
                or not math.isfinite(max_clock_skew_seconds)
                or not 0 <= max_clock_skew_seconds <= 1
            ):
                raise ValueError("invalid_origin")
            self._audience = str(UUID(audience))
        except Exception:
            raise ValueError("invalid_test_relay_binding") from None
        self._origin = parts.netloc
        self._text = (
            TextRelay(
                origin=parts.netloc,
                audience=self._audience,
                credential=credential,
                permits=permits,
                active_revision=active_revision,
                utc_now=utc_now,
                skew=max_clock_skew_seconds,
                transport=http_transport,
                telemetry=telemetry,
            )
            if permits is not None and active_revision is not None
            else None
        )
        self._credential, self._factory = credential, socket_factory
        self._utc_now, self._monotonic, self._skew = utc_now, monotonic, max_clock_skew_seconds
        self._clock: tuple[datetime, float] | None = None
        self._cleanup: set[asyncio.Task[None]] = set()
        self._active = 0
        self._runners: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._telemetry = telemetry
        self._logger = logging.getLogger("management.test-relay.transport")
        self._logger.disabled = True

    def _time(self) -> datetime:
        now, tick = utc(self._utc_now()), self._monotonic()
        if not math.isfinite(tick):
            raise PermissionError("test_clock_unavailable")
        if self._clock is not None:
            previous, prior_tick = self._clock
            elapsed = (now - previous).total_seconds()
            if elapsed < 0 or tick < prior_tick or abs(elapsed - (tick - prior_tick)) > self._skew:
                raise PermissionError("test_clock_unavailable")
        self._clock = now, tick
        return now

    def _startup(self, permit: StartPermit, deadline: float) -> float:
        now = self._time() + timedelta(seconds=self._skew)
        if now < permit.issued_at or now < permit.admission_started_at:
            raise PermissionError("test_clock_unavailable")
        remaining = min(
            deadline - asyncio.get_running_loop().time(),
            (permit.admission_deadline_utc - now).total_seconds(),
            (permit.auth_expires_at - now).total_seconds(),
        )
        if not math.isfinite(deadline) or not 0 < remaining <= 10:
            raise PermissionError("test_deadline_exceeded")
        return remaining

    def _current(self, permit: StartPermit, validate: Callable[[], ServerSession]) -> ServerSession:
        current = validate()
        now = self._time() + timedelta(seconds=self._skew)
        if (
            current is None
            or not math.isfinite(current.expires_at)
            or now.timestamp() >= min(current.expires_at, permit.auth_expires_at.timestamp())
        ):
            raise PermissionError("test_auth_expired")
        return current

    def _dispose(self, socket: RuntimeSocket) -> None:
        async def close() -> None:
            try:
                async with asyncio.timeout(10):
                    await socket.close()
            except Exception:
                pass

        task = asyncio.create_task(close())
        self._cleanup.add(task)
        task.add_done_callback(self._cleanup.discard)

    def _ready(self, raw: str, permit: StartPermit) -> ConsoleSnapshot:
        envelope = parse_control(raw)
        if envelope.get("type") == "error" and "sessionId" not in envelope:
            raise BackendRefusal.from_message(envelope)
        expected = {
            "type",
            "sessionId",
            "sequence",
            "generation",
            "mode",
            "revisionId",
            "revisionNumber",
            "agentId",
            "expiresAt",
            "idleExpiresAt",
            "bounds",
        }
        if (
            set(envelope) != expected | {"audioFormat"}
            or envelope["type"] != "ready"
            or envelope["mode"] != permit.mode
            or type(envelope["sequence"]) is not int
            or envelope["sequence"] != 1
            or envelope["bounds"]
            != {
                "controlBytes": 8192,
                "textBytes": 4096,
                "replyBytes": 65536,
                "captureBytes": 12000,
                "playbackBytes": 24000,
                "inputFrameBytes": 1920,
            }
        ):
            raise PermissionError("invalid_test_ready")
        if envelope["audioFormat"] != {
            "codec": "pcm16le",
            "sampleRate": 24000,
            "channels": 1,
        }:
            raise PermissionError("invalid_test_ready")
        snapshot = ConsoleSnapshot.model_validate_json(
            json.dumps(
                {"status": "ready"}
                | {
                    key: value
                    for key, value in envelope.items()
                    if key
                    in {
                        "mode",
                        "sessionId",
                        "generation",
                        "revisionId",
                        "revisionNumber",
                        "agentId",
                        "expiresAt",
                        "idleExpiresAt",
                    }
                }
            )
        )
        return snapshot

    async def _bridge(
        self,
        browser: WebSocket,
        runtime: RuntimeSocket,
        *,
        permit: StartPermit,
        snapshot: ConsoleSnapshot,
        token_expires: float,
        validate_current: Callable[[], ServerSession],
    ) -> None:
        stopped = asyncio.Event()
        tasks: set[asyncio.Task[None]] = set()
        server_sequence, client_sequence = 1, 0
        generation = snapshot.generation
        frames = RelayFrames(
            mode=permit.mode, session_id=str(snapshot.session_id), generation=generation
        )
        seen: dict[int, str] = {}

        def current() -> None:
            if self._closed:
                raise PermissionError("test_relay_closed")
            self._current(permit, validate_current)
            now = self._time() + timedelta(seconds=self._skew)
            if snapshot.expires_at is None or now.timestamp() >= min(
                snapshot.expires_at.timestamp(), token_expires
            ):
                raise PermissionError("test_auth_expired")

        def envelope(value: dict[str, Any], *, server: bool) -> None:
            nonlocal server_sequence, client_sequence
            if (
                value.get("sessionId") != snapshot.session_id
                or type(value.get("sequence")) is not int
                or not 1 <= value["sequence"] <= 2**32 - 1
            ):
                raise PermissionError("invalid_test_sequence")
            if server:
                if value["sequence"] <= server_sequence:
                    raise PermissionError("invalid_test_sequence")
                server_sequence = value["sequence"]
            else:
                sequence = value["sequence"]
                encoded = hashlib.sha256(
                    json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if sequence in seen:
                    if seen[sequence] != encoded:
                        raise PermissionError("invalid_test_sequence")
                else:
                    if sequence != client_sequence + 1:
                        raise PermissionError("invalid_test_sequence")
                    client_sequence = sequence
                    seen[sequence] = encoded
                    if len(seen) > 64:
                        seen.pop(next(iter(seen)))

        async def from_browser() -> None:
            while True:
                message = await browser.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    current()
                    frame = message["bytes"]
                    frames.from_browser(frame)
                    async with asyncio.timeout(1):
                        await runtime.send(frame)
                    continue
                raw = message.get("text")
                if not isinstance(raw, str):
                    raise PermissionError("invalid_test_control")
                value = parse_control(raw)
                fields = {
                    "stop": set(),
                    "ping": {"nonce"},
                    "pong": {"nonce"},
                    "audio.played": {"audioSequence", "generation"},
                    "input.mute": {"muted"},
                    "output.interrupt": {"generation"},
                }
                kind = value.get("type")
                if (
                    kind not in fields
                    or set(value) != {"type", "sequence", "sessionId"} | fields[kind]
                ):
                    raise PermissionError("invalid_test_control")
                if kind in {"ping", "pong"} and (
                    not isinstance(value["nonce"], str)
                    or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["nonce"]) is None
                ):
                    raise PermissionError("invalid_test_control")
                envelope(value, server=False)
                frames.from_browser(raw)
                if kind != "stop":
                    current()
                async with asyncio.timeout(1):
                    await runtime.send(raw)
                if kind == "stop":
                    await stopped.wait()
                    return

        async def from_runtime() -> None:
            nonlocal generation
            while True:
                raw = await runtime.recv()
                if isinstance(raw, bytes):
                    current()
                    if frames.from_runtime(raw):
                        async with asyncio.timeout(1):
                            await browser.send_bytes(raw)
                        frames.sent_audio(
                            struct.unpack(">BII", raw[:9])[2], generation=frames.generation
                        )
                    continue
                if not isinstance(raw, str):
                    raise PermissionError("invalid_test_control")
                value = parse_control(raw)
                if value.get("type") == "error" and "sessionId" not in value:
                    raise BackendRefusal.from_message(value)
                envelope(value, server=True)
                kind = value.get("type")
                fields = {
                    "pong": {"nonce"},
                    "ping": {"nonce"},
                    "command.ack": {"acceptedSequence"},
                    "agent.changed": {"agentId", "communityId"},
                    "generation.changed": {"reason"},
                    "error": {"code", "cleanupStatus", "correlationId"},
                    "ended": {"code", "cleanupStatus", "correlationId"},
                    "audio.credit": {"availableSamples", "acceptedAudioSequence"},
                    "transcript.delta": {"speaker", "fragment", "start_ms", "end_ms"},
                    "progress": {"state", "text"},
                }
                if kind not in fields or set(value) != {
                    "type",
                    "sequence",
                    "sessionId",
                    "generation",
                } | fields[kind] | (
                    {"messageId"} if kind == "error" and "messageId" in value else set()
                ) | ({"reason"} if kind == "error" and "reason" in value else set()):
                    raise PermissionError("invalid_test_control")
                if (
                    kind == "error"
                    and "messageId" in value
                    and (
                        not isinstance(value["messageId"], str)
                        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value["messageId"])
                        is None
                    )
                ):
                    raise PermissionError("invalid_test_control")
                if (
                    type(value["generation"]) is not int
                    or not 1 <= value["generation"] <= 2**32 - 1
                ):
                    raise PermissionError("invalid_test_control")
                if kind in {"error", "ended"}:
                    if (
                        not isinstance(value["code"], str)
                        or value["code"] not in (PUBLIC_CODES if kind == "error" else {"stopped"})
                        or value["cleanupStatus"] not in {"active", "complete", "pending"}
                        or not isinstance(value["correlationId"], str)
                        or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["correlationId"]) is None
                    ):
                        raise PermissionError("invalid_test_control")
                if kind == "command.ack" and (
                    type(value["acceptedSequence"]) is not int
                    or not 1 <= value["acceptedSequence"] <= client_sequence
                ):
                    raise PermissionError("invalid_test_sequence")
                if kind in {"ping", "pong"} and (
                    not isinstance(value["nonce"], str)
                    or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["nonce"]) is None
                ):
                    raise PermissionError("invalid_test_control")
                if value["generation"] < generation:
                    continue
                if value["generation"] > generation:
                    if kind not in {"agent.changed", "generation.changed", "error", "ended"}:
                        raise PermissionError("invalid_test_generation")
                    generation = value["generation"]
                if kind == "audio.credit":
                    frames.from_runtime(raw)
                if kind in {"generation.changed", "agent.changed"}:
                    frames.from_runtime(raw)
                if kind == "transcript.delta" and (
                    permit.mode != "speech"
                    or value["speaker"] not in {"user", "assistant"}
                    or not isinstance(value["fragment"], str)
                    or len(value["fragment"].encode("utf-8")) > 4096
                    or any(
                        type(value[field]) not in (int, float) or not math.isfinite(value[field])
                        for field in ("start_ms", "end_ms")
                    )
                    or not 0 <= value["start_ms"] <= value["end_ms"] <= 3600000
                ):
                    raise PermissionError("invalid_test_control")
                if kind == "progress" and (
                    permit.mode != "speech" or not valid_progress(value["state"], value["text"])
                ):
                    raise PermissionError("invalid_test_control")
                if kind not in {"error", "ended"}:
                    current()
                async with asyncio.timeout(1):
                    await browser.send_text(raw)
                if kind == "ended":
                    await browser.close(code=1000, reason="stopped")
                    stopped.set()
                    return

        async def watch() -> None:
            while True:
                current()
                try:
                    async with asyncio.timeout(0.9):
                        await stopped.wait()
                except TimeoutError:
                    continue
                return

        try:
            tasks = {
                asyncio.create_task(from_browser()),
                asyncio.create_task(from_runtime()),
                asyncio.create_task(watch()),
            }
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            stopped.set()
            for task in tasks:
                task.cancel()
            # Not gather: it re-raises a child's bare CancelledError, which hides a server cancel.
            if tasks:
                await asyncio.wait(tasks)
            seen.clear()
            frames.clear()

    async def run(
        self,
        browser: WebSocket,
        *,
        permit: StartPermit,
        signed_permit: str,
        validate_current: Callable[[], ServerSession],
        deadline: float,
    ) -> None:
        if self._closed or self._active + len(self._cleanup) >= 5:
            await browser.close(code=1013, reason="test_unavailable")
            return
        self._active += 1
        runner = asyncio.current_task()
        if runner is not None:
            self._runners.add(runner)
        runtime: RuntimeSocket | None = None
        try:
            self._current(permit, validate_current)
            remaining = self._startup(permit, deadline)
            if permit.mode == "text":
                if self._text is None:
                    raise PermissionError("test_text_unavailable")
                await self._text.run(
                    browser,
                    permit=permit,
                    validate_current=validate_current,
                    deadline=deadline,
                    current=lambda: self._current(permit, validate_current),
                )
                return
            async with asyncio.timeout_at(
                min(deadline, asyncio.get_running_loop().time() + remaining)
            ):
                token = await self._credential.get_token(f"api://{self._audience}/.default")
                remaining = self._startup(permit, deadline)
                self._current(permit, validate_current)
                if (
                    not token.token
                    or not math.isfinite(token.expires_on)
                    or token.expires_on <= permit.admission_deadline_utc.timestamp()
                ):
                    raise PermissionError("test_workload_expired")
                request = compose_request(
                    "voice",
                    origin=self._origin,
                    token=token.token,
                    signed_permit=signed_permit,
                    request_id=permit.request_id,
                    correlation_id=correlation_for(permit.request_id),
                )
                runtime = await self._factory(
                    request.url,
                    additional_headers=request.headers,
                    subprotocols=list(request.subprotocols),
                    ssl=ssl.create_default_context(),
                    proxy=None,
                    compression=None,
                    max_size=8192,
                    max_queue=1,
                    write_limit=12000,
                    open_timeout=remaining,
                    close_timeout=1,
                    ping_interval=20,
                    # Reads pause while a browser send is pending; a pong may wait behind audio.
                    ping_timeout=5,
                    logger=self._logger,
                )
                self._startup(permit, deadline)
                self._current(permit, validate_current)
                await runtime.send(json.dumps(request.message, separators=(",", ":")))
                ready = await runtime.recv()
                self._startup(permit, deadline)
                self._current(permit, validate_current)
                if not isinstance(ready, str) or len(ready.encode("utf-8")) > 8192:
                    raise PermissionError("invalid_test_ready")
                snapshot = self._ready(ready, permit)
                self._startup(permit, deadline)
                async with asyncio.timeout_at(deadline):
                    await browser.send_text(ready)
                    if self._telemetry is not None:
                        self._telemetry.event(
                            "test.relay",
                            "success",
                            mode=permit.mode,
                            reason="ready",
                            correlation_id=correlation_for(permit.request_id),
                        )
            signed_permit = ""
            token_expires = token.expires_on
            del token
            await self._bridge(
                browser,
                runtime,
                permit=permit,
                snapshot=snapshot,
                token_expires=token_expires,
                validate_current=validate_current,
            )
        except Exception as error:
            code = 1011
            reason = "test_unavailable"
            if isinstance(error, BackendRefusal):
                code, reason = error.browser_close()
            elif isinstance(error, RelayLimit):
                code = 1009
            elif isinstance(error, ConnectionClosed) and error.rcvd is not None:
                refusal = BACKEND_CLOSE_CODES.get(error.rcvd.code)
                if refusal is not None:
                    code, reason = BackendRefusal(
                        refusal, error.rcvd.reason or None
                    ).browser_close()
                elif error.rcvd.code in {1000, 1008, 1009, 1011, 1013}:
                    code = error.rcvd.code
            elif isinstance(error, (HTTPException, PermissionError, ValueError)):
                code = 1008
            if self._telemetry is not None and code in {1011, 1013} and reason != "manager_busy":
                self._telemetry.event(
                    "test.relay",
                    "failure",
                    mode=permit.mode,
                    reason="unavailable",
                    correlation_id=correlation_for(permit.request_id),
                )
                closed = error if isinstance(error, ConnectionClosed) else None
                self._telemetry.diagnostic(
                    "test.relay.failure",
                    mode=permit.mode,
                    correlation_id=correlation_for(permit.request_id),
                    error=type(error).__name__,
                    numbers={
                        "received_close": closed.rcvd.code if closed and closed.rcvd else None,
                        "sent_close": closed.sent.code if closed and closed.sent else None,
                        "browser_close": code,
                    },
                )
            try:
                async with asyncio.timeout(1):
                    await browser.close(code=code, reason=reason)
            except Exception:
                pass
        finally:
            self._active -= 1
            if runner is not None:
                self._runners.discard(runner)
            if runtime is not None:
                self._dispose(runtime)

    async def close(self) -> None:
        self._closed = True
        async with asyncio.timeout(10):
            runners = tuple(task for task in self._runners if task is not asyncio.current_task())
            for task in runners:
                task.cancel()
            if runners:
                await asyncio.gather(*runners, return_exceptions=True)
            if self._cleanup:
                await asyncio.gather(*tuple(self._cleanup), return_exceptions=True)
