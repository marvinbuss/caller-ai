"""GPT-Live client-delegation adapter on the OpenAI SDK, with strict validation of every event.

`sdk_socket` opens the session through `AsyncOpenAI(...).live.connect()` with bounded frames, no
proxy and no redirect to another origin. `GptLiveProtocol` validates each raw frame (size, duplicate
keys, shape, exact started configuration) because the SDK does not; `GptLiveAdapter` and
`GptLiveSession` add admission, deadlines and a closure receipt. Failures are fixed codes, never
provider text. No Realtime, Responses or other fallback exists.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import math
import re
import ssl
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

from azure.core.credentials import AccessToken
from openai import AsyncOpenAI
from openai.resources.live.live import AsyncLiveConnection
from openai.types.websocket_connection_options import WebSocketConnectionOptions

from caller_runtime.adapters.strict_json import unique_object
from caller_runtime.domain.conversation import (
    AttemptIntent,
    ClosureReceipt,
    TestSlot,
    WorkBudget,
    utc,
)
from caller_runtime.observability import Telemetry
from caller_runtime.services.admission import (
    AdmissionError,
    SettlementObserver,
    TestAdmissionService,
)

EVENT_LIMIT = 256 * 1024
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
CLOSE_REASONS = frozenset(
    {"close_requested", "expired", "content", "remote_hangup", "connection_lost"}
)
PCM_FORMAT = {"type": "audio/pcm", "rate": 24000}


def keys(document: dict[str, Any], required: set[str], optional: set[str] | None = None) -> None:
    if not required <= document.keys() or not document.keys() <= required | (optional or set()) | {
        "event_id"
    }:
        raise ValueError("invalid_live_shape")
    event_id = document.get("event_id")
    if event_id is not None and (
        not isinstance(event_id, str) or not IDENTIFIER.fullmatch(event_id)
    ):
        raise ValueError("invalid_live_event_id")


def timeline(document: dict[str, Any]) -> None:
    for name in ("start_ms", "end_ms"):
        value = document[name]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 3600000:
            raise ValueError("invalid_live_timeline")
    if document["end_ms"] <= document["start_ms"]:
        raise ValueError("invalid_live_timeline")


class GptLiveProtocol:
    def __init__(self, *, instructions: str, voice: str) -> None:
        if (
            not isinstance(instructions, str)
            or not instructions.strip()
            or len(instructions.encode("utf-8")) > 16 * 1024
            or not isinstance(voice, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", voice)
        ):
            raise ValueError("invalid_live_configuration")
        self._instructions, self._voice = instructions, voice
        self.provider_session_id: str | None = None
        self.voice_seconds = 0.0
        self.closed = False
        self._delegations: set[str] = set()

    def start_event(self) -> dict[str, Any]:
        return {
            "type": "session.start",
            "session": {
                "model": "gpt-live-1",
                "instructions": self._instructions,
                "audio": {"output": {"voice": self._voice}},
                "delegation": {"type": "client"},
            },
        }

    def audio_event(self, pcm: bytes) -> dict[str, str]:
        if (
            self.provider_session_id is None
            or self.closed
            or not isinstance(pcm, bytes)
            or not pcm
            or len(pcm) % 2
            or len(pcm) > 1920
        ):
            raise ValueError("invalid_live_audio")
        return {
            "type": "session.input_audio.append",
            "audio": base64.b64encode(pcm).decode("ascii"),
        }

    def context_event(
        self, delegation_id: str | None, content: str, *, quiet: bool = False
    ) -> dict[str, Any]:
        if (
            self.provider_session_id is None
            or self.closed
            or (delegation_id is not None and delegation_id not in self._delegations)
            or not isinstance(content, str)
            or not content.strip()
            or len(content.encode("utf-8")) > 500
            or type(quiet) is not bool
        ):
            raise ValueError("invalid_live_context")
        return {
            "type": "session.thinking.append" if quiet else "session.commentary.append",
            "delegation_id": delegation_id,
            "content": content,
        }

    def accept(self, raw: str | bytes) -> dict[str, Any]:
        try:
            if not isinstance(raw, (str, bytes)):
                raise ValueError("invalid_live_message")
            content = raw.encode("utf-8") if isinstance(raw, str) else raw
            if len(content) > EVENT_LIMIT or self.closed:
                raise ValueError("invalid_live_message")
            event = json.loads(
                content,
                object_pairs_hook=unique_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError("nonfinite_live_value")
                ),
            )
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError("invalid_live_shape")
            kind = event["type"]
            if kind == "error":
                raise ValueError("live_provider_error")
            if kind == "session.started":
                keys(event, {"type", "session"})
                if self.provider_session_id is not None or not isinstance(event["session"], dict):
                    raise ValueError("duplicate_live_start")
                session = event["session"]
                expected = self.start_event()["session"]
                keys(
                    session,
                    {"id", *expected},
                    {"expires_at", "status", "input", "context_management"},
                )
                if not isinstance(session["id"], str) or not IDENTIFIER.fullmatch(session["id"]):
                    raise ValueError("invalid_live_session")
                audio = session["audio"]
                if not isinstance(audio, dict):
                    raise ValueError("changed_live_configuration")
                keys(audio, {"output"}, {"format"})
                if (
                    any(session[key] != expected[key] for key in expected if key != "audio")
                    or audio["output"] != expected["audio"]["output"]
                    or audio.get("format", PCM_FORMAT) != PCM_FORMAT
                    or session.get("status", "active") != "active"
                    or session.get("input", []) != []
                    or not isinstance(session.get("context_management", {}), dict)
                ):
                    raise ValueError("changed_live_configuration")
                if "expires_at" in session and (
                    type(session["expires_at"]) is not int or session["expires_at"] <= 0
                ):
                    raise ValueError("invalid_live_expiry")
                self.provider_session_id = session["id"]
                return event
            if self.provider_session_id is None:
                raise ValueError("live_not_started")
            if kind in {
                "session.output_audio.delta",
                "session.input_transcript.delta",
                "session.output_transcript.delta",
            }:
                if kind == "session.output_audio.delta":
                    # Live audio deltas carry no timeline; transcripts always do.
                    keys(event, {"type", "delta"}, {"start_ms", "end_ms"})
                    if ("start_ms" in event) != ("end_ms" in event):
                        raise ValueError("invalid_live_timeline")
                else:
                    keys(event, {"type", "delta", "start_ms", "end_ms"})
                if "start_ms" in event:
                    timeline(event)
                if not isinstance(event["delta"], str):
                    raise ValueError("invalid_live_delta")
                if kind == "session.output_audio.delta":
                    if len(event["delta"]) > 32000:
                        raise ValueError("live_audio_queue_limit")
                    pcm = base64.b64decode(event["delta"], validate=True)
                    if not pcm or len(pcm) % 2 or len(pcm) > 24000:
                        raise ValueError("invalid_live_pcm")
                    event["pcm"] = pcm
                elif len(event["delta"].encode("utf-8")) > 8 * 1024:
                    raise ValueError("live_transcript_limit")
                return event
            if kind == "session.delegation.created":
                keys(event, {"type", "delegation", "offset_ms"})
                delegation = event["delegation"]
                if not isinstance(delegation, dict):
                    raise ValueError("invalid_client_delegation")
                keys(delegation, {"id", "type", "target"})
                if (
                    delegation["type"] != "delegation"
                    or delegation["target"] != "client"
                    or not isinstance(delegation["id"], str)
                    or not IDENTIFIER.fullmatch(delegation["id"])
                    or type(event["offset_ms"]) not in (int, float)
                    or not math.isfinite(event["offset_ms"])
                    or not 0 <= event["offset_ms"] <= 3600000
                ):
                    raise ValueError("invalid_client_delegation")
                if delegation["id"] not in self._delegations and len(self._delegations) >= 64:
                    raise ValueError("client_delegation_limit")
                self._delegations.add(delegation["id"])
                return event
            if kind in {"session.usage.updated", "session.closed"}:
                keys(
                    event,
                    {"type", "usage"} | ({"reason"} if kind == "session.closed" else set()),
                    {"context_window"} if kind == "session.usage.updated" else {"session"},
                )
                if "session" in event and (
                    not isinstance(event["session"], dict)
                    or event["session"].get("id") != self.provider_session_id
                ):
                    raise ValueError("invalid_live_session")
                usage = event["usage"]
                if not isinstance(usage, dict) or set(usage) != {"seconds"}:
                    raise ValueError("invalid_live_usage")
                seconds = usage["seconds"]
                if (
                    type(seconds) not in (int, float)
                    or not math.isfinite(seconds)
                    or not 0 <= seconds <= 2**31
                ):
                    raise ValueError("invalid_live_usage")
                if "context_window" in event:
                    window = event["context_window"]
                    if (
                        not isinstance(window, dict)
                        or set(window) != {"usage_ratio"}
                        or type(window["usage_ratio"]) not in (int, float)
                        or not 0 <= window["usage_ratio"] <= 1
                    ):
                        raise ValueError("invalid_live_context_usage")
                self.voice_seconds = max(self.voice_seconds, float(seconds))
                if kind == "session.closed":
                    if event["reason"] not in CLOSE_REASONS:
                        raise ValueError("invalid_live_close_reason")
                    self.closed = True
                    event["provider_session_id"] = self.provider_session_id
                return event
            if kind in {"session.input_audio.muted", "session.input_audio.unmuted"}:
                keys(event, {"type"})
                return event
            if kind in {
                "session.instructions.appended",
                "session.thinking.appended",
                "session.commentary.appended",
            }:
                keys(event, {"type"}, {"client_event_id", "start_ms", "end_ms"})
                if ("start_ms" in event) != ("end_ms" in event):
                    raise ValueError("invalid_context_ack")
                if "start_ms" in event:
                    timeline(event)
                return event
            raise ValueError("unsupported_live_event")
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError, binascii.Error):
            raise ValueError("invalid_live_event") from None


class LiveCredential(Protocol):
    async def get_token(self, *scopes: str) -> AccessToken: ...


class LiveSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


class _SdkSocket:
    def __init__(self, client: AsyncOpenAI, connection: AsyncLiveConnection) -> None:
        self._client, self._connection = client, connection

    async def send(self, message: str) -> None:
        await self._connection.send_raw(message)

    async def recv(self) -> str | bytes:
        # Raw bytes keep the strict duplicate-key and size validation in GptLiveProtocol.
        return await self._connection.recv_bytes()

    async def close(self) -> None:
        try:
            await self._connection.close()
        finally:
            await self._client.close()


async def sdk_socket(base_url: str, *, token: str, **options: Any) -> LiveSocket:
    # No retries or reconnects: a dropped Live session must end, never silently resume.
    client = AsyncOpenAI(api_key=token, base_url=base_url, max_retries=0)
    try:
        connection = await client.live.connect(
            websocket_connection_options=cast(WebSocketConnectionOptions, options)
        ).enter()
    except BaseException:
        await client.close()
        raise
    return _SdkSocket(client, connection)


class GptLiveAdapter:
    def __init__(
        self,
        *,
        endpoint: str,
        approved_hosts: frozenset[str],
        credential: LiveCredential,
        admission: TestAdmissionService,
        utc_now: Callable[[], datetime],
        voice: str,
        socket_factory: Callable[..., Awaitable[LiveSocket]] = sdk_socket,
        settlement_observer: SettlementObserver | None = None,
        telemetry: Telemetry | None = None,
        call_observer: Callable[[bool], None] | None = None,
    ) -> None:
        try:
            parts = urlsplit(endpoint)
            if (
                parts.scheme != "https"
                or parts.hostname not in approved_hosts
                or not parts.hostname
                or not parts.hostname.endswith((".openai.azure.com", ".services.ai.azure.com"))
                or parts.username is not None
                or parts.password is not None
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or parts.port not in {None, 443}
                or "\\" in endpoint
                or any(ord(character) < 33 for character in endpoint)
                or credential is None
                or admission is None
                or socket_factory is None
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", voice)
            ):
                raise ValueError("invalid_binding")
        except (ValueError, TypeError):
            raise ValueError("invalid_live_binding") from None
        self._url = "https://" + (parts.netloc or "") + "/openai/v1"
        self._credential, self.admission, self.utc_now = credential, admission, utc_now
        self._voice, self._factory = voice, socket_factory
        self._settlement_observer = settlement_observer
        self._call_observer = call_observer
        self._telemetry = telemetry
        self._sessions: dict[str, GptLiveSession] = {}
        self._closed = False
        self._logger = logging.getLogger("caller-runtime.live.transport")
        self._logger.disabled = True
        self.binding_hash = hashlib.sha256(
            json.dumps(
                {
                    "endpoint": endpoint.rstrip("/"),
                    "model": "gpt-live-1",
                    "voice": voice,
                    "delegation": "client",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    async def start(
        self, slot: TestSlot, *, instructions: str, work: WorkBudget | None = None
    ) -> "GptLiveSession":
        if self._closed or slot.mode != "speech":
            raise PermissionError("mode_mismatch")
        if slot.session_id in self._sessions or len(self._sessions) >= 5:
            raise PermissionError("live_session_busy")
        protocol = GptLiveProtocol(instructions=instructions, voice=self._voice)
        boundary = min(
            slot.auth_expires_at,
            slot.expires_at,
            slot.idle_expires_at,
            work.work_deadline_utc if work is not None else slot.admission_deadline_utc,
        )
        remaining = min(10.0, (boundary - utc(self.utc_now())).total_seconds())
        if remaining <= 0:
            raise PermissionError("live_deadline_exceeded")
        socket: LiveSocket | None = None
        try:
            async with asyncio.timeout(remaining):
                token = await self._credential.get_token("https://ai.azure.com/.default")
                # The token must outlive the session boundary; the SDK cannot refresh it.
                if (
                    not token.token
                    or not math.isfinite(token.expires_on)
                    or token.expires_on <= boundary.timestamp()
                ):
                    raise PermissionError("live_identity_unavailable")
                intent = await self.admission.begin_attempt(
                    slot.session_id,
                    slot.owner_epoch,
                    kind="live",
                    binding_hash=self.binding_hash,
                    generation=slot.generation,
                    work=work,
                )

                async def begin() -> LiveSocket:
                    nonlocal socket
                    socket = await self._factory(
                        self._url,
                        token=token.token,
                        ssl=ssl.create_default_context(),
                        proxy=None,
                        compression=None,
                        max_size=EVENT_LIMIT,
                        max_queue=1,
                        write_limit=12000,
                        open_timeout=remaining,
                        close_timeout=1,
                        ping_interval=20,
                        # Reads pause for up to 2 s while playback credit is exhausted.
                        ping_timeout=5,
                        logger=self._logger,
                    )
                    await socket.send(json.dumps(protocol.start_event(), allow_nan=False))
                    event = protocol.accept(await socket.recv())
                    if event["type"] != "session.started":
                        raise ValueError("live_not_started")
                    return socket

                connected = await self.admission.perform(intent, begin, work=work)
                self._observe_call(True)
                provider_id = protocol.provider_session_id
                if provider_id is None:
                    raise ValueError("live_not_started")
                bound = await self.admission.bind_live_session(
                    intent, provider_session_id=provider_id
                )
                session = GptLiveSession(
                    adapter=self, socket=connected, protocol=protocol, intent=bound
                )
                self._sessions[slot.session_id] = session
                return session
        except AdmissionError as error:
            raise PermissionError(error.code) from None
        except PermissionError:
            raise
        except Exception:
            self._observe_call(False)
            raise PermissionError("live_unavailable") from None
        finally:
            if socket is not None and slot.session_id not in self._sessions:
                # A socket opened by a start that failed must not outlive it.
                try:
                    async with asyncio.timeout(1):
                        await socket.close()
                except Exception:
                    pass

    def _observe_call(self, success: bool) -> None:
        if self._call_observer is not None:
            try:
                self._call_observer(success)
            except Exception:
                pass

    async def close(self) -> None:
        self._closed = True
        sessions = tuple(self._sessions.values())
        if sessions:
            async with asyncio.timeout(10):
                await asyncio.gather(
                    *(session.close() for session in sessions), return_exceptions=True
                )


class GptLiveSession:
    def __init__(
        self,
        *,
        adapter: GptLiveAdapter,
        socket: LiveSocket,
        protocol: GptLiveProtocol,
        intent: AttemptIntent,
    ) -> None:
        self.adapter, self._socket, self.protocol, self.intent = adapter, socket, protocol, intent
        self._read_lock, self._write_lock, self._close_lock = (
            asyncio.Lock(),
            asyncio.Lock(),
            asyncio.Lock(),
        )
        self._receipt: ClosureReceipt | None = None
        self._settlement_ref: str | None = None
        self._closure_confirmed = False
        self._disposed = False
        self._transport_disposed = False
        self._retired = False
        self._muted = False

    @property
    def provider_session_id(self) -> str:
        if self.intent.provider_session_id is None:
            raise PermissionError("live_unavailable")
        return self.intent.provider_session_id

    async def _send(self, event: dict[str, Any]) -> None:
        if self._retired and event["type"] != "session.close":
            raise PermissionError("stale_generation")
        raw = json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > EVENT_LIMIT:
            raise PermissionError("live_message_limit")
        async with asyncio.timeout(1):
            async with self._write_lock:
                if self._retired and event["type"] != "session.close":
                    raise PermissionError("stale_generation")
                await self._socket.send(raw)

    async def _receive(self) -> dict[str, Any]:
        async with self._read_lock:
            if self.protocol.closed:
                if self._receipt is None:
                    raise PermissionError("live_closure_unverified")
                return {"type": "session.closed"}
            event = self.protocol.accept(await self._socket.recv())
            if self.adapter._telemetry is not None and event["type"] in {
                "session.usage.updated",
                "session.closed",
            }:
                self.adapter._telemetry.event(
                    "test.usage",
                    "success",
                    mode="speech",
                    reason="stopped" if event["type"] == "session.closed" else "ready",
                    measurement="voice_seconds_final"
                    if event["type"] == "session.closed"
                    else "voice_seconds_snapshot",
                    value=self.protocol.voice_seconds,
                )
            if event["type"] == "session.closed":
                self._receipt = ClosureReceipt(
                    sessionId=self.intent.session_id,
                    providerSessionId=self.provider_session_id,
                    ownerId=self.intent.owner_id,
                    ownerEpoch=self.intent.owner_epoch,
                    generation=self.intent.generation,
                    observedAt=utc(self.adapter.utc_now()),
                    provenanceRef=self.intent.provenance_ref,
                    reason=event["reason"],
                    voiceSeconds=self.protocol.voice_seconds,
                    authenticated=True,
                )
                try:
                    await self._confirm_closure()
                except Exception:
                    # The provider's close event is already proven; close() settles it again, so
                    # one transient authority failure must not leave the slot held as unverified.
                    pass
            return event

    async def _confirm_closure(self) -> None:
        if self._closure_confirmed:
            return
        if self._receipt is None:
            raise PermissionError("live_closure_unverified")
        if self._settlement_ref is None:
            self._settlement_ref = "live-" + uuid4().hex
        if self.adapter._settlement_observer is not None:
            self.adapter._settlement_observer.record(
                self.intent, self._settlement_ref, closure=self._receipt
            )
        await self.adapter.admission.settle_attempt(
            self.intent,
            settlement_ref=self._settlement_ref,
            closure=self._receipt,
        )
        self._closure_confirmed = True

    async def send_audio(self, pcm: bytes) -> None:
        try:
            if self._muted:
                raise PermissionError("input_muted")
            event = self.protocol.audio_event(pcm)
            await self.adapter.admission.check_media(
                self.intent.session_id, self.intent.owner_epoch, generation=self.intent.generation
            )
            await self._send(event)
        except PermissionError:
            raise
        except Exception:
            if self.adapter._telemetry is not None and not self._retired:
                self.adapter._telemetry.event(
                    "test.session", "failure", mode="speech", reason="unavailable"
                )
            raise PermissionError("live_unavailable") from None

    async def set_muted(self, muted: bool) -> None:
        if type(muted) is not bool:
            raise PermissionError("invalid_mute_state")
        try:
            await self.adapter.admission.check_media(
                self.intent.session_id, self.intent.owner_epoch, generation=self.intent.generation
            )
            self._muted = muted
            await self._send(
                {"type": "session.input_audio.mute" if muted else "session.input_audio.unmute"}
            )
        except Exception:
            raise PermissionError("live_unavailable") from None

    async def append_context(
        self,
        delegation_id: str | None,
        content: str,
        *,
        quiet: bool = False,
        work: WorkBudget | None = None,
    ) -> None:
        try:
            if work is not None:
                work.require(utc(self.adapter.utc_now()))
            event = self.protocol.context_event(delegation_id, content, quiet=quiet)
            await self.adapter.admission.check_output(
                self.intent.session_id, self.intent.owner_epoch, generation=self.intent.generation
            )
            await self._send(event)
        except Exception:
            raise PermissionError("live_context_unavailable") from None

    async def next_event(self) -> dict[str, Any]:
        if self.protocol.closed or self._disposed or self._retired:
            raise PermissionError("live_unavailable")
        try:
            while True:
                await self.adapter.admission.check_output(
                    self.intent.session_id,
                    self.intent.owner_epoch,
                    generation=self.intent.generation,
                )
                try:
                    async with asyncio.timeout(1):
                        event = await self._receive()
                except TimeoutError:
                    continue
                await self.adapter.admission.check_output(
                    self.intent.session_id,
                    self.intent.owner_epoch,
                    generation=self.intent.generation,
                )
                if self._retired:
                    raise PermissionError("stale_generation")
                return event
        except Exception:
            raise PermissionError("live_unavailable") from None

    def retire_output(self) -> None:
        self._retired = True

    @property
    def disposed(self) -> bool:
        return self._transport_disposed

    async def close(self, *, deadline: float | None = None) -> ClosureReceipt:
        self.retire_output()
        deadline = min(deadline or float("inf"), asyncio.get_running_loop().time() + 10)
        async with self._close_lock:
            try:
                async with asyncio.timeout_at(deadline):
                    if not self.protocol.closed:
                        await self._send({"type": "session.close"})
                    # Only the provider's close event proves usage ended; a dropped socket does not.
                    while self._receipt is None:
                        await self._receive()
                    for attempt in range(3):
                        try:
                            await self._confirm_closure()
                            break
                        except Exception:
                            if attempt == 2:
                                raise
                            await asyncio.sleep(0.05 * (attempt + 1))
            except Exception:
                raise PermissionError("live_closure_unverified") from None
            finally:
                if not self._disposed:
                    self._disposed = True
                    try:
                        async with asyncio.timeout_at(
                            min(deadline, asyncio.get_running_loop().time() + 1)
                        ):
                            await self._socket.close()
                            self._transport_disposed = True
                    except Exception:
                        pass
                if self._receipt is not None and self._transport_disposed:
                    self.adapter._sessions.pop(self.intent.session_id, None)
            if self._receipt is None or not self._transport_disposed:
                raise PermissionError("live_closure_unverified")
            return self._receipt
