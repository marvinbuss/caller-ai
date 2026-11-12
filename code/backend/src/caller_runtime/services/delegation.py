"""Accepted-work records and the bounded speech-fragment correlator.

A `WorkItem` carries one accepted request with its immutable budget and a step counter capped at 8.
`SpeechFragments` joins timed, server-verified input fragments (at most 4 KiB, in order, within 2 s
of the delegation offset) into one request and marks anything out of order or oversized ambiguous
so the model is asked to clarify rather than guess. Everything is in memory, bounded and cleared
with the session; nothing starts work or replays it on construction.
"""

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from caller_runtime.domain.conversation import (
    MAX_WORK_SECONDS,
    SPEECH_WORK_SECONDS,
    WORK_SECONDS,
    TestSlot,
    WorkBudget,
    utc,
)


@dataclass(frozen=True, slots=True)
class WorkReceipt:
    task_id: str
    session_id: str
    owner_epoch: int
    generation: int
    agent_epoch: int
    provider_session_id: str | None
    delegation_id: str | None
    work: WorkBudget


@dataclass(frozen=True, slots=True)
class Acceptance:
    receipt: WorkReceipt
    duplicate: bool


@dataclass(slots=True)
class WorkItem:
    receipt: WorkReceipt
    text: str | None = field(repr=False)
    steps: int = 0
    generation: int = field(init=False)
    agent_epoch: int = field(init=False)
    requires_clarity_check: bool = False

    def __post_init__(self) -> None:
        self.generation, self.agent_epoch = self.receipt.generation, self.receipt.agent_epoch

    @property
    def clarification_required(self) -> bool:
        return self.text is None

    def step(self, now: datetime) -> None:
        self.receipt.work.require(now)
        if self.steps >= 8:
            raise PermissionError("work_step_limit")
        self.steps += 1


class SpeechFragments:
    def __init__(self) -> None:
        self._text = ""
        self._generation: int | None = None
        self._end_ms: float | None = None
        self._ambiguous = False

    @property
    def retained_bytes(self) -> int:
        return len(self._text.encode("utf-8"))

    def clear(self) -> None:
        self._text = ""
        self._generation = None
        self._end_ms = None
        self._ambiguous = False

    def append(
        self, delta: str, *, start_ms: float, end_ms: float, generation: int, accepted: bool
    ) -> None:
        if accepted is not True:
            return
        if self._generation != generation:
            self.clear()
            self._generation = generation
        if self._ambiguous:
            return
        if (
            not isinstance(delta, str)
            or not delta
            or type(generation) is not int
            or not 1 <= generation <= 2**32 - 1
            or any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in (start_ms, end_ms)
            )
            or not 0 <= start_ms <= end_ms <= 3600000
            or self._end_ms is not None
            and start_ms < self._end_ms
            or self.retained_bytes + len(delta.encode("utf-8")) > 4096
        ):
            self._text = ""
            self._ambiguous = True
            return
        self._text += delta
        self._end_ms = end_ms

    def consume(self, *, offset_ms: float, generation: int) -> str | None:
        try:
            if (
                not self._ambiguous
                and self._text.strip()
                and generation == self._generation
                and self._end_ms is not None
                and type(offset_ms) in (int, float)
                and math.isfinite(offset_ms)
                and self._end_ms <= offset_ms <= self._end_ms + 2000
            ):
                return self._text
            return None
        finally:
            self.clear()


