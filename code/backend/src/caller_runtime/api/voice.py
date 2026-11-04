"""The voice conversation endpoint: speech over one WebSocket, authenticated by the first message.

The workload credential rides in `Authorization` on the upgrade and the signed start permit in the
first message. Rejections before the upgrade are plain HTTP denials; later failures send one
`error` message and close with the code the shared contract assigns to that error.
"""

import asyncio
import hashlib
import json
import math
import re
import struct
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from fastapi import APIRouter, WebSocket
from pydantic import Field
from starlette.websockets import WebSocketDisconnect, WebSocketState

from caller_runtime.adapters.strict_json import unique_object
from caller_runtime.api.connection import (
    FIRST_MESSAGE_SECONDS,
    MAX_CONTROL_BYTES,
    PermitVerifier,
    PublicError,
    WorkloadIdentity,
    parse_settings,
    public_error,
    public_error_for,
    verify_permit,
)
from caller_runtime.domain.conversation import Identifier, Metadata, StartPermit, WorkBudget
from caller_runtime.observability import Telemetry, sanitize_correlation_id
from caller_runtime.services.admission import AdmissionError
from caller_runtime.services.conversation import ConversationExecutor

PATH = "/v1/conversations/voice"
SUBPROTOCOL = "caller.voice.v1"


class OversizedFrame(PermissionError):
    pass


class WirePressure(PermissionError):
    pass


async def receive_text(socket: WebSocket) -> str:
    message = await socket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    raw = message.get("text")
    if not isinstance(raw, str):
        raise PermissionError("invalid_control")
    if len(raw.encode("utf-8")) > MAX_CONTROL_BYTES:
        raise OversizedFrame("control_limit")
    return raw


