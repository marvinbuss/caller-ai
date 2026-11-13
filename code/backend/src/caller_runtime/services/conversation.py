"""Runs one conversation's work: tool dispatch, delegation, transcripts and ordered cleanup.

`ConversationExecutor` takes accepted text or Live delegations from a bounded queue, runs the
pinned agents through the SDK and returns results to the text or speech caller. Every external
step first passes an admission gate, so a fenced or expired session stops before dispatch.
Failures surface as fixed codes; content stays in memory bounded by the context limit and is
dropped on close. `close` releases resources in a fixed order and reports whether cleanup is
verified.
"""

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import Any, TypedDict
from uuid import uuid4

from agents import FunctionTool, RunConfig, RunContextWrapper, Runner
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
from caller_runtime.observability import Telemetry
from caller_runtime.services.admission import AdmissionError
from caller_runtime.services.cleanup import CleanupObservations, ResourceCleanupVerifier
from caller_runtime.services.conversation_agents import Mode, agent_instructions, build_agents
from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments
from caller_runtime.services.delegation import (
    Acceptance,
    DelegationQueue,
    SpeechFragments,
    WorkItem,
)
from caller_runtime.services.greeting import (
    GREETING_TARGET_SECONDS,
    TEXT_FALLBACK_GREETING,
    TEXT_GREETING_INPUT,
    TEXT_GREETING_SECONDS,
    VOICE_GREETING_INPUT,
)
from caller_runtime.services.speech_parts import QUIET_HANDOVER, dropped_note, split_reply

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
BOUNDED_WORK_FAILURES = frozenset({"model_unavailable", "work_deadline_exceeded", "work_busy"})
# Cleanup retried once a detached model request has finished, held strongly until done.
_LATE_CLEANUPS: set[asyncio.Task[None]] = set()


class TaskClarity(Metadata):
    unambiguous: bool


class SourceBinding(TypedDict):
    source_agent_id: str
    source_agent_epoch: int


