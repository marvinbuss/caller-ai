"""Frame-level accounting of the voice relay: sequences, generations, credits and bounds.

The relay forwards browser audio and control frames to the backend and runtime frames back. This
module checks every frame against the same bounds the backend enforces (8 KiB control frames,
1,920-byte PCM frames, capture and playback budgets) so a misbehaving peer is refused by the
relay before anything is forwarded. It holds no content beyond the bounded byte counters.
"""

import hashlib
import json
import re
import struct
from typing import Any

from management_app.api.configuration import unique_object


class RelayLimit(PermissionError):
    """A control or audio frame exceeded its declared bound."""


def parse_control(raw: str) -> dict[str, Any]:
    """Parse one bounded JSON control frame into an object, or refuse it."""
    try:
        if not isinstance(raw, str):
            raise ValueError("invalid_control")
        if len(raw.encode("utf-8")) > 8192:
            raise RelayLimit("control_limit")
        value = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError("object_required")
        json.dumps(value, allow_nan=False)
        return value
    except RelayLimit:
        raise
    except Exception:
        raise PermissionError("invalid_test_control") from None


class RelayFrames:
    def __init__(self, *, mode: str, session_id: str, generation: int) -> None:
        if (
            mode != "speech"
            or not session_id
            or type(generation) is not int
            or not 1 <= generation <= 2**32 - 1
        ):
            raise ValueError("invalid_relay_frames")
        self.mode, self.session_id, self.generation = mode, session_id, generation
        self.input_sequence = 0
        self.output_sequence = 0
        self.sent_sequence = 0
        self.played_sequence = 0
        self.server_sequence = 1
        self.client_sequence = 0
        self._credit = 0
        self._input: dict[int, int] = {}
        self._output: dict[int, int] = {}
        self._seen: dict[int, str] = {}
        self._muted = False
        self._output_blocked = False

    @property
    def capture_bytes(self) -> int:
        return sum(self._input.values())

    @property
    def playback_bytes(self) -> int:
        return sum(self._output.values())

    def _audio(self, raw: bytes) -> tuple[int, int]:
        if len(raw) < 11 or (len(raw) - 9) % 2:
            raise PermissionError("invalid_audio_frame")
        if len(raw) - 9 > 1920:
            raise RelayLimit("audio_frame_limit")
        protocol, generation, sequence = struct.unpack(">BII", raw[:9])
        if protocol != 1 or not sequence:
            raise PermissionError("invalid_audio_frame")
        return generation, sequence

    def from_browser(self, raw: str | bytes) -> bool:
        """Account one browser frame; `False` drops capture of a retired generation that was
        already on the wire when the generation changed."""
        if isinstance(raw, bytes):
            generation, sequence = self._audio(raw)
            if self._muted:
                raise PermissionError("test_input_muted")
            if 1 <= generation < self.generation:
                return False
            if generation != self.generation:
                raise PermissionError("stale_generation")
            if sequence != self.input_sequence + 1:
                raise PermissionError("invalid_audio_sequence")
            count = len(raw) - 9
            if count // 2 > self._credit or self.capture_bytes + count > 12000:
                raise PermissionError("capture_credit_exhausted")
            self._credit -= count // 2
            self.input_sequence = sequence
            self._input[sequence] = count
            return True
        value = parse_control(raw)
        kind = value.get("type")
        fields = {
            "stop": set(),
            "ping": {"nonce"},
            "pong": {"nonce"},
            "input.mute": {"muted"},
            "output.interrupt": {"generation"},
            "audio.played": {"generation", "audioSequence"},
        }
        if (
            not isinstance(kind, str)
            or kind not in fields
            or set(value) != {"type", "sessionId", "sequence"} | fields[kind]
            or value["sessionId"] != self.session_id
            or type(value["sequence"]) is not int
            or not 1 <= value["sequence"] <= 2**32 - 1
        ):
            raise PermissionError("invalid_test_control")
        digest = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        sequence = value["sequence"]
        if sequence in self._seen:
            if self._seen[sequence] != digest:
                raise PermissionError("invalid_test_sequence")
            return True
        if sequence != self.client_sequence + 1:
            raise PermissionError("invalid_test_sequence")
        if kind in {"output.interrupt", "audio.played"} and (
            type(value["generation"]) is not int or not 1 <= value["generation"] <= self.generation
        ):
            raise PermissionError("stale_generation")
        if kind == "audio.played":
            played = value["audioSequence"]
            if type(played) is not int or played < 0:
                raise PermissionError("invalid_played_ack")
            # An ack of a retired generation was in flight when it changed; it frees nothing.
            if value["generation"] == self.generation:
                if not self.played_sequence <= played <= self.sent_sequence:
                    raise PermissionError("invalid_played_ack")
                self.played_sequence = played
                self._output = {key: count for key, count in self._output.items() if key > played}
        elif kind == "input.mute":
            if type(value["muted"]) is not bool:
                raise PermissionError("invalid_test_control")
            self._muted = value["muted"]
        elif kind == "stop" or (
            kind == "output.interrupt" and value["generation"] == self.generation
        ):
            self._output_blocked = True
        elif kind in {"ping", "pong"}:
            if (
                not isinstance(value["nonce"], str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["nonce"]) is None
            ):
                raise PermissionError("invalid_test_control")
        self.client_sequence = sequence
        self._seen[sequence] = digest
        if len(self._seen) > 64:
            self._seen.pop(next(iter(self._seen)))
        return True

    def from_runtime(self, raw: str | bytes) -> bool:
        if isinstance(raw, bytes):
            generation, sequence = self._audio(raw)
            if generation < self.generation:
                return False
            if self._output_blocked and generation == self.generation:
                return False
            if generation != self.generation or sequence != self.output_sequence + 1:
                raise PermissionError("invalid_audio_sequence")
            count = len(raw) - 9
            if self.playback_bytes + count > 24000:
                raise PermissionError("playback_credit_exhausted")
            self.output_sequence = sequence
            self._output[sequence] = count
            return True
        value = parse_control(raw)
        if (
            value.get("sessionId") != self.session_id
            or type(value.get("sequence")) is not int
            or not self.server_sequence < value["sequence"] <= 2**32 - 1
            or type(value.get("generation")) is not int
            or not 1 <= value["generation"] <= 2**32 - 1
        ):
            raise PermissionError("invalid_test_sequence")
        kind = value.get("type")
        if kind in {"generation.changed", "agent.changed"}:
            fields = {"reason"} if kind == "generation.changed" else {"agentId", "communityId"}
            if (
                set(value) != {"type", "sessionId", "sequence", "generation"} | fields
                or value["generation"] <= self.generation
            ):
                raise PermissionError("invalid_test_generation")
            if kind == "generation.changed" and value["reason"] not in {
                "segment_retired",
                "agent_changed",
                "interrupted",
                "stopped",
            }:
                raise PermissionError("invalid_test_generation")
            if kind == "agent.changed" and (
                not isinstance(value["agentId"], str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value["agentId"]) is None
            ):
                raise PermissionError("invalid_test_control")
            if kind == "agent.changed" and value["communityId"] is not None:
                if (
                    not isinstance(value["communityId"], str)
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value["communityId"])
                    is None
                ):
                    raise PermissionError("invalid_test_control")
            self.generation = value["generation"]
            self.input_sequence = self.output_sequence = self.sent_sequence = (
                self.played_sequence
            ) = 0
            self._input.clear()
            self._output.clear()
            self._credit = 0
            self._output_blocked = False
        elif kind == "audio.credit":
            if (
                set(value)
                != {
                    "type",
                    "sessionId",
                    "sequence",
                    "generation",
                    "availableSamples",
                    "acceptedAudioSequence",
                }
                or value["generation"] != self.generation
                or type(value["availableSamples"]) is not int
                or not 0 <= value["availableSamples"] <= 6000
                or type(value["acceptedAudioSequence"]) is not int
                or not 0 <= value["acceptedAudioSequence"] <= self.input_sequence
            ):
                raise PermissionError("invalid_audio_credit")
            self._input = {
                key: count
                for key, count in self._input.items()
                if key > value["acceptedAudioSequence"]
            }
            self._credit = max(0, value["availableSamples"] - self.capture_bytes // 2)
        else:
            raise PermissionError("invalid_test_control")
        self.server_sequence = value["sequence"]
        return True

    def sent_audio(self, sequence: int, *, generation: int) -> None:
        if generation != self.generation:
            return
        if (
            type(sequence) is not int
            or sequence != self.sent_sequence + 1
            or sequence > self.output_sequence
        ):
            raise PermissionError("invalid_audio_sequence")
        self.sent_sequence = sequence

    def clear(self) -> None:
        self._input.clear()
        self._output.clear()
        self._seen.clear()
        self._credit = 0
        self._output_blocked = True