class ConversationService(Protocol):
    async def start(
        self, permit: StartPermit, *, emit: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> ConversationExecutor: ...


class Authenticate(Metadata):
    type: Literal["authenticate"]
    permit: str = Field(min_length=1, max_length=6144, repr=False)
    request_id: Identifier = Field(alias="requestId")
    settings: Any = None


def control(raw: str) -> dict[str, Any]:
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CONTROL_BYTES:
            raise ValueError("invalid_control")
        value = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError("invalid_control")
        json.dumps(value, allow_nan=False)
        return value
    except Exception:
        raise PermissionError("invalid_control") from None


def server_control(event: dict[str, Any]) -> dict[str, Any]:
    shapes = {
        "ready": (
            {
                "mode",
                "revisionId",
                "revisionNumber",
                "agentId",
                "expiresAt",
                "idleExpiresAt",
                "bounds",
            },
            {"audioFormat"},
        ),
        "transcript.delta": ({"speaker", "fragment", "start_ms", "end_ms"}, set()),
        "generation.changed": ({"reason"}, set()),
        "agent.changed": ({"agentId", "communityId"}, set()),
        "audio.credit": ({"availableSamples"}, {"acceptedAudioSequence"}),
        "command.ack": ({"acceptedSequence"}, set()),
        "pong": ({"nonce"}, set()),
        "ping": ({"nonce"}, set()),
        "error": ({"code"}, {"reason", "cleanupStatus", "correlationId", "messageId"}),
        "ended": ({"code", "cleanupStatus"}, {"correlationId"}),
    }
    kind = event.get("type")
    if not isinstance(kind, str) or kind not in shapes:
        raise PermissionError("invalid_server_control")
    required, optional = shapes[kind]
    if (
        not {"type"} | required <= event.keys()
        or not event.keys() <= {"type", "generation"} | required | optional
    ):
        raise PermissionError("invalid_server_control")
    checked = dict(event)
    if kind == "error":
        public = public_error(str(checked["code"]))
        checked["code"] = public.code
        if public.reason is not None:
            checked["reason"] = public.reason
        checked.setdefault("cleanupStatus", "active" if public.reason == "work_busy" else "pending")
    if kind == "ended" and checked["code"] != "stopped":
        raise PermissionError("invalid_server_control")
    if kind in {"error", "ended"}:
        if checked["cleanupStatus"] not in {"active", "complete", "pending"}:
            raise PermissionError("invalid_server_control")
        checked["correlationId"] = sanitize_correlation_id(checked.get("correlationId"))
    if kind == "generation.changed" and checked["reason"] not in {
        "segment_retired",
        "agent_changed",
        "interrupted",
        "stopped",
    }:
        raise PermissionError("invalid_server_control")
    for field in ("messageId", "agentId", "revisionId", "communityId"):
        value = checked.get(field)
        if field in checked and not (field == "communityId" and value is None):
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value) is None
            ):
                raise PermissionError("invalid_server_control")
    for field in (
        "generation",
        "acceptedSequence",
        "acceptedAudioSequence",
        "revisionNumber",
        "availableSamples",
    ):
        if field in checked:
            value = checked[field]
            minimum = 0 if field in {"acceptedAudioSequence", "availableSamples"} else 1
            maximum = 6000 if field == "availableSamples" else 2**32 - 1
            if type(value) is not int or not minimum <= value <= maximum:
                raise PermissionError("invalid_server_control")
    if kind in {"ping", "pong"} and (
        not isinstance(checked["nonce"], str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", checked["nonce"]) is None
    ):
        raise PermissionError("invalid_server_control")
    if kind == "transcript.delta":
        if (
            checked["speaker"] not in {"user", "assistant"}
            or not isinstance(checked["fragment"], str)
            or len(checked["fragment"].encode("utf-8")) > 4096
            or any(
                type(checked[field]) not in (int, float) or not math.isfinite(checked[field])
                for field in ("start_ms", "end_ms")
            )
            or not 0 <= checked["start_ms"] <= checked["end_ms"] <= 3600000
        ):
            raise PermissionError("invalid_server_control")
    if kind == "ready":
        if checked["mode"] != "speech" or checked["bounds"] != {
            "controlBytes": 8192,
            "textBytes": 4096,
            "replyBytes": 65536,
            "captureBytes": 12000,
            "playbackBytes": 24000,
            "inputFrameBytes": 1920,
        }:
            raise PermissionError("invalid_server_control")
        if (
            checked.get("audioFormat") != {"codec": "pcm16le", "sampleRate": 24000, "channels": 1}
            or type(checked["audioFormat"]["channels"]) is not int
        ):
            raise PermissionError("invalid_server_control")
        times = []
        for field in ("expiresAt", "idleExpiresAt"):
            if not isinstance(checked[field], str) or len(checked[field]) > 64:
                raise PermissionError("invalid_server_control")
            try:
                timestamp = datetime.fromisoformat(checked[field].replace("Z", "+00:00"))
            except ValueError:
                raise PermissionError("invalid_server_control") from None
            if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
                raise PermissionError("invalid_server_control")
            times.append(timestamp)
        if times[1] > times[0]:
            raise PermissionError("invalid_server_control")
    return checked


class AudioFlow:
    def __init__(self, *, generation: int, monotonic: Callable[[], float] = time.monotonic) -> None:
        if type(generation) is not int or not 1 <= generation <= 2**32 - 1 or monotonic is None:
            raise ValueError("invalid_audio_flow")
        self.generation, self._clock = generation, monotonic
        self._observed = monotonic()
        if not math.isfinite(self._observed):
            raise ValueError("invalid_audio_flow")
        self._input_sequence = 0
        self._input_samples = 6000.0
        self._output_sequence = 0
        self._sent_sequence = 0
        self._played_sequence = 0
        self._pending: OrderedDict[int, int] = OrderedDict()
        self._pending_bytes = 0

    @property
    def input_sequence(self) -> int:
        return self._input_sequence

    @property
    def available_samples(self) -> int:
        observed = self._clock()
        if not math.isfinite(observed) or observed < self._observed:
            raise PermissionError("audio_clock_invalid")
        self._input_samples = min(6000.0, self._input_samples + (observed - self._observed) * 24000)
        self._observed = observed
        return int(self._input_samples)

    @property
    def pending_bytes(self) -> int:
        return self._pending_bytes

    def input(self, raw: bytes) -> tuple[int, bytes]:
        if not isinstance(raw, bytes) or len(raw) < 11 or (len(raw) - 9) % 2:
            raise PermissionError("invalid_audio_frame")
        if len(raw) - 9 > 1920:
            raise OversizedFrame("audio_frame_limit")
        protocol, generation, sequence = struct.unpack(">BII", raw[:9])
        if protocol != 1:
            raise PermissionError("invalid_audio_frame")
        if generation != self.generation:
            raise PermissionError("stale_generation")
        if sequence != self._input_sequence + 1:
            raise PermissionError("invalid_audio_sequence")
        samples = (len(raw) - 9) // 2
        if samples > self.available_samples:
            raise PermissionError("capture_credit_exhausted")
        self._input_samples -= samples
        self._input_sequence = sequence
        return sequence, raw[9:]

    def output(self, pcm: bytes, *, generation: int) -> list[bytes]:
        if generation != self.generation:
            raise PermissionError("stale_generation")
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 24000:
            raise PermissionError("invalid_audio_frame")
        if len(pcm) + self._pending_bytes > 24000:
            raise PermissionError("playback_credit_exhausted")
        count = (len(pcm) + 959) // 960
        if self._output_sequence + count > 2**32 - 1:
            raise PermissionError("audio_sequence_exhausted")
        frames = []
        for offset in range(0, len(pcm), 960):
            self._output_sequence += 1
            fragment = pcm[offset : offset + 960]
            self._pending[self._output_sequence] = len(fragment)
            self._pending_bytes += len(fragment)
            frames.append(struct.pack(">BII", 1, generation, self._output_sequence) + fragment)
        return frames

    def sent(self, sequence: int, *, generation: int) -> None:
        if generation != self.generation:
            raise PermissionError("stale_generation")
        if (
            type(sequence) is not int
            or sequence != self._sent_sequence + 1
            or sequence > self._output_sequence
        ):
            raise PermissionError("invalid_audio_sequence")
        self._sent_sequence = sequence

    def played(self, sequence: int, *, generation: int) -> None:
        if generation != self.generation:
            raise PermissionError("stale_generation")
        if (
            type(sequence) is not int
            or not self._played_sequence <= sequence <= self._sent_sequence
        ):
            raise PermissionError("invalid_played_ack")
        if sequence == self._played_sequence:
            return
        self._played_sequence = sequence
        while self._pending:
            first = next(iter(self._pending))
            if first > sequence:
                break
            self._pending_bytes -= self._pending.pop(first)

    def reset(self, generation: int) -> None:
        if type(generation) is not int or not self.generation < generation <= 2**32 - 1:
            raise PermissionError("stale_generation")
        self.generation = generation
        self._input_sequence = self._output_sequence = self._sent_sequence = (
            self._played_sequence
        ) = 0
        self._input_samples = 6000.0
        self._pending.clear()
        self._pending_bytes = 0
        self._observed = self._clock()
        if not math.isfinite(self._observed):
            raise PermissionError("audio_clock_invalid")

    def dispose(self) -> None:
        self._pending.clear()
        self._pending_bytes = 0
        self._input_samples = 0


class VoiceWire:
    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket
        self.session: ConversationExecutor | None = None
        self._outbound: asyncio.Queue[tuple[int, str | bytes]] = asyncio.Queue(maxsize=64)
        self._server_sequence = 0
        self._client_sequence = 0
        self._seen: OrderedDict[int, str] = OrderedDict()
        self._generation = 1
        self.audio = AudioFlow(generation=1)
        self._inflight_audio = 0
        self._inflight_generation = 1

    def _advance(self, generation: int) -> None:
        if generation <= self._generation:
            return
        self._generation = generation
        self.audio.reset(generation)
        retained = []
        while not self._outbound.empty():
            item = self._outbound.get_nowait()
            self._outbound.task_done()
            if item[0] >= generation:
                retained.append(item)
        for item in retained:
            self._outbound.put_nowait(item)

    async def emit(self, event: dict[str, Any]) -> None:
        if self.session is None:
            raise PermissionError("conversation_not_ready")
        event = server_control(event)
        if self.session.ended and event["type"] not in {"error", "ended"}:
            return
        slot = self.session.policy.slot
        generation = event.get("generation", slot.generation)
        if type(generation) is not int or not 1 <= generation <= 2**32 - 1:
            raise PermissionError("invalid_output_generation")
        self._advance(generation)
        self._server_sequence += 1
        if self._server_sequence > 2**32 - 1:
            raise PermissionError("server_sequence_exhausted")
        document = dict(event) | {
            "sessionId": slot.session_id,
            "sequence": self._server_sequence,
            "generation": generation,
        }
        raw = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > MAX_CONTROL_BYTES:
            raise PermissionError("control_limit")
        try:
            self._outbound.put_nowait((generation, raw))
        except asyncio.QueueFull:
            raise WirePressure("control_queue_full") from None

    async def audio_output(self, pcm: bytes, *, generation: int) -> None:
        if self.session is None or self.session.policy.slot.mode != "speech":
            raise PermissionError("mode_mismatch")
        if generation < self._generation:
            return
        try:
            await self.session.policy.admission.check_output(
                self.session.policy.slot.session_id,
                self.session.policy.slot.owner_epoch,
                generation=generation,
            )
        except AdmissionError as error:
            if error.code == "stale_generation":
                return
            raise
        self._advance(generation)
        extra = self._inflight_audio if self._inflight_generation != generation else 0
        if len(pcm) + self.audio.pending_bytes + extra > 24000:
            raise WirePressure("playback_credit_exhausted")
        frames = self.audio.output(pcm, generation=generation)
        try:
            for frame in frames:
                self._outbound.put_nowait((generation, frame))
        except asyncio.QueueFull:
            raise WirePressure("control_queue_full") from None

    async def write(self) -> None:
        while True:
            generation, raw = await self._outbound.get()
            try:
                if generation < self._generation:
                    continue
                if self.session is None:
                    raise PermissionError("conversation_not_ready")
                terminal = isinstance(raw, str) and json.loads(raw)["type"] in {"error", "ended"}
                if self.session.ended and not terminal:
                    continue
                if not terminal:
                    try:
                        await self.session.policy.admission.check_output(
                            self.session.policy.slot.session_id,
                            self.session.policy.slot.owner_epoch,
                            generation=generation,
                        )
                    except AdmissionError as error:
                        if error.code == "stale_generation" or self.session.ended:
                            continue
                        raise
                async with asyncio.timeout(1):
                    if isinstance(raw, bytes):
                        self._inflight_audio, self._inflight_generation = len(raw) - 9, generation
                        try:
                            await self.socket.send_bytes(raw)
                            if self.audio.generation == generation:
                                self.audio.sent(
                                    struct.unpack(">BII", raw[:9])[2], generation=generation
                                )
                        finally:
                            self._inflight_audio = 0
                    else:
                        await self.socket.send_text(raw)
            finally:
                self._outbound.task_done()

    def dispose(self) -> None:
        while not self._outbound.empty():
            self._outbound.get_nowait()
            self._outbound.task_done()
        self.audio.dispose()
        self._seen.clear()
        self.session = None

    def command(self, raw: str) -> tuple[dict[str, Any], bool]:
        if self.session is None:
            raise PermissionError("conversation_not_ready")
        value = control(raw)
        kind = value.get("type")
        fields = {
            "stop": set(),
            "ping": {"nonce"},
            "pong": {"nonce"},
            "input.mute": {"muted"},
            "output.interrupt": {"generation"},
            "audio.played": {"audioSequence", "generation"},
        }
        if (
            not isinstance(kind, str)
            or kind not in fields
            or set(value) != {"type", "sequence", "sessionId"} | fields[kind]
        ):
            raise PermissionError("invalid_control")
        sequence = value["sequence"]
        if (
            type(sequence) is not int
            or not 1 <= sequence <= 2**32 - 1
            or value["sessionId"] != self.session.policy.slot.session_id
        ):
            raise PermissionError("invalid_sequence")
        if sequence in self._seen:
            fingerprint = hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if self._seen[sequence] != fingerprint:
                raise PermissionError("invalid_sequence")
            return value, True
        if kind in {"ping", "pong"} and (
            not isinstance(value["nonce"], str)
            or not 1 <= len(value["nonce"]) <= 64
            or any(
                not character.isascii() or not (character.isalnum() or character in "-_")
                for character in value["nonce"]
            )
        ):
            raise PermissionError("invalid_control")
        if kind == "input.mute" and type(value["muted"]) is not bool:
            raise PermissionError("invalid_control")
        if kind == "output.interrupt" and (
            type(value["generation"]) is not int or value["generation"] != self._generation
        ):
            raise PermissionError("stale_generation")
        if kind == "audio.played" and (
            type(value["generation"]) is not int or value["generation"] != self._generation
        ):
            raise PermissionError("stale_generation")
        if kind == "audio.played" and (
            type(value["audioSequence"]) is not int or value["audioSequence"] < 0
        ):
            raise PermissionError("invalid_control")
        digest = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if sequence != self._client_sequence + 1:
            raise PermissionError("invalid_sequence")
        self._seen[sequence] = digest
        self._client_sequence = sequence
        if len(self._seen) > 64:
            self._seen.popitem(last=False)
        return value, False


def create_router(
    *,
    service: ConversationService,
    workload_identity: WorkloadIdentity,
    permits: PermitVerifier,
    telemetry: Telemetry | Callable[[], Telemetry],
    utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_clock_skew_seconds: float = 1,
) -> APIRouter:
    if any(port is None for port in (service, workload_identity, permits, telemetry, utc_now)) or (
        type(max_clock_skew_seconds) not in (int, float)
        or not math.isfinite(max_clock_skew_seconds)
        or not 0 <= max_clock_skew_seconds <= 1
    ):
        raise ValueError("explicit_test_socket_ports_required")
    router = APIRouter()
    pre_auth = 0
    pre_auth_by_actor: dict[str, int] = {}

    @router.websocket(PATH)
    async def voice_session(socket: WebSocket) -> None:
        nonlocal pre_auth
        session: ConversationExecutor | None = None
        wire = VoiceWire(socket)
        tasks: set[asyncio.Task[Any]] = set()
        stopped = asyncio.Event()
        pre_auth_counted = False
        verified_actor: str | None = None

        def release_pre_auth() -> None:
            nonlocal pre_auth, pre_auth_counted, verified_actor
            if pre_auth_counted:
                pre_auth -= 1
                pre_auth_counted = False
            if verified_actor is not None:
                remaining = pre_auth_by_actor.get(verified_actor, 1) - 1
                if remaining > 0:
                    pre_auth_by_actor[verified_actor] = remaining
                else:
                    pre_auth_by_actor.pop(verified_actor, None)
                verified_actor = None

        def terminate_local() -> None:
            if session is not None:
                session.fence()

        async def refuse(error: Exception) -> None:
            """Send one `error` message and close with the code assigned to that error."""
            public: PublicError = public_error_for(error)
            reason = public.reason or public.code
            state = socket.application_state
            if state == WebSocketState.CONNECTED:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    async with asyncio.timeout(1):
                        await socket.send_text(
                            json.dumps({"type": "error"} | public.body()["error"])
                        )
                except Exception:
                    pass
            if state != WebSocketState.DISCONNECTED:
                await socket.close(code=public.close_code, reason=reason)

        try:
            if socket.scope.get("scheme") != "wss" or socket.query_params:
                await refuse(PermissionError("insecure_transport"))
                return
            if socket.scope.get("subprotocols", []).count(SUBPROTOCOL) != 1:
                await refuse(PermissionError("invalid_control"))
                return
            if pre_auth >= 10:
                # Unauthenticated sockets are capped so a flood cannot hold server resources.
                await refuse(WirePressure("pre_auth_full"))
                return
            pre_auth += 1
            pre_auth_counted = True
            first_deadline = asyncio.get_running_loop().time() + FIRST_MESSAGE_SECONDS
            try:
                async with asyncio.timeout_at(first_deadline):
                    principal = await workload_identity.require_runtime(socket)
                    if (
                        not isinstance(principal, str)
                        or not principal.strip()
                        or len(principal) > 128
                    ):
                        raise PermissionError("unverified_workload")
                    # Accept only once the workload credential is verified; bad callers get HTTP.
                    await socket.accept(
                        subprotocol=SUBPROTOCOL, headers=[(b"cache-control", b"no-store")]
                    )
                    first = await receive_text(socket)
                    assertion = Authenticate.model_validate_json(
                        json.dumps(control(first), allow_nan=False)
                    )
                    permit = await verify_permit(permits, assertion.permit)
                    if permit.request_id != assertion.request_id:
                        raise PermissionError("invalid_test_permit")
                    if permit.mode != "speech":
                        raise PermissionError("mode_mismatch")
                    parse_settings(assertion.settings)
                    if pre_auth_by_actor.get(permit.actor_key, 0) >= 2:
                        # One manager cannot fill the pre-auth pool with unfinished starts.
                        raise WirePressure("actor_pre_auth_full")
                    verified_actor = permit.actor_key
                    pre_auth_by_actor[verified_actor] = pre_auth_by_actor.get(verified_actor, 0) + 1
            except TimeoutError:
                raise PermissionError("unverified_workload") from None
            activity = telemetry() if callable(telemetry) else telemetry
            remaining = min(
                10.0,
                (permit.admission_deadline_utc - utc_now()).total_seconds()
                - max_clock_skew_seconds,
            )
            if remaining <= 0:
                raise TimeoutError("admission_deadline_exceeded")
            admission_deadline = asyncio.get_running_loop().time() + remaining
            async with asyncio.timeout_at(admission_deadline):
                session = await service.start(permit, emit=wire.emit)
            first = ""
            del assertion
            wire.session = session
            activity.event("test.admission", "success", mode=permit.mode, reason="ready")
            release_pre_auth()
            slot = session.policy.slot
            writer = asyncio.create_task(wire.write())
            tasks.add(writer)
            work_ready = asyncio.Event()
            await wire.emit(
                {
                    "type": "ready",
                    "mode": slot.mode,
                    "revisionId": slot.revision_id,
                    "revisionNumber": session.policy.snapshot.revision_number,
                    "agentId": slot.active_agent_id,
                    "expiresAt": slot.expires_at.isoformat(),
                    "idleExpiresAt": slot.idle_expires_at.isoformat(),
                    "bounds": {
                        "controlBytes": 8192,
                        "textBytes": 4096,
                        "replyBytes": 65536,
                        "captureBytes": 12000,
                        "playbackBytes": 24000,
                        "inputFrameBytes": 1920,
                    },
                    "audioFormat": {"codec": "pcm16le", "sampleRate": 24000, "channels": 1},
                }
            )
            async with asyncio.timeout_at(admission_deadline):
                await wire._outbound.join()

            async def work() -> None:
                while True:
                    await work_ready.wait()
                    work_ready.clear()
                    while await session.process_next(emit=wire.emit):
                        pass
                    if session.ended:
                        return

            tasks.add(asyncio.create_task(work()))

            async def observed(current: Any) -> None:
                if await workload_identity.require_runtime(socket) != principal:
                    raise PermissionError("workload_identity_changed")

            watcher = asyncio.create_task(
                session.policy.admission.watch(
                    slot.session_id, slot.owner_epoch, stopped=stopped, observed=observed
                )
            )
            tasks.add(watcher)
            accepted_samples: dict[int, int] = {}
            speech_end: dict[int, float] = {}

            async def credit() -> None:
                while True:
                    await wire.emit(
                        {
                            "type": "audio.credit",
                            "availableSamples": wire.audio.available_samples,
                            "acceptedAudioSequence": wire.audio.input_sequence,
                        }
                    )
                    try:
                        async with asyncio.timeout(0.05):
                            await stopped.wait()
                    except TimeoutError:
                        continue
                    return

            async def voice() -> None:
                segments = session.speech_segments
                if segments is None:
                    raise PermissionError("speech_transition_unavailable")
                while True:
                    generation, event = await segments.next_event()
                    if generation != session.policy.slot.generation:
                        continue
                    kind = event["type"]
                    if kind == "session.output_audio.delta":
                        await wire.audio_output(event["pcm"], generation=generation)
                    elif kind in {
                        "session.input_transcript.delta",
                        "session.output_transcript.delta",
                    }:
                        input_speech = (
                            kind == "session.input_transcript.delta"
                            and event["end_ms"] <= accepted_samples.get(generation, 0) / 24
                            and event["end_ms"] > speech_end.get(generation, -1)
                        )
                        await session.accept_transcript(
                            event, generation=generation, input_audio_accepted=input_speech
                        )
                        if input_speech:
                            speech_end[generation] = event["end_ms"]
                            session.speech_fragments.append(
                                event["delta"],
                                start_ms=event["start_ms"],
                                end_ms=event["end_ms"],
                                generation=generation,
                                accepted=True,
                            )
                            if session.retained_bytes > 256 * 1024:
                                raise PermissionError("conversation_context_limit")
                        for offset in range(0, len(event["delta"]), 512):
                            await wire.emit(
                                {
                                    "type": "transcript.delta",
                                    "speaker": "user"
                                    if kind == "session.input_transcript.delta"
                                    else "assistant",
                                    "fragment": event["delta"][offset : offset + 512],
                                    "start_ms": event["start_ms"],
                                    "end_ms": event["end_ms"],
                                    "generation": generation,
                                }
                            )
                    elif kind == "session.delegation.created":
                        correlated = session.speech_fragments.consume(
                            offset_ms=event["offset_ms"], generation=generation
                        )
                        await session.submit_delegation(
                            segments.current,
                            event,
                            task_text=correlated,
                            unambiguous=False,
                            requires_clarity_check=correlated is not None,
                        )
                        work_ready.set()

            if session.speech_segments is None:
                raise PermissionError("speech_transition_unavailable")
            tasks.add(asyncio.create_task(credit()))
            tasks.add(asyncio.create_task(voice()))

            async def read() -> None:
                while True:
                    frame = await socket.receive()
                    if frame["type"] == "websocket.disconnect":
                        raise WebSocketDisconnect(frame.get("code", 1000))
                    if frame.get("bytes") is not None:
                        if session.speech_segments is None:
                            raise PermissionError("mode_mismatch")
                        sequence, pcm = wire.audio.input(frame["bytes"])
                        current_segment = session.speech_segments.current
                        if current_segment.intent.generation != wire.audio.generation:
                            raise PermissionError("stale_generation")
                        await current_segment.send_audio(pcm)
                        if wire.audio.generation not in accepted_samples:
                            accepted_samples.clear()
                            speech_end.clear()
                        accepted_samples[wire.audio.generation] = (
                            accepted_samples.get(wire.audio.generation, 0) + len(pcm) // 2
                        )
                        continue
                    raw = frame.get("text")
                    if not isinstance(raw, str):
                        raise PermissionError("invalid_control")
                    if len(raw.encode("utf-8")) > MAX_CONTROL_BYTES:
                        raise OversizedFrame("control_limit")
                    message, duplicate = wire.command(raw)
                    if message["type"] != "stop":
                        await session.policy.admission.check_output(
                            session.policy.slot.session_id,
                            session.policy.slot.owner_epoch,
                            generation=session.policy.slot.generation,
                        )
                    if duplicate:
                        await wire.emit(
                            {"type": "command.ack", "acceptedSequence": message["sequence"]}
                        )
                        continue
                    if message["type"] == "stop":
                        terminate_local()
                        return
                    if message["type"] == "ping":
                        await wire.emit({"type": "pong", "nonce": message["nonce"]})
                    elif message["type"] == "input.mute":
                        if session.speech_segments is None:
                            raise PermissionError("mode_mismatch")
                        await session.speech_segments.current.set_muted(message["muted"])
                        await wire.emit(
                            {"type": "command.ack", "acceptedSequence": message["sequence"]}
                        )
                    elif message["type"] == "audio.played":
                        wire.audio.played(
                            message["audioSequence"], generation=message["generation"]
                        )
                    elif message["type"] == "output.interrupt":
                        current = await session.policy.admission.check_output(
                            session.policy.slot.session_id,
                            session.policy.slot.owner_epoch,
                            generation=session.policy.slot.generation,
                        )
                        now = utc_now()
                        work_budget = WorkBudget(
                            now,
                            min(
                                now + timedelta(seconds=10),
                                current.auth_expires_at,
                                current.expires_at,
                                current.idle_expires_at,
                            ),
                        )
                        await session.policy.interrupt(work_budget)
                        await wire.emit(
                            {"type": "command.ack", "acceptedSequence": message["sequence"]}
                        )
                    elif message["type"] != "pong":
                        raise PermissionError("unsupported_control")

            reader = asyncio.create_task(read())
            tasks.add(reader)
            completed, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if watcher in completed and reader not in completed:
                watcher.result()
                raise AdmissionError("terminal")
            for finished in completed:
                finished.result()
            if reader not in completed:
                raise AdmissionError("terminal")
            stopped.set()
            background = tasks - {writer, reader}
            for task in background:
                task.cancel()
            if background:
                await asyncio.gather(*background, return_exceptions=True)
            released = await session.close()
            await wire.emit(
                {
                    "type": "ended",
                    "code": "stopped",
                    "cleanupStatus": "complete" if released else "pending",
                }
            )
            async with asyncio.timeout(1):
                await wire._outbound.join()
            await socket.close(code=1000, reason="stopped")
        except WebSocketDisconnect:
            terminate_local()
        except Exception as error:
            terminate_local()
            await refuse(error)
        finally:
            terminate_local()
            stopped.set()
            release_pre_auth()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            wire.dispose()
            if session is not None:
                await session.close()

    return router
