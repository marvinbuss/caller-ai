"""Runs one conversation's work: tool dispatch, handovers, the shared history and ordered cleanup.

`ConversationExecutor` takes accepted text or Live delegations from a bounded queue, runs the
pinned agents through the SDK and returns results to the text or speech caller. Every model step
is built from the conversation's `SharedHistory` plus the current work item's own items, so the
agent that continues after a handover sees the whole conversation (feature 007). Every external
step first passes an admission gate, so a fenced or expired session stops before dispatch.
Failures surface as fixed codes; content stays in memory bounded by the context limit and is
dropped on close. `close` releases resources in a fixed order and reports whether cleanup is
verified.
"""

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Literal, cast
from uuid import uuid4

from agents import Agent, FunctionTool, Handoff, RunConfig, RunContextWrapper, Runner
from agents.result import RunResultStreaming
from agents.run_config import ToolExecutionConfig
from jsonschema import Draft202012Validator

from caller_runtime.adapters.capability_client import ConversationCapabilityClient
from caller_runtime.adapters.foundry_text import FoundryTextAdapter
from caller_runtime.adapters.gpt_live import GptLiveSession
from caller_runtime.adapters.strict_json import unique_object
from caller_runtime.domain.conversation import (
    SPEECH_WORK_SECONDS,
    Metadata,
    TestSlot,
    WorkBudget,
)
from caller_runtime.domain.shared_history import (
    HISTORY_LIMIT,
    Handover,
    HistoryEntry,
    SharedHistory,
    bounded,
)
from caller_runtime.observability import Telemetry
from caller_runtime.services import condensing
from caller_runtime.services.admission import AdmissionError
from caller_runtime.services.cleanup import CleanupObservations, ResourceCleanupVerifier
from caller_runtime.services.conversation_agents import (
    Mode,
    agent_instructions,
    build_agents,
    destination_handoff,
    handoff_schema,
)
from caller_runtime.services.conversation_policy import (
    REFUSALS,
    ConversationPolicy,
    SpeechSegments,
)
from caller_runtime.services.delegation import (
    Acceptance,
    DelegationQueue,
    SpeechFragments,
    WorkItem,
)
from caller_runtime.services.delegation_update import changed, delegation_update
from caller_runtime.services.greeting import (
    GREETING_TARGET_SECONDS,
    TEXT_FALLBACK_GREETING,
    TEXT_GREETING_INPUT,
    TEXT_GREETING_SECONDS,
    VOICE_GREETING_INPUT,
)
from caller_runtime.services.handover_rules import Destination
from caller_runtime.services.speech_parts import QUIET_HANDOVER, dropped_note, split_reply
from caller_runtime.settings import (
    REASONING_EFFORT,
    TRANSCRIPTION_WAIT_SECONDS,
    VOICE_DELEGATION_UPDATES,
    ReasoningEffort,
)

# The caller hears this only when work fails before the agent reported a final state.
WORK_FAILED_LINE = "I could not finish that, please try again."
# `report_progress` (FR-018-FR-025): the states, the ones that end the work and the ones that
# enter history, the still-working repeat interval and the rounds one item may spend on updates.
PROGRESS_STATES = (
    "still_working",
    "waiting_for_input",
    "completed",
    "failed",
    "cancellation_confirmed",
)
FINAL_STATES = frozenset({"completed", "failed", "cancellation_confirmed"})
HISTORY_STATES = FINAL_STATES | {"waiting_for_input"}
STILL_WORKING_REPEAT_SECONDS = 10.0
PROGRESS_ROUNDS = 6
PROGRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": list(PROGRESS_STATES)},
        "message": {"type": "string", "minLength": 1, "maxLength": 200},
    },
    "required": ["state", "message"],
    "additionalProperties": False,
}
# A spoken part waits this long for the previous part's acknowledgement.
PART_ACK_SECONDS = 2.0
# Parts stop this close to the end of the work budget, so a long reply never fails the item.
PART_MARGIN_SECONDS = 0.25
# One work item failing within its own budget; the session and its slot stay usable.
# Failures bounded to one work item: the session continues and the item's entries are kept. The
# step limit is one of them, so a refused handover at the limit stays recorded (feature 007).
BOUNDED_WORK_FAILURES = frozenset(
    {"model_unavailable", "work_deadline_exceeded", "work_busy", "work_step_limit"}
)
# Cleanup retried once a detached model request has finished, held strongly until done.
_LATE_CLEANUPS: set[asyncio.Task[None]] = set()


class TaskClarity(Metadata):
    unambiguous: bool


class HandoverRefused(Exception):
    """Raised from a handoff callback so the run stops; the requesting agent hears the reason."""

    def __init__(self, destination: Destination, record: Handover, arguments: str) -> None:
        super().__init__(record.reason)
        self.destination, self.record, self.arguments = destination, record, arguments


@dataclass(slots=True)
class StepActions:
    """What one model step did: at most one tool call or handoff (`called`)."""

    called: bool = False
    reported: bool = False
    applied: tuple[Destination, Handover, str] | None = None


@dataclass(slots=True)
class ProgressState:
    """Progress of one work item: the rate limit and whether a final state came."""

    last_state: str | None = None
    last_still_working_at: datetime | None = None
    final_reported: bool = False
    rounds: int = 0


def model_items(items: list[Any]) -> list[Any]:
    """The SDK's input items without reasoning items, which never enter model input (FR-005)."""
    return [
        item for item in items if not (isinstance(item, dict) and item.get("type") == "reasoning")
    ]