@dataclass(slots=True)
class ProgressState:
    """Progress of one work item: the rate limit, whether a final state came and history."""

    last_state: str | None = None
    last_still_working_at: datetime | None = None
    final_reported: bool = False
    rounds: int = 0
    entries: list[dict[str, str]] = field(default_factory=list)


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
    ) -> None:
        self.policy, self.text_adapter, self.utc_now = policy, text_adapter, utc_now
        self.capability_client = capability_client
        self.speech_segments = speech_segments
        self.queue = DelegationQueue(speech_work_seconds=speech_work_seconds)
        self._current_item: WorkItem | None = None
        self._progress: dict[str, ProgressState] = {}
        self._late_cleanup: asyncio.Task[None] | None = None
        self.speech_fragments = SpeechFragments()
        self._accepting, self._processing = asyncio.Lock(), asyncio.Lock()
        self._history: list[Any] = []
        self._transcripts: list[dict[str, Any]] = []
        # Assistant speech before the caller's first words or delegation is the greeting.
        self._greeting_parts: list[str] | None = [] if policy.slot.mode == "speech" else None
        self._failed = False
        self._speech: dict[str, GptLiveSession] = {}
        self._speaking = False
        self._interrupted = asyncio.Event()
        self._cleanup_observations = cleanup_observations
        self._telemetry = telemetry
        self._cleanup_correlation = uuid4().hex
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
        """Stream the opening greeting of a new text conversation and keep it as its first turn.

        `started` is the loop time the open request arrived. Without a complete model greeting
        by `TEXT_GREETING_SECONDS` after it, the request is cancelled and the fixed fallback is
        streamed instead.
        """
        slot = self.policy.slot
        if self._failed or slot.mode != "text" or self._history:
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
        self._history = [{"role": "assistant", "content": greeting}]

    def restore_history(self, turns: list[dict[str, str]]) -> None:
        """Seed a fresh text session with the owner's stored turns before the first message."""
        if self._history or self.policy.slot.mode != "text":
            raise PermissionError("mode_mismatch")
        self._history = [dict(turn) for turn in turns]
        if self._context_bytes(self._history) > 256 * 1024:
            self._history.clear()
            raise PermissionError("conversation_context_limit")

    def text_turns(self) -> list[dict[str, str]]:
        """Return only plain user and assistant text; tool and transfer items are not stored."""
        return [
            {"role": item["role"], "content": item["content"]}
            for item in self._history
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)
            and item["content"]
        ]

    async def submit_text(self, task_id: str, text: str) -> Acceptance:
        async with self._accepting:
            if self._failed or self.policy.slot.mode != "text":
                raise PermissionError("mode_mismatch")
            if self._context_bytes([*self._history, {"role": "user", "content": text}]) + 256 > (
                256 * 1024
            ):
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
            )
            if not accepted.duplicate:
                self._close_greeting()
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
                if self._telemetry is not None:
                    self._telemetry.event(
                        "test.delegation",
                        "success" if unambiguous else "failure",
                        mode="speech",
                        reason="ready" if unambiguous else "invalid_input",
                    )
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
        row = {
            "speaker": "user" if event["type"] == "session.input_transcript.delta" else "assistant",
            "text": event["delta"],
            "start_ms": event["start_ms"],
            "end_ms": event["end_ms"],
            "generation": generation,
        }
        candidate = self._transcripts + [row]
        size = len(json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        current_size = (
            len(json.dumps(self._transcripts, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if self._transcripts
            else 0
        )
        if size > 64 * 1024 or self.retained_bytes - current_size + size > 256 * 1024:
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
        if row["speaker"] == "user" and input_audio_accepted and event["delta"].strip():
            await self.policy.admission.user_activity(
                self.policy.slot.session_id,
                self.policy.slot.owner_epoch,
                generation=generation,
                mode="speech",
            )
            self._close_greeting()
        elif (
            row["speaker"] == "assistant"
            and self._greeting_parts is not None
            and sum(len(part.encode("utf-8")) for part in self._greeting_parts)
            + len(event["delta"].encode("utf-8"))
            <= 4096
        ):
            self._greeting_parts.append(event["delta"])
        self._transcripts = candidate

    def _close_greeting(self) -> None:
        """Keep the spoken greeting as the first assistant history entry (FR-039)."""
        if self._greeting_parts is None:
            return
        greeting = "".join(self._greeting_parts).strip()
        self._greeting_parts = None
        if greeting and not self._history:
            self._history.append({"role": "assistant", "content": greeting})

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
        if item.generation != current.generation or item.agent_epoch != current.agent_epoch:
            raise PermissionError("stale_generation")
        if initial and (
            item.receipt.generation != current.generation
            or item.receipt.agent_epoch != current.agent_epoch
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
        called: list[bool],
        *,
        tool_id: str | None = None,
        action_id: str | None = None,
    ) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": properties,
            "required": [
                key for key in properties if key not in {"request_summary", "transfer_reason"}
            ],
            "additionalProperties": False,
        }

        async def invoke(context: RunContextWrapper[Any], arguments: str) -> str:
            await self._gate(item)
            if (
                called[0]
                or self.policy.agent_epoch != source.agent_epoch
                or self.policy.active_agent_id != source.active_agent_id
            ):
                raise PermissionError("stale_agent_epoch")
            if len(arguments.encode("utf-8")) > 8192:
                raise PermissionError("invalid_work_input")
            values = json.loads(arguments, object_pairs_hook=unique_object)
            if not isinstance(values, dict) or not Draft202012Validator(schema).is_valid(values):
                raise PermissionError("invalid_work_input")
            called[0] = True
            item.step(self.utc_now())
            binding: SourceBinding = {
                "source_agent_id": source.active_agent_id,
                "source_agent_epoch": source.agent_epoch,
            }
            transfer_context = {
                key: values[key] for key in ("request_summary", "transfer_reason") if key in values
            }
            if tool_id is not None and action_id is not None:
                if self.capability_client is None:
                    raise PermissionError("capability_unavailable")
                result = await self.policy.capability(
                    self.capability_client,
                    tool_id,
                    action_id,
                    values["inputs"],
                    item.receipt.work,
                    **binding,
                )
            elif name == "enter_routing":
                await self.policy.enter_routing(item.receipt.work, **binding)
                result = {"status": "routed"}
            elif name == "route_community":
                await self.policy.route(values["community_id"], item.receipt.work, **binding)
                result = {"status": "routed"}
            elif name == "handover_agent":
                await self.policy.route_to_peer(
                    values["agent_id"], item.receipt.work, context=transfer_context, **binding
                )
                result = {"status": "handed_over"}
            elif name == "transfer_community":
                await self.policy.transfer_community(
                    values["community_id"], item.receipt.work, context=transfer_context, **binding
                )
                result = {"status": "routed"}
            elif name == "goodbye":
                await self.policy.goodbye(item.receipt.work, **binding)
                result = {"status": "ending"}
            else:
                view = self.policy.visibility.view_for(source.active_agent_id)
                result = {"peers": view["peers"], "communities": view["communities"]}
            if self.policy.agent_epoch != source.agent_epoch:
                item.generation = self.policy.slot.generation
                item.agent_epoch = self.policy.agent_epoch
            await self._gate(item)
            return json.dumps(result, allow_nan=False, separators=(",", ":"))

        return FunctionTool(
            name=name,
            description="Published-policy " + name.replace("_", " "),
            params_json_schema=schema,
            on_invoke_tool=invoke,
            strict_json_schema=False,
        )

    def _tools(
        self,
        configured: dict[str, Any],
        item: WorkItem,
        source: TestSlot,
        called: list[bool],
        *,
        capabilities: bool,
        emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        reported: list[bool] | None = None,
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
                    called,
                    tool_id=grant["toolId"],
                    action_id=action,
                )
                for grant in configured["capabilityAssignments"]
                for action in grant["permittedActionNames"]
            ]
        progress = (
            [self._progress_tool(item, called, reported, emit)]
            if emit is not None and reported is not None
            else []
        )
        if role == "goodbye":
            return progress
        result = [self._tool("goodbye", {}, item, source, called)]
        if role == "welcome":
            result.append(self._tool("enter_routing", {}, item, source, called))
        elif role == "routing":
            result.append(
                self._tool(
                    "route_community",
                    {"community_id": {"type": "string", "maxLength": 128}},
                    item,
                    source,
                    called,
                )
            )
        else:
            fields = {
                "request_summary": {"type": "string", "maxLength": 1024},
                "transfer_reason": {"type": "string", "maxLength": 1024},
            }
            result.extend(
                [
                    self._tool("discover_peers", {}, item, source, called),
                    self._tool(
                        "handover_agent",
                        {"agent_id": {"type": "string", "maxLength": 128}, **fields},
                        item,
                        source,
                        called,
                    ),
                    self._tool(
                        "transfer_community",
                        {"community_id": {"type": "string", "maxLength": 128}, **fields},
                        item,
                        source,
                        called,
                    ),
                ]
            )
        return result + progress

    def _progress_tool(
        self,
        item: WorkItem,
        called: list[bool],
        reported: list[bool],
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> FunctionTool:
        async def invoke(context: RunContextWrapper[Any], arguments: str) -> str:
            if called[0]:
                raise PermissionError("stale_agent_epoch")
            # The answer goes back to the model either way, so the agent loop continues.
            called[0] = True
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
                return json.dumps("refused")
            progress.rounds += 1
            reported[0] = True
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
            progress.entries.append(
                {"role": "assistant", "content": f"Status ({state}): {message}"}
            )
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
            reasoning_effort="low",
        )
        agent = registry[slot.active_agent_id]
        agent.output_type = TaskClarity
        prompt = (
            "Assess only whether these authenticated timed input fragments contain "
            "a self-contained, "
            "unambiguous request. They are not an authoritative complete ASR turn. Set unambiguous "
            "to false if information is missing or unclear; do not infer missing words, select "
            "an action, rewrite the task, or authorize a capability. Exact fragment text:\n"
            + str(item.text)
        )
        async with asyncio.timeout(item.receipt.work.remaining(self.utc_now())):
            result = await Runner.run(
                agent,
                input=prompt,
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
        if item.text is None or item.requires_clarity_check and not await self._clear_task(item):
            segment = self._speech.get(item.receipt.task_id)
            if segment is None:
                raise PermissionError("clarification_required")
            await segment.append_context(
                item.receipt.delegation_id, "Please clarify your request.", work=item.receipt.work
            )
            return
        inputs = self._history + [{"role": "user", "content": item.text}]
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
                called, reported = [False], [False]
                registry = build_agents(
                    self.policy.snapshot,
                    model=model,
                    mode=slot.mode,
                    capabilities=partial(
                        self._tools, item=item, source=slot, called=called, capabilities=True
                    ),
                    policy_tools=partial(
                        self._tools,
                        item=item,
                        source=slot,
                        called=called,
                        capabilities=False,
                        emit=emit,
                        reported=reported,
                    ),
                    # A caller waits in silence on speech; text keeps the deployment default.
                    reasoning_effort="low" if slot.mode == "speech" else None,
                )
                agent = registry[slot.active_agent_id]
                agent.tool_use_behavior = "stop_on_first_tool"
                if self._context_bytes(inputs) > 256 * 1024:
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
                self._runs.append(run)
                self._sdk_drained = False
                run_completed = False
                try:
                    async for event in run.stream_events():
                        await self._gate(item)
                        if (
                            event.type != "raw_response_event"
                            or event.data.type != "response.output_text.delta"
                        ):
                            continue
                        delta = event.data.delta
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
                        if self._context_bytes(inputs) + reply_bytes + 256 > 256 * 1024:
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
                    run_completed = True
                    await self._gate(item)
                    progress_round = reported[0]
                    if called[0]:
                        if self.policy.agent_epoch != slot.agent_epoch:
                            inputs = [
                                {
                                    "role": "user",
                                    "content": "Authorized transfer context: "
                                    + json.dumps(self.policy.transfer_context, allow_nan=False),
                                }
                            ]
                            if slot.active_community_id is None:
                                inputs = [{"role": "user", "content": item.text}]
                            self._history = []
                            await emit(
                                {
                                    "type": "agent.changed",
                                    "agentId": self.policy.active_agent_id,
                                    "communityId": self.policy.active_community_id,
                                    "generation": self.policy.slot.generation,
                                }
                            )
                        else:
                            inputs = run.to_input_list()
                        continue
                    # Final and waiting progress states stay in history before the answer.
                    progress = self._progress.get(item.receipt.task_id)
                    entries = progress.entries if progress is not None else []
                    self._history = [
                        *inputs,
                        *entries,
                        {"role": "assistant", "content": "".join(reply)},
                    ]
                    entries.clear()
                    if self._context_bytes(self._history) > 256 * 1024:
                        raise PermissionError("conversation_context_limit")
                    if slot.mode == "speech":
                        segment = self._speech.get(item.receipt.task_id)
                        if segment is None:
                            raise PermissionError("stale_generation")
                        delegation_id = item.receipt.delegation_id
                        if segment.intent.generation != slot.generation:
                            if self.speech_segments is None:
                                raise PermissionError("speech_transition_unavailable")
                            segment = self.speech_segments.current
                            if segment.intent.generation != slot.generation:
                                raise PermissionError("stale_generation")
                            delegation_id = None
                        await self._speak(item, segment, delegation_id, "".join(reply))
                    else:
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

    async def _work_error(
        self,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        task_id: str | None = None,
        *,
        active: bool = False,
    ) -> None:
        if self._telemetry is not None:
            self._telemetry.event(
                "test.work", "failure", mode=self.policy.slot.mode, reason="unavailable"
            )
        message: dict[str, Any] = {
            "type": "error",
            "code": "work_unavailable",
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
    ) -> None:
        """Send a spoken reply in parts of at most 500 bytes, each after the previous one's
        acknowledgement or 2 s; an interruption drops the rest into one quiet note."""
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
                await self._work_error(emit, item.receipt.task_id, active=not ending)
                if not ending:
                    if progress.entries and (
                        self._context_bytes([*self._history, *progress.entries]) <= 256 * 1024
                    ):
                        self._history.extend(progress.entries)
                    # The fixed line only stands in when the agent reported no final state.
                    if not progress.final_reported and item.receipt.delegation_id is not None:
                        self._progress_event("failure", "fallback_line")
                        await self._tell_live(item, WORK_FAILED_LINE)
            finally:
                self.queue.finish(item.receipt)
                self._speech.pop(item.receipt.task_id, None)
                self._progress.pop(item.receipt.task_id, None)
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
        return self._context_bytes(self._history)

    @property
    def ended(self) -> bool:
        return self._failed

    def _context_bytes(self, values: list[Any]) -> int:
        history = (
            len(json.dumps(values, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if values
            else 0
        )
        transcripts = (
            len(json.dumps(self._transcripts, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if self._transcripts
            else 0
        )
        context = (
            len(
                json.dumps(
                    self.policy.transfer_context, ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
            )
            if self.policy.transfer_context
            else 0
        )
        return (
            history
            + transcripts
            + context
            + self.queue.retained_bytes
            + self.speech_fragments.retained_bytes
        )

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
        self._history.clear()
        self._transcripts.clear()
        self._greeting_parts = None
        self.speech_fragments.clear()
        self.policy.transfer_context.clear()
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
