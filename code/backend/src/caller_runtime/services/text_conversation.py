"""One text turn: admit a short session, continue the owner's stored conversation, store the result.

Each turn takes its own admission slot and releases it after verified cleanup, so a manager has
one in-flight turn. The conversation itself lives in the history store, keyed by the owner, so
follow-up turns can land on any backend instance. It is stored as version 2: every shared history
entry plus the agent position and a Routing transfer in progress. A conversation stored before
version 2 is closed: the request starts a new one, with the greeting (FR-025). Failure to read or
write the store stops the turn instead of answering without context.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from caller_runtime.adapters.text_history_repository import (
    MAX_DOCUMENT_BYTES,
    ClosedConversation,
    StoredConversation,
    TextHistoryRepository,
)
from caller_runtime.domain.conversation import StartPermit, WorkBudget, utc
from caller_runtime.domain.text_history import TextConversation, new_conversation_id
from caller_runtime.observability import Telemetry
from caller_runtime.services.conversation import ConversationExecutor

Emit = Callable[[dict[str, Any]], Awaitable[None]]
T = TypeVar("T")
STORE_BUDGET_SECONDS = 2


class TextRuntime(Protocol):
    async def start(self, permit: StartPermit, *, emit: Emit) -> ConversationExecutor: ...


@dataclass(frozen=True, slots=True)
class TurnResult:
    conversation_id: str
    completed: bool


class TextConversationService:
    def __init__(
        self,
        *,
        runtime: TextRuntime,
        history: Callable[[], TextHistoryRepository],
        telemetry: Telemetry | Callable[[], Telemetry],
        utc_now: Callable[[], datetime],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if any(port is None for port in (runtime, history, telemetry, utc_now)):
            raise ValueError("explicit_text_conversation_ports_required")
        self._runtime, self._history = runtime, history
        self._telemetry, self._utc_now, self._monotonic = telemetry, utc_now, monotonic

    async def _store(self, operation: Awaitable[T]) -> T:
        """Run one store call and record only its outcome and duration, never its content."""
        telemetry = self._telemetry() if callable(self._telemetry) else self._telemetry
        started = self._monotonic()
        try:
            result = await operation
        except BaseException:
            telemetry.event(
                "history.store",
                "failure",
                measurement="store_seconds",
                value=min(self._monotonic() - started, 2**31),
            )
            raise
        telemetry.event(
            "history.store",
            "success",
            measurement="store_seconds",
            value=min(self._monotonic() - started, 2**31),
        )
        return result

    @staticmethod
    def _deadline() -> float:
        return asyncio.get_running_loop().time() + STORE_BUDGET_SECONDS

    async def run(
        self,
        permit: StartPermit,
        *,
        text: str | None,
        conversation_id: str | None,
        emit: Emit,
    ) -> TurnResult:
        owner = permit.actor_key
        stored: StoredConversation | None = None
        closed = False
        if text is None:
            # The open request starts a new conversation with the greeting and nothing else.
            if conversation_id is not None:
                raise PermissionError("invalid_control")
            return await self._open(permit, emit=emit)
        started = asyncio.get_running_loop().time()
        if conversation_id is not None:
            # Load before admission so an unknown or foreign id never takes a slot.
            loaded = await self._store(
                self._history().load(owner, conversation_id, deadline=self._deadline())
            )
            if loaded is None:
                raise PermissionError("conversation_not_found")
            if isinstance(loaded, ClosedConversation):
                # Stored before history version 2: a new conversation starts (FR-025).
                closed, conversation_id = True, None
            else:
                stored = loaded
        identifier = conversation_id or new_conversation_id()
        controller = await self._runtime.start(permit, emit=emit)
        try:
            if stored is not None:
                await self._resume(controller, stored.conversation)
            if closed:
                await emit({"type": "conversation", "conversationId": identifier})
                await controller.open_text(emit, started=started)
            await controller.submit_text("text-" + uuid4().hex, text)
            if not closed:
                await emit({"type": "conversation", "conversationId": identifier})
            completed = False

            async def watch(event: dict[str, Any]) -> None:
                nonlocal completed
                completed = completed or event.get("type") == "text.completed"
                await emit(event)

            while await controller.process_next(emit=watch):
                pass
            if completed:
                # A failed or cut-short turn writes nothing, so history never holds a half turn.
                await self._save(controller, owner, identifier, stored, emit=emit)
            return TurnResult(identifier, completed)
        finally:
            # Cleanup must finish even if the client disconnects and cancels this request.
            await asyncio.shield(controller.close())

    async def _open(self, permit: StartPermit, *, emit: Emit) -> TurnResult:
        started = asyncio.get_running_loop().time()
        identifier = new_conversation_id()
        controller = await self._runtime.start(permit, emit=emit)
        try:
            await emit({"type": "conversation", "conversationId": identifier})
            await controller.open_text(emit, started=started)
            await self._save(controller, permit.actor_key, identifier, None)
            return TurnResult(identifier, True)
        finally:
            await asyncio.shield(controller.close())

    async def _resume(
        self, controller: ConversationExecutor, conversation: TextConversation
    ) -> bool:
        """Restore the stored entries, agent and Routing transfer. A position the pinned revision
        no longer allows starts over at Welcome with all entries kept; Welcome then hands over
        to Routing without a new greeting."""
        slot = controller.policy.slot
        now = utc(self._utc_now())
        work = WorkBudget(
            now,
            min(
                now + timedelta(seconds=10),
                slot.auth_expires_at,
                slot.expires_at,
                slot.idle_expires_at,
            ),
        )
        controller.restore_history(conversation.entries)
        if not await controller.policy.restore_position(
            conversation.active_agent_id, conversation.active_community_id, work
        ):
            return False
        controller.policy.restore_pending(conversation.pending_destination)
        return True

    async def _save(
        self,
        controller: ConversationExecutor,
        owner: str,
        identifier: str,
        stored: StoredConversation | None,
        *,
        emit: Emit | None = None,
    ) -> None:
        conversation = self._document(controller, owner, identifier, stored)
        if self._size(conversation) > MAX_DOCUMENT_BYTES and emit is not None:
            # Condensing runs first; if the document is still too large, the conversation ends.
            await controller.condense(emit)
            conversation = self._document(controller, owner, identifier, stored)
        if self._size(conversation) > MAX_DOCUMENT_BYTES:
            raise PermissionError("conversation_context_limit")
        if stored is None:
            await self._store(self._history().create(conversation, deadline=self._deadline()))
        else:
            await self._store(
                self._history().replace(stored, conversation, deadline=self._deadline())
            )

    @staticmethod
    def _size(conversation: TextConversation) -> int:
        return len(
            json.dumps(
                conversation.model_dump(mode="json", by_alias=True), ensure_ascii=False
            ).encode("utf-8")
        )

    def _document(
        self,
        controller: ConversationExecutor,
        owner: str,
        identifier: str,
        stored: StoredConversation | None,
    ) -> TextConversation:
        now = utc(self._utc_now())
        policy = controller.policy
        try:
            return TextConversation(
                id=identifier,
                ownerKey=owner,
                historyVersion=2,
                entries=list(controller.history.entries),
                activeAgentId=policy.active_agent_id,
                activeCommunityId=policy.active_community_id,
                pendingDestination=policy.pending_destination,
                createdAt=stored.conversation.created_at if stored is not None else now,
                updatedAt=now,
            )
        except ValidationError:
            # The model bounds the context, so overflow surfaces here, never as silent trimming.
            raise PermissionError("conversation_context_limit") from None
