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
from datetime import datetime
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
from caller_runtime.domain.conversation import Metadata, TestSlot, WorkBudget
from caller_runtime.observability import Telemetry
from caller_runtime.services.admission import AdmissionError
from caller_runtime.services.cleanup import CleanupObservations, ResourceCleanupVerifier
from caller_runtime.services.conversation_agents import build_agents
from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments
from caller_runtime.services.delegation import (
    Acceptance,
    DelegationQueue,
    SpeechFragments,
    WorkItem,
)


class TaskClarity(Metadata):
    unambiguous: bool


class SourceBinding(TypedDict):
    source_agent_id: str
    source_agent_epoch: int


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
    ) -> None:
        self.policy, self.text_adapter, self.utc_now = policy, text_adapter, utc_now
        self.capability_client = capability_client
        self.speech_segments = speech_segments
        self.queue = DelegationQueue()
        self.speech_fragments = SpeechFragments()
        self._accepting, self._processing = asyncio.Lock(), asyncio.Lock()
        self._history: list[Any] = []
        self._transcripts: list[dict[str, Any]] = []
        self._failed = False
        self._speech: dict[str, GptLiveSession] = {}
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
                self._speech[task_id] = segment
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
        self._transcripts = candidate

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
        if role == "goodbye":
            return []
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
        return result

    async def _clear_task(self, item: WorkItem) -> bool:
        slot = await self._gate(item)
        item.step(self.utc_now())
        model = self.text_adapter.model_for(slot, item.receipt.work)
        registry = build_agents(
            self.policy.snapshot,
            model=model,
            capabilities=lambda configured: [],
            policy_tools=lambda configured: [],
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
        async with asyncio.timeout(item.receipt.work.remaining(self.utc_now())):
            while True:
                slot = await self._gate(item)
                item.step(self.utc_now())
                model = self.text_adapter.model_for(slot, item.receipt.work)
                called = [False]
                registry = build_agents(
                    self.policy.snapshot,
                    model=model,
                    capabilities=partial(
                        self._tools, item=item, source=slot, called=called, capabilities=True
                    ),
                    policy_tools=partial(
                        self._tools, item=item, source=slot, called=called, capabilities=False
                    ),
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
                    self._history = inputs + [{"role": "assistant", "content": "".join(reply)}]
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
                        await segment.append_context(
                            delegation_id, "".join(reply), work=item.receipt.work
                        )
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
        self, emit: Callable[[dict[str, Any]], Awaitable[None]], task_id: str | None = None
    ) -> None:
        if self._telemetry is not None:
            self._telemetry.event(
                "test.work", "failure", mode=self.policy.slot.mode, reason="unavailable"
            )
        message = {
            "type": "error",
            "code": "work_unavailable",
            "generation": self.policy.slot.generation,
        }
        if task_id is not None:
            message["messageId"] = task_id
        try:
            async with asyncio.timeout(0.1):
                await emit(message)
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
            except PermissionError:
                self._failed = True
                await self._work_error(emit)
                await self.close()
                return True
            if item is None:
                return False
            self._processing_task = asyncio.current_task()
            failed_here = False
            try:
                await self._execute(item, emit)
            except Exception as error:
                self._failed = True
                failed_here = True
                if self._telemetry is not None:
                    self._telemetry.diagnostic(
                        "test.work.failure",
                        mode=self.policy.slot.mode,
                        error=type(error).__name__,
                        code=error.args[0]
                        if isinstance(error, (PermissionError, AdmissionError))
                        else None,
                    )
                await self._work_error(emit, item.receipt.task_id)
            finally:
                self.queue.finish(item.receipt)
                self._speech.pop(item.receipt.task_id, None)
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
            if failed_here:
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
        return result

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