class DelegationQueue:
    def __init__(self, *, speech_work_seconds: int = SPEECH_WORK_SECONDS) -> None:
        if (
            type(speech_work_seconds) is not int
            or not WORK_SECONDS <= speech_work_seconds <= MAX_WORK_SECONDS
        ):
            raise ValueError("invalid_speech_work_budget")
        self._speech_work_seconds = speech_work_seconds
        self._seen: dict[str, WorkReceipt] = {}
        self._pending: WorkItem | None = None
        self._executing: WorkItem | None = None
        self._closed = False
        self._history_bytes = 0

    @staticmethod
    def key(task_id: str, provider_session_id: str | None, delegation_id: str | None) -> str:
        source = (
            "text\0" + task_id
            if delegation_id is None
            else "speech\0" + str(provider_session_id) + "\0" + delegation_id
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def lookup(
        self, task_id: str, provider_session_id: str | None = None, delegation_id: str | None = None
    ) -> WorkReceipt | None:
        return self._seen.get(self.key(task_id, provider_session_id, delegation_id))

    def accept(
        self,
        slot: TestSlot,
        *,
        task_id: str,
        text: str | None,
        now: datetime,
        provider_session_id: str | None = None,
        delegation_id: str | None = None,
        requires_clarity_check: bool = False,
    ) -> Acceptance:
        if self._closed:
            raise PermissionError("work_queue_closed")
        for value in (task_id, provider_session_id, delegation_id):
            if value is not None and (
                not isinstance(value, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value)
            ):
                raise PermissionError("invalid_work_input")
        if (
            text is not None
            and (not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 4096)
        ) or (slot.mode == "text" and text is None):
            raise PermissionError("invalid_work_input")
        if slot.mode == "speech":
            live = slot.resources["live"]
            if (
                not provider_session_id
                or not delegation_id
                or live.attempt is None
                or live.attempt.provider_session_id != provider_session_id
                or live.attempt.generation != slot.generation
                or live.attempted == live.settled
            ):
                raise PermissionError("stale_generation")
        elif provider_session_id is not None or delegation_id is not None:
            raise PermissionError("invalid_work_input")
        key = self.key(task_id, provider_session_id, delegation_id)
        if key in self._seen:
            return Acceptance(self._seen[key], True)
        if self._pending is not None:
            raise PermissionError("work_queue_full")
        now = utc(now)
        seconds = WORK_SECONDS if delegation_id is None else self._speech_work_seconds
        if (
            slot.terminal_fence
            or slot.ready_at is None
            or now < (slot.last_user_at or slot.started_at)
            or now >= min(slot.auth_expires_at, slot.expires_at, slot.idle_expires_at)
        ):
            raise PermissionError("conversation_expired")
        receipt = WorkReceipt(
            task_id,
            slot.session_id,
            slot.owner_epoch,
            slot.generation,
            slot.agent_epoch,
            provider_session_id,
            delegation_id,
            WorkBudget(
                now,
                min(
                    now + timedelta(seconds=seconds),
                    slot.auth_expires_at,
                    slot.expires_at,
                    slot.idle_expires_at,
                ),
            ),
        )
        size = len(repr(receipt).encode("utf-8")) + len(key)
        if self._history_bytes + size > 64 * 1024:
            raise PermissionError("work_history_limit")
        self._seen[key] = receipt
        self._history_bytes += size
        self._pending = WorkItem(receipt, text, requires_clarity_check=requires_clarity_check)
        return Acceptance(receipt, False)

    def take(self, *, now: datetime) -> WorkItem | None:
        if self._executing is not None:
            raise PermissionError("work_busy")
        item, self._pending = self._pending, None
        if item is not None:
            item.receipt.work.require(now)
        self._executing = item
        return item

    def finish(self, receipt: WorkReceipt) -> None:
        if self._closed:
            return
        if self._executing is None or self._executing.receipt != receipt:
            raise PermissionError("stale_work")
        self._executing.text = None
        self._executing = None

    @property
    def retained_bytes(self) -> int:
        return self._history_bytes + sum(
            len(item.text.encode("utf-8"))
            for item in (self._pending, self._executing)
            if item is not None and item.text is not None
        )

    def close(self) -> None:
        self._closed = True
        if self._pending is not None:
            self._pending.text = None
        if self._executing is not None:
            self._executing.text = None
        self._pending = self._executing = None
        self._seen.clear()
        self._history_bytes = 0