class ConversationExecutor:
    def __init__(
        self,
        *,
        policy: ConversationPolicy,
        text_adapter: FoundryTextAdapter,
        utc_now: Callable[[], datetime],
        capability_client: ConversationCapabilityClient | None = None,
        speech_segments: SpeechSegments | None = None,
        cleanup_observations: CleanupObservations | None = None,
        telemetry: Telemetry | None = None,
        speech_work_seconds: int = SPEECH_WORK_SECONDS,
        reasoning_effort: ReasoningEffort = REASONING_EFFORT,
        transcription_wait_seconds: float = TRANSCRIPTION_WAIT_SECONDS,
        delegation_updates: bool = VOICE_DELEGATION_UPDATES,
    ) -> None:
        self.policy, self.text_adapter, self.utc_now = policy, text_adapter, utc_now
        self.capability_client = capability_client
        self.speech_segments = speech_segments
        self.reasoning_effort = reasoning_effort
        self.transcription_wait_seconds = transcription_wait_seconds
        self.delegation_updates = delegation_updates
        # The reachable set the last delegation update described (R5).
        self._update_destinations: tuple[str, ...] | None = None
        self._update_watches: set[asyncio.Task[None]] = set()
        self.queue = DelegationQueue(speech_work_seconds=speech_work_seconds)
        self._current_item: WorkItem | None = None
        self._progress: dict[str, ProgressState] = {}
        self._late_cleanup: asyncio.Task[None] | None = None
        self.speech_fragments = SpeechFragments()
        self._accepting, self._processing = asyncio.Lock(), asyncio.Lock()
        self._leaving = asyncio.Lock()
        # Voice orders entries by session time; text by insertion (contracts/shared-history.md).
        self.history = SharedHistory(timeline=policy.slot.mode == "speech")
        # The running work item's entries; they join the history when the item ends.
        self._item_entries: list[HistoryEntry] = []
        # Transcript deltas of the turn or response in progress: (speaker, first start_ms, text).
        self._open_speech: tuple[str, float, list[str]] | None = None
        # How far the caller's transcript has come, for the transcription wait (FR-024).
        self._caller_end_ms = -1.0
        self._caller_heard = asyncio.Event()
        self._failed = False
        self._speech: dict[str, GptLiveSession] = {}
        self._speaking = False
        self._interrupted = asyncio.Event()
        self._cleanup_observations = cleanup_observations
        self._telemetry = telemetry
        # One correlation id per session for its events (FR-018).
        self._cleanup_correlation = uuid4().hex
        # Per running item: the first handling role, applied handovers, and the handoff whose
        # destination has not produced output yet (when, path), for `handover_seconds`.
        self._first_role: str | None = None
        self._handed_on = False
        self._handover_started: tuple[datetime, str] | None = None
        # Whether each accepted voice delegation was unambiguous, reported when its item ends.
        self._delegation_ready: dict[str, bool] = {}
        self._reported_cleanup: bool | None = None
        self._closing = asyncio.Lock()
        self._cleanup_result: bool | None = None
        self._processing_task: asyncio.Task[Any] | None = None
        self._runs: list[RunResultStreaming] = []
        self._sdk_drained = True
        self._prepare_attempted = False
        self._owned_live: tuple[GptLiveSession, ...] = ()
        if cleanup_observations is not None:
            cleanup_observations.register(policy.slot, disposed=self._disposal_ready)
        if self._telemetry is not None and policy.slot.ready_at is not None:
            self._telemetry.event("test.session", "success", mode=policy.slot.mode, reason="ready")

    async def prepare(self) -> None:
        if self._failed or self._prepare_attempted or self._processing.locked():
            raise PermissionError("test_runtime_unavailable")
        self._prepare_attempted = True
        async with self._processing:
            self._processing_task = asyncio.current_task()
            try:
                slot = self.policy.slot
                if (
                    slot.ready_at is not None
                    or self.policy.active_agent_id
                    != self.policy.visibility.lifecycle_target("welcome")
                ):
                    raise PermissionError("test_runtime_unavailable")
                work = WorkBudget(
                    slot.started_at,
                    min(
                        slot.admission_deadline_utc,
                        slot.auth_expires_at,
                        slot.expires_at,
                        slot.idle_expires_at,
                    ),
                )
                await self.policy.admission.check_work(
                    slot.session_id, slot.owner_epoch, generation=slot.generation, work=work
                )
                welcome = next(
                    agent
                    for agent in self.policy.snapshot.for_runtime()["agents"]
                    if agent["agentId"] == slot.active_agent_id
                )
                async with asyncio.timeout(work.remaining(self.utc_now())):
                    await self.text_adapter.ready(slot, work, instructions=welcome["prompt"])
                await self.policy.admission.check_work(
                    slot.session_id, slot.owner_epoch, generation=slot.generation, work=work
                )
            finally:
                self._processing_task = None

    async def greeting_text(self, mode: Mode, work: WorkBudget) -> str:
        """Ask the pinned Welcome agent for this modality's greeting; cancelling abandons it."""
        slot = self.policy.slot
        if self._failed or mode != slot.mode:
            raise PermissionError("mode_mismatch")
        welcome = next(
            agent
            for agent in self.policy.snapshot.for_runtime()["agents"]
            if agent["agentId"] == self.policy.visibility.lifecycle_target("welcome")
        )
        return await self.text_adapter.greeting(
            slot,
            work,
            instructions=agent_instructions(welcome, mode, self.policy.snapshot.for_runtime()),
            prompt=VOICE_GREETING_INPUT if mode == "speech" else TEXT_GREETING_INPUT,
        )

    async def open_text(
        self, emit: Callable[[dict[str, Any]], Awaitable[None]], *, started: float
    ) -> None:
        """Stream the opening greeting of a new text conversation and keep it as its first entry.

        `started` is the loop time the open request arrived. Without a complete model greeting
        by `TEXT_GREETING_SECONDS` after it, the request is cancelled and the fixed fallback is
        streamed instead. Welcome then hands over to Routing (FR-030).
        """
        slot = self.policy.slot
        if self._failed or slot.mode != "text" or self.history.entries:
            raise PermissionError("mode_mismatch")
        loop = asyncio.get_running_loop()
        now = self.utc_now()
        work = WorkBudget(
            now,
            min(
                now + timedelta(seconds=TEXT_GREETING_SECONDS),
                slot.auth_expires_at,
                slot.expires_at,
                slot.idle_expires_at,
            ),
        )
        reason = "ready"
        try:
            async with asyncio.timeout_at(started + TEXT_GREETING_SECONDS):
                greeting = await self.greeting_text("text", work)
        except Exception:
            greeting, reason = TEXT_FALLBACK_GREETING, "fallback"
        await self.policy.admission.check_output(
            slot.session_id, slot.owner_epoch, generation=slot.generation
        )
        message_id = "greeting-" + uuid4().hex
        for offset in range(0, len(greeting), 512):
            await emit(
                {
                    "type": "text.delta",
                    "messageId": message_id,
                    "text": greeting[offset : offset + 512],
                    "generation": slot.generation,
                }
            )
            if offset == 0 and self._telemetry is not None:
                seconds = loop.time() - started
                late = seconds > GREETING_TARGET_SECONDS
                self._telemetry.event(
                    "test.greeting",
                    "failure" if late else "success",
                    mode="text",
                    reason="deadline" if late else reason,
                    measurement="greeting_seconds",
                    value=max(0.0, seconds),
                )
        await emit(
            {
                "type": "text.completed",
                "messageId": message_id,
                "text": "",
                "generation": slot.generation,
            }
        )
        self.history.record_agent_answer(greeting, agent_id=slot.active_agent_id)
        await self.leave_welcome(emit)

    def _budget(self, seconds: float = 10) -> WorkBudget:
        now = self.utc_now()
        slot = self.policy.slot
        return WorkBudget(
            now,
            min(
                now + timedelta(seconds=seconds),
                slot.auth_expires_at,
                slot.expires_at,
                slot.idle_expires_at,
            ),
        )

    async def _agent_changed(self, emit: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """The Test page shows the active agent in its metadata only (FR-026)."""
        await emit(
            {
                "type": "agent.changed",
                "agentId": self.policy.active_agent_id,
                "communityId": self.policy.active_community_id,
                "generation": self.policy.slot.generation,
            }
        )
        if self.speech_segments is not None:
            try:
                await self.send_delegation_update()
            except Exception:
                self._update_unavailable()

    def _update_unavailable(self) -> None:
        if self._telemetry is not None:
            self._telemetry.event("test.session", "failure", mode="speech", reason="unavailable")

    async def send_delegation_update(self) -> None:
        """Tell the voice assistant what it may delegate now, if that changed (R5)."""
        if not self.delegation_updates or self.speech_segments is None or self._failed:
            return
        communities = changed(self.policy.snapshot, self.policy.slot, self._update_destinations)
        if communities is None:
            return
        segment = self.speech_segments.current
        sent = [
            await segment.append_instructions(part)
            for part in delegation_update(self.policy.snapshot, self.policy.slot, communities)
        ]
        self._update_destinations = communities

        async def watch() -> None:
            # A missing acknowledgement is logged but never ends the session.
            if not all([await segment.acknowledged(event, within=5.0) for event in sent]):
                self._update_unavailable()

        task = asyncio.create_task(watch())
        self._update_watches.add(task)
        task.add_done_callback(self._update_watches.discard)

    async def leave_welcome(
        self,
        emit: Callable[[dict[str, Any]], Awaitable[None]] | None,
        work: WorkBudget | None = None,
    ) -> bool:
        """Apply the Welcome → Routing move (`lifecycle`, R7) once; later calls do nothing."""
        routing = self.policy.visibility.lifecycle_target("routing")
        welcome = self.policy.visibility.lifecycle_target("welcome")
        async with self._leaving:
            if self._failed or self.policy.active_agent_id != welcome:
                return False
            record = await self.policy.handover(routing, "lifecycle", work or self._budget())
        self._handover_event(record)
        if emit is not None:
            await self._agent_changed(emit)
        return True

    def _event(
        self,
        name: str,
        outcome: str,
        *,
        reason: str,
        measurement: str | None = None,
        value: float | None = None,
        fields: Mapping[str, str | bool] | None = None,
    ) -> None:
        """A content-free event of this session, with its correlation id (FR-018)."""
        if self._telemetry is not None:
            self._telemetry.event(
                name,
                outcome,
                correlation_id=self._cleanup_correlation,
                mode=self.policy.slot.mode,
                reason=reason,
                measurement=measurement,
                value=value,
                fields=fields,
            )

    def _role(self, agent_id: str) -> str:
        for agent in self.policy.snapshot.for_runtime()["agents"]:
            if agent["agentId"] == agent_id:
                return str(agent["role"])
        return "ordinary"

    def _handover_event(self, record: Handover) -> None:
        """`test.handover` for an applied or refused handover, and `test.history` at an applied
        one with the size of the history the destination continues with (FR-019)."""
        applied = record.outcome == "applied"
        self._event(
            "test.handover",
            "success" if applied else "failure",
            reason=record.reason,
            fields={
                "source_role": record.source_role,
                "destination_role": record.destination_role,
                "path": record.path,
            },
        )
        if applied:
            size = self.history.rendered_bytes + self._context_bytes(
                [item for entry in self._item_entries for item in entry.items()]
            )
            self._event(
                "test.history",
                "success",
                reason="ready",
                fields={"history_kib": condensing.kib_range(size)},
            )

    def restore_history(self, entries: Iterable[HistoryEntry]) -> None:
        """Seed a fresh text session with the owner's stored entries before the first message."""
        if self.history.entries or self.policy.slot.mode != "text":
            raise PermissionError("mode_mismatch")
        for entry in entries:
            self.history.record(entry)
        if self.history.rendered_bytes > HISTORY_LIMIT:
            self.history.clear()
            raise PermissionError("conversation_context_limit")

    async def submit_text(self, task_id: str, text: str) -> Acceptance:
        async with self._accepting:
            if self._failed or self.policy.slot.mode != "text":
                raise PermissionError("mode_mismatch")
            if self.retained_bytes + len(text.encode("utf-8")) + 256 > HISTORY_LIMIT:
                raise PermissionError("conversation_context_limit")
            slot = await self.policy.admission.check_output(
                self.policy.slot.session_id,
                self.policy.slot.owner_epoch,
                generation=self.policy.slot.generation,
            )
            try:
                accepted = self.queue.accept(slot, task_id=task_id, text=text, now=self.utc_now())
            except PermissionError:
                if self._telemetry is not None:
                    self._telemetry.event(
                        "test.denial", "failure", mode="text", reason="invalid_input"
                    )
                raise
            if not accepted.duplicate:
                try:
                    await self.policy.admission.user_activity(
                        slot.session_id, slot.owner_epoch, generation=slot.generation, mode="text"
                    )
                except Exception:
                    self._failed = True
                    self.queue.close()
                    raise PermissionError("conversation_unavailable") from None
                if self._telemetry is not None:
                    self._telemetry.event("test.work", "success", mode="text", reason="ready")
            return accepted

    async def submit_delegation(
        self,
        segment: GptLiveSession,
        event: dict[str, Any],
        *,
        task_text: str | None,
        unambiguous: bool,
        requires_clarity_check: bool = False,
    ) -> Acceptance:
        async with self._accepting:
            if (
                self._failed
                or self.policy.slot.mode != "speech"
                or type(unambiguous) is not bool
                or type(requires_clarity_check) is not bool
            ):
                raise PermissionError("mode_mismatch")
            try:
                if (
                    event["type"] != "session.delegation.created"
                    or event["delegation"]["target"] != "client"
                ):
                    raise ValueError("invalid_delegation")
                delegation_id = event["delegation"]["id"]
                offset_ms = float(event["offset_ms"])
                if not math.isfinite(offset_ms) or not 0 <= offset_ms <= 3600000:
                    raise ValueError("invalid_delegation")
                segment.protocol.context_event(delegation_id, "Please clarify your request.")
            except Exception:
                raise PermissionError("invalid_delegation") from None
            slot = await self.policy.admission.check_output(
                self.policy.slot.session_id,
                self.policy.slot.owner_epoch,
                generation=self.policy.slot.generation,
            )
            task_id = (
                "speech-"
                + hashlib.sha256(
                    (segment.provider_session_id + "\0" + delegation_id).encode()
                ).hexdigest()
            )
            accepted = self.queue.accept(
                slot,
                task_id=task_id,
                text=task_text if unambiguous or requires_clarity_check else None,
                now=self.utc_now(),
                provider_session_id=segment.provider_session_id,
                delegation_id=delegation_id,
                requires_clarity_check=requires_clarity_check,
                offset_ms=offset_ms,
            )
            if not accepted.duplicate:
                # A delegation ends the voice assistant's response before it; the caller's turn
                # stays open, its transcript may still be catching up (FR-024).
                if self._open_speech is not None and self._open_speech[0] == "assistant":
                    self.flush_speech()
                self._speech[task_id] = segment
                # Editors can switch the quiet update off; a missing setting means on (FR-017).
                quiet = self.policy.snapshot.conversation_settings().get("quietHandover", True)
                try:
                    # A quiet update tells the speaking model the work is under way (FR-016).
                    if quiet is not False:
                        async with asyncio.timeout(1):
                            await segment.append_context(delegation_id, QUIET_HANDOVER, quiet=True)
                except Exception:
                    pass
                self._delegation_ready[task_id] = unambiguous
            return accepted

    async def accept_transcript(
        self, event: dict[str, Any], *, generation: int, input_audio_accepted: bool
    ) -> None:
        if (
            self._failed
            or self.policy.slot.mode != "speech"
            or type(input_audio_accepted) is not bool
        ):
            raise PermissionError("invalid_transcript")
        try:
            if (
                set(event) - {"event_id"} != {"type", "delta", "start_ms", "end_ms"}
                or event["type"]
                not in {"session.input_transcript.delta", "session.output_transcript.delta"}
                or not isinstance(event["delta"], str)
                or len(event["delta"].encode("utf-8")) > 8192
                or type(generation) is not int
                or generation != self.policy.slot.generation
                or any(
                    type(event[key]) not in (int, float) or not math.isfinite(event[key])
                    for key in ("start_ms", "end_ms")
                )
                or not 0 <= event["start_ms"] <= event["end_ms"] <= 3600000
            ):
                raise ValueError("invalid_transcript")
        except Exception:
            raise PermissionError("invalid_transcript") from None
        await self.policy.admission.check_output(
            self.policy.slot.session_id, self.policy.slot.owner_epoch, generation=generation
        )
        speaker = "caller" if event["type"] == "session.input_transcript.delta" else "assistant"
        if self.retained_bytes + len(event["delta"].encode("utf-8")) + 256 > HISTORY_LIMIT:
            self._failed = True
            self._purge()
            try:
                async with asyncio.timeout(1):
                    await self.policy.admission.mark_terminal(
                        self.policy.slot.session_id, self.policy.slot.owner_epoch
                    )
            except Exception:
                pass
            raise PermissionError("transcript_limit")
        if speaker == "caller" and input_audio_accepted and event["delta"].strip():
            await self.policy.admission.user_activity(
                self.policy.slot.session_id,
                self.policy.slot.owner_epoch,
                generation=generation,
                mode="speech",
            )
        # One entry per caller turn or assistant response: deltas merge until the speaker changes.
        if self._open_speech is not None and self._open_speech[0] != speaker:
            self.flush_speech()
        if self._open_speech is None:
            self._open_speech = (speaker, float(event["start_ms"]), [])
        self._open_speech[2].append(event["delta"])
        if speaker == "caller":
            self._caller_end_ms = max(self._caller_end_ms, float(event["end_ms"]))
            self._caller_heard.set()

    async def _await_transcript(self, item: WorkItem) -> None:
        """Wait up to the transcription wait for the caller's transcript to reach the delegation
        (FR-024), then record the caller's speech up to it, `partial` if it did not catch up."""
        if item.offset_ms is None:
            return
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + self.transcription_wait_seconds
        while self._caller_end_ms < item.offset_ms:
            remaining = min(deadline - loop.time(), item.receipt.work.remaining(self.utc_now()))
            if remaining <= 0:
                item.untranscribed = True
                break
            self._caller_heard.clear()
            try:
                async with asyncio.timeout(remaining):
                    await self._caller_heard.wait()
            except TimeoutError:
                item.untranscribed = self._caller_end_ms < item.offset_ms
                break
        if self._telemetry is not None:
            self._telemetry.event(
                "test.latency",
                "success",
                mode="speech",
                reason="ready",
                measurement="transcription_wait_seconds",
                value=max(0.0, loop.time() - started),
            )
        if self._open_speech is not None and self._open_speech[1] <= item.offset_ms:
            # Speech up to the delegation is recorded; later speech is still in progress.
            unheard = self._open_speech[0] == "caller" and item.untranscribed
            self.flush_speech(delivery="partial" if unheard else "transcribed")

    def flush_speech(self, *, delivery: Literal["transcribed", "partial"] = "transcribed") -> None:
        """Record the transcript of the turn or response in progress as one entry (FR-003)."""
        if self._open_speech is None:
            return
        speaker, at_ms, parts = self._open_speech
        self._open_speech = None
        content = "".join(parts).strip()
        if not content:
            return
        if speaker == "caller":
            self.history.record_caller_speech(content, at_ms=at_ms, delivery=delivery)
        else:
            self.history.record_assistant_speech(content, at_ms=at_ms)

    def record_greeting(self, text: str, *, at_ms: float = 0.0) -> None:
        """Keep the greeting text given to the voice assistant before its transcript (FR-002)."""
        if self.policy.slot.mode == "speech" and text.strip() and not self._failed:
            self.history.record_delegation_result(
                text, at_ms=at_ms, agent_id=self.policy.active_agent_id
            )

    async def _gate(self, item: WorkItem, *, initial: bool = False) -> TestSlot:
        if self._failed:
            raise PermissionError("work_queue_closed")
        item.receipt.work.require(self.utc_now())
        for _attempt in range(2):
            slot = self.policy.slot
            try:
                current = await self.policy.admission.check_output(
                    slot.session_id, slot.owner_epoch, generation=slot.generation
                )
                break
            except AdmissionError as error:
                # This executor's own tool transition can land while the read is in flight.
                if error.code != "stale_generation" or await self.policy.settled() is slot:
                    raise
        else:
            raise PermissionError("stale_generation")
        if (
            initial
            and current.mode == "speech"
            and item.receipt.generation == current.generation == item.generation
            and item.receipt.owner_epoch == current.owner_epoch
            and item.agent_epoch < current.agent_epoch
        ):
            # A delegation accepted before an agent change has produced nothing yet: it is the
            # caller's request and runs on the active agent with the whole history (FR-007).
            item.agent_epoch = current.agent_epoch
        if item.generation != current.generation or item.agent_epoch != current.agent_epoch:
            raise PermissionError("stale_generation")
        if initial and (
            item.receipt.generation != current.generation
            or item.agent_epoch != current.agent_epoch
            or item.receipt.owner_epoch != current.owner_epoch
        ):
            raise PermissionError("stale_generation")
        return current

    def _tool(
        self,
        name: str,
        properties: dict[str, Any],
        item: WorkItem,
        source: TestSlot,
        actions: StepActions,
        *,
        tool_id: str,
        action_id: str,
    ) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

        async def invoke(context: RunContextWrapper[Any], arguments: str) -> str:
            await self._gate(item)
            if (
                actions.called
                or self.policy.agent_epoch != source.agent_epoch
                or self.policy.active_agent_id != source.active_agent_id
            ):
                raise PermissionError("stale_agent_epoch")
            if len(arguments.encode("utf-8")) > 8192:
                raise PermissionError("invalid_work_input")
            values = json.loads(arguments, object_pairs_hook=unique_object)
            if not isinstance(values, dict) or not Draft202012Validator(schema).is_valid(values):
                raise PermissionError("invalid_work_input")
            actions.called = True
            item.step(self.utc_now())
            if self.capability_client is None:
                raise PermissionError("capability_unavailable")
            call_id = getattr(context, "tool_call_id", None) or "call_" + uuid4().hex
            try:
                result = await self.policy.capability(
                    self.capability_client,
                    tool_id,
                    action_id,
                    values["inputs"],
                    item.receipt.work,
                    source_agent_id=source.active_agent_id,
                    source_agent_epoch=source.agent_epoch,
                )
            except Exception as error:
                # A failed call is kept too, so later agents do not repeat it blindly (FR-002).
                code = error.args[0] if error.args and isinstance(error.args[0], str) else "error"
                self._record_tool(item, name, call_id, arguments, "failed: " + code)
                raise
            output = json.dumps(result, allow_nan=False, separators=(",", ":"))
            self._record_tool(item, name, call_id, arguments, output)
            await self._gate(item)
            return output

        return FunctionTool(
            name=name,
            description="Published-policy " + name.replace("_", " "),
            params_json_schema=schema,
            on_invoke_tool=invoke,
            strict_json_schema=False,
        )

    def _entry_fields(self, item: WorkItem) -> dict[str, Any]:
        """Where a work item's entry belongs: its agent, community and session time."""
        return {
            "agent_id": self.policy.active_agent_id,
            "community_id": self.policy.active_community_id,
            "at_ms": item.offset_ms if self.history.timeline else None,
        }

    def _record_item(self, item: WorkItem, kind: str, content: str, **fields: Any) -> None:
        if item is not self._current_item:
            return
        self._item_entries.append(
            HistoryEntry.model_validate(
                {"kind": kind, "content": bounded(content)} | self._entry_fields(item) | fields
            )
        )

    def _record_tool(
        self, item: WorkItem, name: str, call_id: str, arguments: str, output: str
    ) -> None:
        """Record one function tool call and its output, paired by `call_id`."""
        self._record_item(item, "tool_call", arguments, name=name, call_id=call_id)
        self._record_item(item, "tool_result", output, name=name, call_id=call_id)

    def _tools(
        self,
        configured: dict[str, Any],
        item: WorkItem,
        source: TestSlot,
        actions: StepActions,
        *,
        capabilities: bool,
        emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> list[FunctionTool]:
        if configured["agentId"] != source.active_agent_id:
            return []
        role = configured["role"]
        if capabilities:
            if role != "ordinary" or self.capability_client is None:
                return []
            return [
                self._tool(
                    "public_"
                    + hashlib.sha256((grant["toolId"] + ":" + action).encode()).hexdigest()[:32],
                    {"inputs": {"type": "object"}},
                    item,
                    source,
                    actions,
                    tool_id=grant["toolId"],
                    action_id=action,
                )
                for grant in configured["capabilityAssignments"]
                for action in grant["permittedActionNames"]
            ]
        return [self._progress_tool(item, actions, emit)] if emit is not None else []

    def _handoffs(
        self,
        configured: dict[str, Any],
        agents: dict[str, Agent[Any]],
        *,
        item: WorkItem,
        source: TestSlot,
        actions: StepActions,
    ) -> list[Handoff[Any, Any]]:
        """The active agent's SDK handoffs, one per permitted destination (FR-008, FR-028)."""
        if configured["agentId"] != source.active_agent_id:
            return []
        return [
            destination_handoff(
                destination,
                agents[destination.agent_id],
                enabled=self.policy.enabled(destination),
                invoke=partial(self._apply_handoff, destination, item, source, actions),
            )
            for destination in self.policy.offered()
        ]

    async def _apply_handoff(
        self,
        destination: Destination,
        item: WorkItem,
        source: TestSlot,
        actions: StepActions,
        arguments: str | None,
    ) -> None:
        """Apply a handoff the model requested; a refusal raises `HandoverRefused` (FR-008)."""
        text = arguments or "{}"
        community_id: str | None = None
        try:
            values = (
                json.loads(text, object_pairs_hook=unique_object)
                if len(text.encode("utf-8")) <= 8192
                else None
            )
        except ValueError:
            values = None
        try:
            if not isinstance(values, dict) or not Draft202012Validator(
                handoff_schema(destination)
            ).is_valid(values):
                raise PermissionError("handover_not_allowed")
            community_id = values.get("community_id")
            await self._gate(item)
            if (
                actions.called
                or self.policy.agent_epoch != source.agent_epoch
                or self.policy.active_agent_id != source.active_agent_id
            ):
                raise PermissionError("stale_agent_epoch")
            actions.called = True
            item.step(self.utc_now())
            record = await self.policy.handover(
                destination.agent_id,
                destination.path,
                item.receipt.work,
                community_id=community_id,
                source_agent_id=source.active_agent_id,
                source_agent_epoch=source.agent_epoch,
            )
        except (PermissionError, AdmissionError) as error:
            code = error.args[0] if error.args else None
            reason = REFUSALS.get(code) if isinstance(code, str) else None
            if reason is None:
                raise
            refused = self.policy.record(
                destination.agent_id, destination.path, community_id=community_id, reason=reason
            )
            self._handover_event(refused)
            raise HandoverRefused(destination, refused, text) from None
        item.generation = self.policy.slot.generation
        item.agent_epoch = self.policy.agent_epoch
        actions.applied = (destination, record, text)
        self._handed_on = True
        self._handover_started = (self.utc_now(), destination.path)
        self._handover_event(record)

    def _progress_tool(
        self,
        item: WorkItem,
        actions: StepActions,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> FunctionTool:
        async def invoke(context: RunContextWrapper[Any], arguments: str) -> str:
            if actions.called:
                raise PermissionError("stale_agent_epoch")
            # The answer goes back to the model either way, so the agent loop continues.
            actions.called = True
            call_id = getattr(context, "tool_call_id", None) or "call_" + uuid4().hex
            progress = self._progress.get(item.receipt.task_id)
            values: Any = None
            if len(arguments.encode("utf-8")) <= 8192:
                try:
                    values = json.loads(arguments, object_pairs_hook=unique_object)
                except ValueError:
                    values = None
            if (
                progress is None
                or progress.rounds >= PROGRESS_ROUNDS
                or not isinstance(values, dict)
                or not Draft202012Validator(PROGRESS_SCHEMA).is_valid(values)
                or not values["message"].strip()
            ):
                refused = json.dumps("refused")
                self._record_tool(item, "report_progress", call_id, arguments, refused)
                return refused
            progress.rounds += 1
            actions.reported = True
            self._record_tool(item, "report_progress", call_id, arguments, json.dumps("reported"))
            await self._report_progress(item, progress, values["state"], values["message"], emit)
            return json.dumps("reported")

        return FunctionTool(
            name="report_progress",
            description=(
                "Tell the user how the current request is going. Use still_working before a "
                "tool or external call, waiting_for_input when you ask the user something, and "
                "completed, failed or cancellation_confirmed only once the outcome is known."
            ),
            params_json_schema=PROGRESS_SCHEMA,
            on_invoke_tool=invoke,
            strict_json_schema=False,
        )

    def _progress_event(self, outcome: str, reason: str) -> None:
        if self._telemetry is not None:
            self._telemetry.event(
                "test.progress", outcome, mode=self.policy.slot.mode, reason=reason
            )

    async def _report_progress(
        self,
        item: WorkItem,
        progress: ProgressState,
        state: str,
        message: str,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Deliver one update unless it is stale or rate-limited (FR-024, FR-025)."""
        now = self.utc_now()
        slot = self.policy.slot
        if (
            self._failed
            or item is not self._current_item
            or item.generation != slot.generation
            or item.receipt.work.remaining(now) <= 0
        ):
            self._progress_event("failure", "stale")
            return
        if state == progress.last_state and (
            state != "still_working"
            or progress.last_still_working_at is None
            or (now - progress.last_still_working_at).total_seconds() < STILL_WORKING_REPEAT_SECONDS
        ):
            self._progress_event("failure", "rate_limited")
            return
        if slot.mode == "speech":
            segment = self._speech.get(item.receipt.task_id)
            if segment is None:
                self._progress_event("failure", "stale")
                return
            delegation_id = item.receipt.delegation_id
            if segment.intent.generation != slot.generation:
                if self.speech_segments is None:
                    self._progress_event("failure", "stale")
                    return
                segment, delegation_id = self.speech_segments.current, None
            for part in split_reply(message):
                if part.strip():
                    await segment.append_context(delegation_id, part, work=item.receipt.work)
            await emit(
                {"type": "progress", "state": state, "text": message, "generation": slot.generation}
            )
        else:
            await emit(
                {
                    "type": "progress",
                    "messageId": item.receipt.task_id,
                    "state": state,
                    "text": message,
                    "generation": slot.generation,
                }
            )
        progress.last_state = state
        if state == "still_working":
            progress.last_still_working_at = now
        if state in FINAL_STATES:
            progress.final_reported = True
        if state in HISTORY_STATES:
            self._record_item(item, "progress", message, state=state)
        self._progress_event("success", state)

    async def _clear_task(self, item: WorkItem) -> bool:
        slot = await self._gate(item)
        item.step(self.utc_now())
        model = self.text_adapter.model_for(slot, item.receipt.work)
        registry = build_agents(
            self.policy.snapshot,
            model=model,
            mode=slot.mode,
            capabilities=lambda configured: [],
            policy_tools=lambda configured: [],
            reasoning_effort=self.reasoning_effort,
        )
        agent = registry[slot.active_agent_id]
        agent.output_type = TaskClarity
        # The conversation so far comes first, so "and the second one?" can be clear (R8).
        prompt = (
            "Assess only whether these authenticated timed input fragments contain a request "
            "that is clear in the context of this conversation. They are not an authoritative "
            "complete ASR turn. Set unambiguous to false if information is missing or unclear "
            "even with the conversation above; do not infer missing words, select an action, "
            "rewrite the task, or authorize a capability. Exact fragment text:\n" + str(item.text)
        )
        inputs: list[Any] = [*self.history.render(), {"role": "user", "content": prompt}]
        async with asyncio.timeout(item.receipt.work.remaining(self.utc_now())):
            result = await Runner.run(
                agent,
                input=inputs,
                max_turns=1,
                run_config=RunConfig(
                    tracing_disabled=True,
                    trace_include_sensitive_data=False,
                    tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                ),
            )
        try:
            await self._gate(item)
            return isinstance(result.final_output, TaskClarity) and result.final_output.unambiguous
        finally:
            result.input = ""
            result.new_items.clear()
            result.raw_responses.clear()
            result.final_output = None

    async def _execute(
        self, item: WorkItem, emit: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        await self._gate(item, initial=True)
        # Welcome never runs a request: a request that comes first makes the move (FR-030).
        if await self.leave_welcome(emit, item.receipt.work):
            item.generation, item.agent_epoch = self.policy.slot.generation, self.policy.agent_epoch
        self._first_role = self._role(self.policy.active_agent_id)
        if self.history.timeline:
            await self._await_transcript(item)
        if item.text is None or item.requires_clarity_check and not await self._clear_task(item):
            segment = self._speech.get(item.receipt.task_id)
            if segment is None:
                raise PermissionError("clarification_required")
            await segment.append_context(
                item.receipt.delegation_id, "Please clarify your request.", work=item.receipt.work
            )
            return
        if not self.history.timeline:
            # Text records the request at once; voice has it as the caller's transcript.
            self._record_item(item, "user_text", item.text, agent_id=None)
        # This work item's own SDK items, kept natively between its steps (shared-history.md).
        own: list[Any] = []
        reply_bytes = 0
        first_text = False
        progress_round = False
        async with asyncio.timeout(item.receipt.work.remaining(self.utc_now())):
            while True:
                slot = await self._gate(item)
                if progress_round:
                    # A round after a progress update is bounded by PROGRESS_ROUNDS instead.
                    item.receipt.work.require(self.utc_now())
                else:
                    item.step(self.utc_now())
                model = self.text_adapter.model_for(slot, item.receipt.work)
                actions = StepActions()
                # The running item's own model input, beyond the shared history (R11).
                pending = [*self._rendered_item_entries(), *own]
                own_bytes = (
                    len(json.dumps(pending, ensure_ascii=False).encode("utf-8")) if pending else 0
                )
                if condensing.needed(
                    self.history, extra_bytes=own_bytes, extra_entries=len(self._item_entries)
                ):
                    await self.condense(
                        emit,
                        work=item.receipt.work,
                        extra_bytes=own_bytes,
                        extra_entries=len(self._item_entries),
                    )
                    slot = await self._gate(item)
                registry = build_agents(
                    self.policy.snapshot,
                    model=model,
                    mode=slot.mode,
                    capabilities=partial(
                        self._tools, item=item, source=slot, actions=actions, capabilities=True
                    ),
                    policy_tools=partial(
                        self._tools,
                        item=item,
                        source=slot,
                        actions=actions,
                        capabilities=False,
                        emit=emit,
                    ),
                    reasoning_effort=self.reasoning_effort,
                    handoffs=partial(self._handoffs, item=item, source=slot, actions=actions),
                )
                agent = registry[slot.active_agent_id]
                agent.tool_use_behavior = "stop_on_first_tool"
                base = [*self.history.render(), *self._rendered_item_entries()]
                inputs = [*base, *own]
                if self._context_bytes(inputs) > HISTORY_LIMIT:
                    raise PermissionError("conversation_context_limit")
                run = Runner.run_streamed(
                    agent,
                    input=inputs,
                    max_turns=1,
                    run_config=RunConfig(
                        tracing_disabled=True,
                        trace_include_sensitive_data=False,
                        tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                    ),
                )
                reply = []
                # The function calls this step produced, by name: they pair entries by call id.
                calls: dict[str, str] = {}
                self._runs.append(run)
                self._sdk_drained = False
                run_completed = False
                try:
                    try:
                        async for event in run.stream_events():
                            await self._gate(item)
                            if event.type == "agent_updated_stream_event":
                                # After an applied handoff the next step is rebuilt for the
                                # destination at its new agent epoch, so this run ends here.
                                if event.new_agent is not agent:
                                    run.cancel(mode="after_turn")
                                continue
                            if event.type != "raw_response_event":
                                continue
                            data = event.data
                            if self._handover_started is not None and (
                                (data.type == "response.output_text.delta" and data.delta)
                                or (
                                    data.type == "response.output_item.done"
                                    and getattr(getattr(data, "item", None), "type", "")
                                    == "function_call"
                                )
                            ):
                                # The destination's first output after the handoff (SC-005).
                                started, path = self._handover_started
                                self._handover_started = None
                                self._event(
                                    "test.latency",
                                    "success",
                                    reason="ready",
                                    measurement="handover_seconds",
                                    value=min(
                                        600.0, max(0.0, (self.utc_now() - started).total_seconds())
                                    ),
                                    fields={"path": path},
                                )
                            if data.type == "response.output_item.done":
                                done = getattr(data, "item", None)
                                if (
                                    done is not None
                                    and getattr(done, "type", "") == "function_call"
                                ):
                                    calls[str(done.name)] = str(done.call_id)
                                continue
                            if data.type != "response.output_text.delta":
                                continue
                            delta = data.delta
                            if delta and not first_text and self._telemetry is not None:
                                self._telemetry.event(
                                    "test.latency",
                                    "success",
                                    mode=slot.mode,
                                    reason="ready",
                                    measurement="backend_first_text_seconds",
                                    value=(
                                        self.utc_now() - item.receipt.work.work_accepted_at
                                    ).total_seconds(),
                                )
                                first_text = True
                            reply_bytes += len(delta.encode("utf-8"))
                            if reply_bytes > 64 * 1024:
                                raise PermissionError("text_reply_limit")
                            if self._context_bytes(inputs) + reply_bytes + 256 > HISTORY_LIMIT:
                                raise PermissionError("conversation_context_limit")
                            reply.append(delta)
                            if slot.mode == "speech":
                                continue
                            for offset in range(0, len(delta), 512):
                                await emit(
                                    {
                                        "type": "text.delta",
                                        "messageId": item.receipt.task_id,
                                        "text": delta[offset : offset + 512],
                                        "generation": slot.generation,
                                    }
                                )
                    except HandoverRefused as refusal:
                        # The requesting agent stays active and hears why (FR-008).
                        run_completed = True
                        destination = refusal.destination
                        call_id = calls.get(destination.name) or "call_" + uuid4().hex
                        self._record_item(
                            item,
                            "handover",
                            refusal.arguments,
                            name=destination.name,
                            call_id=call_id,
                            handover=refusal.record,
                        )
                        own = [*own, *self._item_entries[-1].items()]
                        progress_round = False
                        continue
                    run_completed = True
                    await self._gate(item)
                    progress_round = actions.reported
                    if actions.applied is not None:
                        destination, record, arguments = actions.applied
                        call_id = calls.get(destination.name) or "call_" + uuid4().hex
                        self._record_item(
                            item,
                            "handover",
                            arguments,
                            name=destination.name,
                            call_id=call_id,
                            handover=record,
                            agent_id=record.source_agent_id,
                            community_id=slot.active_community_id,
                        )
                        # The destination continues with the full input list (FR-007).
                        own = [
                            {**entry, "output": "applied"}
                            if isinstance(entry, dict)
                            and entry.get("type") == "function_call_output"
                            and entry.get("call_id") == call_id
                            else entry
                            for entry in model_items(run.to_input_list())[len(base) :]
                        ]
                        await self._agent_changed(emit)
                        continue
                    if actions.called:
                        own = model_items(run.to_input_list())[len(base) :]
                        continue
                    answer = "".join(reply)
                    if slot.mode == "speech":
                        segment = self._speech.get(item.receipt.task_id)
                        if segment is None:
                            raise PermissionError("stale_generation")
                        delivered, dropped = await self._speak(
                            item, segment, item.receipt.delegation_id, answer
                        )
                        if delivered.strip():
                            self._record_item(item, "delegation_result", delivered)
                        if dropped.strip():
                            self._record_item(
                                item, "delegation_result", dropped, delivery="not_delivered"
                            )
                    else:
                        self._record_item(item, "agent_answer", answer)
                        await emit(
                            {
                                "type": "text.completed",
                                "messageId": item.receipt.task_id,
                                "text": "",
                                "generation": slot.generation,
                            }
                        )
                    return
                finally:
                    run.cancel()
                    if run_completed:
                        self._runs.remove(run)
                        self._sdk_drained = not self._runs

    def _rendered_item_entries(self) -> list[dict[str, Any]]:
        """Entries the running item recorded outside any SDK step (its request text)."""
        return [
            rendered
            for entry in self._item_entries
            if entry.kind == "user_text"
            for rendered in entry.items()
        ]

    def _commit_item_entries(self) -> None:
        """The ended work item's entries join the shared history, in their order."""
        entries, self._item_entries = self._item_entries, []
        for entry in entries:
            self.history.record(entry)

    async def _work_error(
        self,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        task_id: str | None = None,
        *,
        active: bool = False,
        code: str = "work_unavailable",
    ) -> None:
        if self._telemetry is not None:
            self._telemetry.event(
                "test.work", "failure", mode=self.policy.slot.mode, reason="unavailable"
            )
        message: dict[str, Any] = {
            "type": "error",
            "code": code,
            "generation": self.policy.slot.generation,
        }
        if task_id is not None:
            message["messageId"] = task_id
        if active:
            # The session stays open, so the voice client must not treat this as its end.
            message["cleanupStatus"] = "active"
        try:
            async with asyncio.timeout(0.1):
                await emit(message)
        except Exception:
            pass

    def interrupt_speech(self) -> None:
        """Verified caller speech or a new delegation: drop the reply parts not yet sent."""
        if self._speaking:
            self._interrupted.set()

    async def _speak(
        self, item: WorkItem, segment: GptLiveSession, delegation_id: str | None, reply: str
    ) -> tuple[str, str]:
        """Send a spoken reply in parts of at most 500 bytes, each after the previous one's
        acknowledgement or 2 s; an interruption drops the rest into one quiet note. Returns the
        parts sent and the parts dropped."""
        # A blank reply goes to the adapter unchanged, which refuses it as before.
        parts = [part for part in split_reply(reply) if part.strip()] or [reply]
        work = item.receipt.work
        sent = 0
        interrupted = False
        self._speaking = True
        self._interrupted.clear()
        try:
            for index, part in enumerate(parts):
                if index:
                    if self._interrupted.is_set():
                        interrupted = True
                        break
                    if work.remaining(self.utc_now()) <= PART_MARGIN_SECONDS:
                        # The work budget is spent; the rest is dropped without a note.
                        break
                last = index == len(parts) - 1
                event_id = None if last else "part_" + uuid4().hex
                await segment.append_context(delegation_id, part, work=work, event_id=event_id)
                sent += 1
                if event_id is not None:
                    waits = {
                        asyncio.create_task(
                            segment.acknowledged(
                                event_id,
                                within=max(
                                    0.0,
                                    min(
                                        PART_ACK_SECONDS,
                                        work.remaining(self.utc_now()) - PART_MARGIN_SECONDS,
                                    ),
                                ),
                            )
                        ),
                        asyncio.create_task(self._interrupted.wait()),
                    }
                    try:
                        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for waiting in waits:
                            waiting.cancel()
                        await asyncio.wait(waits)
        finally:
            self._speaking = False
        dropped = len(parts) - sent
        if interrupted:
            for note in dropped_note("".join(parts[sent:])):
                await segment.append_context(delegation_id, note, quiet=True, work=work)
        if self._telemetry is not None and (len(parts) > 1 or dropped):
            for measurement, value in (("parts", len(parts)), ("dropped_parts", dropped)):
                self._telemetry.event(
                    "test.speech_parts",
                    "success",
                    mode="speech",
                    reason="ready",
                    measurement=measurement,
                    value=value,
                )
        return "".join(parts[:sent]), "".join(parts[sent:])

    def _delegation_event(self, item: WorkItem) -> None:
        """`test.delegation` when a voice delegation's work item ends (FR-019, R9)."""
        ready = self._delegation_ready.pop(item.receipt.task_id, False)
        handled = self._role(self.policy.active_agent_id)
        self._event(
            "test.delegation",
            "success" if ready else "failure",
            reason="ready" if ready else "invalid_input",
            fields={
                "first_role": self._first_role or handled,
                "handled_role": handled,
                "handed_on": self._handed_on,
                "untranscribed": item.untranscribed,
            },
        )

    async def condense(
        self,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        work: WorkBudget | None = None,
        extra_bytes: int = 0,
        extra_entries: int = 0,
    ) -> None:
        """Replace the oldest entries with a condensed account and say so (R11, FR-016).

        Any failure ends the conversation with `conversation_context_limit`.
        """
        slot = self.policy.slot
        size = {"history_kib": condensing.kib_range(self.history.rendered_bytes + extra_bytes)}
        chosen = condensing.plan(
            self.history.entries, extra_bytes=extra_bytes, extra_entries=extra_entries
        )
        try:
            if chosen is None:
                raise PermissionError("conversation_context_limit")
            account = await self.text_adapter.condense(
                slot,
                work or self._budget(),
                instructions=condensing.INSTRUCTIONS,
                inputs=cast(Any, condensing.request(chosen.condensed)),
            )
            history = condensing.condensed(self.history, chosen, account)
        except Exception:
            self._event("test.history", "failure", reason="limit", fields=size)
            raise PermissionError("conversation_context_limit") from None
        self.history = history
        self._event("test.history", "success", reason="condensed", fields=size)
        await emit({"type": "history.condensed", "generation": slot.generation})

    async def _tell_live(self, item: WorkItem, content: str) -> None:
        """Best effort: give the Live model a fixed line for this delegation."""
        segment = self._speech.get(item.receipt.task_id)
        if segment is None:
            return
        delegation_id = item.receipt.delegation_id
        try:
            if segment.intent.generation != self.policy.slot.generation:
                if self.speech_segments is None:
                    return
                segment, delegation_id = self.speech_segments.current, None
            async with asyncio.timeout(1):
                await segment.append_context(delegation_id, content)
        except Exception:
            pass

    async def process_next(self, *, emit: Callable[[dict[str, Any]], Awaitable[None]]) -> bool:
        if self._processing.locked():
            raise PermissionError("work_busy")
        async with self._processing:
            if self._failed:
                return False
            try:
                item = self.queue.take(now=self.utc_now())
            except PermissionError as error:
                if error.args == ("work_deadline_exceeded",):
                    # The pending item expired while earlier work ran; it is dropped, not run.
                    await self._work_error(emit, active=True)
                    return True
                self._failed = True
                await self._work_error(emit)
                await self.close()
                return True
            if item is None:
                return False
            self._processing_task = asyncio.current_task()
            self._current_item = item
            progress = self._progress[item.receipt.task_id] = ProgressState()
            failed_here = False
            ending = False
            try:
                await self._execute(item, emit)
            except Exception as error:
                code = (
                    error.args[0]
                    if isinstance(error, (PermissionError, AdmissionError)) and error.args
                    else None
                )
                # Only a failure bounded to this item keeps the session; anything else ends it.
                ending = not isinstance(error, TimeoutError) and code not in BOUNDED_WORK_FAILURES
                if ending:
                    self._failed = True
                failed_here = True
                if self._telemetry is not None:
                    self._telemetry.diagnostic(
                        "test.work.failure",
                        mode=self.policy.slot.mode,
                        error=type(error).__name__,
                        code=code,
                    )
                await self._work_error(
                    emit,
                    item.receipt.task_id,
                    active=not ending,
                    code=code if code == "conversation_context_limit" else "work_unavailable",
                )
                if code == "conversation_context_limit" and item.receipt.delegation_id:
                    # The caller hears why the call ends (R11).
                    await self._tell_live(item, condensing.LIMIT_MESSAGE)
                if not ending:
                    # The fixed line only stands in when the agent reported no final state.
                    if not progress.final_reported and item.receipt.delegation_id is not None:
                        self._progress_event("failure", "fallback_line")
                        await self._tell_live(item, WORK_FAILED_LINE)
            finally:
                self.queue.finish(item.receipt)
                self._speech.pop(item.receipt.task_id, None)
                self._progress.pop(item.receipt.task_id, None)
                if item.receipt.delegation_id is not None:
                    self._delegation_event(item)
                self._first_role, self._handed_on, self._handover_started = None, False, None
                if self._failed:
                    self._item_entries.clear()
                else:
                    # What the item did stays in history, a failed one included (FR-002).
                    self._commit_item_entries()
                self._current_item = None
                self._processing_task = None
            if self._telemetry is not None:
                self._telemetry.event(
                    "test.latency",
                    "failure" if failed_here else "success",
                    mode=self.policy.slot.mode,
                    reason="unavailable" if failed_here else "ready",
                    measurement="work_seconds",
                    value=(self.utc_now() - item.receipt.work.work_accepted_at).total_seconds(),
                )
            if ending:
                await self.close()
            return True

    @property
    def retained_bytes(self) -> int:
        return self._context_bytes(
            [*self.history.render(), *(item for e in self._item_entries for item in e.items())]
        )

    @property
    def ended(self) -> bool:
        return self._failed

    def _context_bytes(self, values: list[Any]) -> int:
        """The bytes of a model input plus everything else this conversation holds in memory."""
        rendered = (
            len(json.dumps(values, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if values
            else 0
        )
        speech = (
            sum(len(part.encode("utf-8")) for part in self._open_speech[2])
            if self._open_speech is not None
            else 0
        )
        return rendered + speech + self.queue.retained_bytes + self.speech_fragments.retained_bytes

    async def _disposal_ready(self) -> bool:
        return (
            self._failed
            and self._processing_task is None
            and self._sdk_drained
            and self.text_adapter.disposed
            and self.retained_bytes == 0
            and all(segment.disposed for segment in self._owned_live)
        )

    def _purge(self) -> None:
        self.queue.close()
        for task in self._update_watches:
            task.cancel()
        self.history.clear()
        self._item_entries.clear()
        self._open_speech = None
        self.speech_fragments.clear()
        self._speech.clear()
        for run in self._runs:
            run.input = ""
            run.new_items.clear()
            run.raw_responses.clear()
            run.final_output = None

    def fence(self) -> None:
        self._failed = True
        self.policy.admission.fence_local(self.policy.slot.session_id)
        owned = set(self._owned_live) | set(self._speech.values())
        if self.speech_segments is not None:
            try:
                owned.add(self.speech_segments.current)
            except PermissionError:
                pass
        self._owned_live = tuple(owned)
        for segment in self._owned_live:
            segment.retire_output()
        self._purge()

    async def close(self, *, deadline: float | None = None) -> bool:
        self.fence()
        started = asyncio.get_running_loop().time()
        boundary = asyncio.get_running_loop().time() + 10
        if deadline is not None:
            if type(deadline) not in (int, float) or not math.isfinite(deadline):
                raise ValueError("invalid_cleanup_deadline")
            boundary = min(boundary, deadline)
        try:
            result = await self._close_resources(boundary)
        except TimeoutError:
            self._failed = True
            self._purge()
            if not self._closing.locked():
                self._cleanup_result = False
            result = False
        if self._telemetry is not None and self._reported_cleanup != result:
            self._telemetry.event(
                "test.session",
                "success" if result else "failure",
                mode=self.policy.slot.mode,
                reason="stopped" if result else "cleanup_pending",
                correlation_id=self._cleanup_correlation,
            )
            self._telemetry.event(
                "test.latency",
                "success" if result else "failure",
                mode=self.policy.slot.mode,
                reason="stopped" if result else "cleanup_pending",
                measurement="cleanup_seconds",
                value=asyncio.get_running_loop().time() - started,
                correlation_id=self._cleanup_correlation,
            )
            self._telemetry.event(
                "test.cleanup",
                "success" if result else "failure",
                mode=self.policy.slot.mode,
                reason="stopped" if result else "cleanup_pending",
                correlation_id=self._cleanup_correlation,
            )
            self._reported_cleanup = result
        if not result and self._late_cleanup is None and self.text_adapter.draining:
            # A model request left running after its budget holds the slot until it finishes;
            # retry cleanup then instead of leaving the slot held for good.
            self._late_cleanup = asyncio.create_task(self._close_after_drain())
            _LATE_CLEANUPS.add(self._late_cleanup)
            self._late_cleanup.add_done_callback(_LATE_CLEANUPS.discard)
        return result

    async def _close_after_drain(self) -> None:
        try:
            await self.text_adapter.drained()
            async with self._closing:
                if self._cleanup_result is False:
                    self._cleanup_result = None
            await self.close()
        except Exception:
            pass

    async def _close_resources(self, deadline: float) -> bool:
        async with asyncio.timeout_at(deadline):
            async with self._closing:
                if self._cleanup_result is not None:
                    return self._cleanup_result
                self._failed = True
                owned = set(self._owned_live) | set(self._speech.values())
                if self.speech_segments is not None:
                    try:
                        owned.add(self.speech_segments.current)
                    except PermissionError:
                        pass
                self._owned_live = tuple(owned)
                for segment in self._owned_live:
                    segment.retire_output()
                active = self._processing_task
                if (
                    active is not None
                    and active is not asyncio.current_task()
                    and not active.done()
                ):
                    active.cancel()
                self._purge()
                try:
                    try:
                        async with asyncio.timeout_at(
                            min(deadline, asyncio.get_running_loop().time() + 1)
                        ):
                            await self.policy.admission.mark_terminal(
                                self.policy.slot.session_id, self.policy.slot.owner_epoch
                            )
                    except Exception:
                        pass
                    if active is not None and active is not asyncio.current_task():
                        try:
                            await active
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            pass
                    for run in self._runs:
                        run.cancel()
                        try:
                            async for _event in run.stream_events():
                                pass
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass
                    self._sdk_drained = all(
                        run.run_loop_task is None or run.run_loop_task.done() for run in self._runs
                    )
                    try:
                        await self.text_adapter.close()
                    except Exception:
                        pass
                    for segment in self._owned_live:
                        try:
                            await segment.close(deadline=deadline)
                        except Exception:
                            pass
                    current = await self.policy.admission.repository.read(deadline=deadline)
                    slot = next(
                        (
                            entry
                            for entry in current.authority.slots
                            if entry.session_id == self.policy.slot.session_id
                        ),
                        None,
                    )
                    if slot is None:
                        self._cleanup_result = True
                    elif self._cleanup_observations is not None:
                        observed = await self._cleanup_observations.observe(slot, deadline)
                        self._cleanup_result = (
                            observed is not None
                            and await self.policy.admission.release(
                                slot.session_id,
                                slot.owner_epoch,
                                ResourceCleanupVerifier.evidence(slot, observed),
                            )
                        )
                    else:
                        self._cleanup_result = False
                    if self._cleanup_result and self._cleanup_observations is not None:
                        self._cleanup_observations.forget(self.policy.slot.session_id)
                    return self._cleanup_result
                except Exception:
                    self._cleanup_result = False
                    return False
                finally:
                    self._purge()
                    self._runs.clear()
