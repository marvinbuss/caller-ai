"""One text turn: admit a short session, continue the owner's stored conversation, store the result.

Each turn takes its own admission slot and releases it after verified cleanup, so a manager has
one in-flight turn. The conversation itself lives in the history store, keyed by the owner, so
follow-up turns can land on any backend instance. Failure to read or write the store stops the
turn instead of answering without context.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from caller_runtime.adapters.text_history_repository import (
    StoredConversation,
    TextHistoryRepository,
)
from caller_runtime.domain.conversation import StartPermit, WorkBudget, utc
from caller_runtime.domain.text_history import StoredTurn, TextConversation, new_conversation_id
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
        text: str,
        conversation_id: str | None,
        emit: Emit,
    ) -> TurnResult:
        owner = permit.actor_key
        stored: StoredConversation | None = None
        if conversation_id is not None:
            # Load before admission so an unknown or foreign id never takes a slot.
            stored = await self._store(
                self._history().load(owner, conversation_id, deadline=self._deadline())
            )
            if stored is None:
                raise PermissionError("conversation_not_found")
        identifier = conversation_id or new_conversation_id()
        controller = await self._runtime.start(permit, emit=emit)
        try:
            if stored is not None:
                await self._resume(controller, stored.conversation)
            await controller.submit_text("text-" + uuid4().hex, text)
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
                await self._save(controller, owner, identifier, stored)
            return TurnResult(identifier, completed)
        finally:
            # Cleanup must finish even if the client disconnects and cancels this request.
            await asyncio.shield(controller.close())

    async def _resume(
        self, controller: ConversationExecutor, conversation: TextConversation
    ) -> bool:
        """Restore the stored agent and turns; a position the pinned revision no longer allows
        starts the conversation over at the welcome agent."""
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
        if not await controller.policy.restore_position(
            conversation.active_agent_id, conversation.active_community_id, work
        ):
            return False
        controller.restore_history(conversation.messages())
        return True

    async def _save(
        self,
        controller: ConversationExecutor,
        owner: str,
        identifier: str,
        stored: StoredConversation | None,
    ) -> None:
        now = utc(self._utc_now())
        policy = controller.policy
        try:
            conversation = TextConversation(
                id=identifier,
                ownerKey=owner,
                turns=[StoredTurn.model_validate(turn) for turn in controller.text_turns()],
                activeAgentId=policy.active_agent_id,
                activeCommunityId=policy.active_community_id,
                createdAt=stored.conversation.created_at if stored is not None else now,
                updatedAt=now,
            )
        except ValidationError:
            # The model bounds the context, so overflow surfaces here, never as silent trimming.
            raise PermissionError("conversation_context_limit") from None
        if stored is None:
            await self._store(self._history().create(conversation, deadline=self._deadline()))
        else:
            await self._store(
                self._history().replace(stored, conversation, deadline=self._deadline())
            )
