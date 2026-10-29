"""Call-local fresh-session transfers; no built-in history-preserving SDK handoffs."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from agents.realtime import RealtimeSession

from caller_runtime.adapters.agents_sdk import AgentsSdkAdapter
from caller_runtime.adapters.session_repository import SessionConflict, metadata_model
from caller_runtime.domain.handover import HandoverOutcome, HandoverReason, HandoverRecord
from caller_runtime.domain.session import CallSession, LifecycleState, TerminalReason
from caller_runtime.domain.transfer_context import TransferContextPolicy
from caller_runtime.domain.visibility import CommunityVisibility
from caller_runtime.metrics import RuntimeMetrics
from caller_runtime.services.routing import HandoverWindow


class HandoverRepository(Protocol):
    async def read(self, session_id: str) -> dict[str, Any] | None: ...

    async def replace(self, session_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]: ...

    async def commit_handover(
        self, session_id: str, metadata: Mapping[str, Any], record: HandoverRecord
    ) -> dict[str, Any]: ...


class HandoverPhone(Protocol):
    async def clear(self, provider_call_ref: str) -> None: ...

    async def hangup(self, provider_call_ref: str) -> bool: ...

    async def send_audio(self, payload: bytes) -> None: ...


@dataclass(frozen=True, slots=True)
class TransferResult:
    outcome: HandoverOutcome
    reason_code: HandoverReason


@dataclass(slots=True, repr=False)
class OwnedHandover:
    metadata: CallSession
    phone: HandoverPhone
    scope: AbstractAsyncContextManager[RealtimeSession]
    session: RealtimeSession
    window: HandoverWindow = field(default_factory=HandoverWindow)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    work: asyncio.Task[TransferResult] | None = None
    requested: HandoverRecord | None = None
    ended: bool = False


class HandoverCoordinator:
    def __init__(
        self,
        *,
        repository: HandoverRepository,
        sdk: AgentsSdkAdapter,
        visibility: CommunityVisibility,
        context_policy: TransferContextPolicy,
        operation_budget: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
        handover_limit: int = 10,
        handover_window_seconds: float = 60.0,
        metrics: RuntimeMetrics | None = None,
    ) -> None:
        if not 0 < operation_budget <= 10:
            raise ValueError("invalid_operation_budget")
        self._repository, self._sdk = repository, sdk
        self._visibility, self._context_policy = visibility, context_policy
        self._budget, self._monotonic = operation_budget, monotonic
        self._metrics = metrics
        self._calls: dict[str, OwnedHandover] = {}
        self._admitting: set[str] = set()
        HandoverWindow(limit=handover_limit, seconds=handover_window_seconds)
        self._window_factory: Callable[[], HandoverWindow] = lambda: HandoverWindow(
            limit=handover_limit, seconds=handover_window_seconds, monotonic=monotonic
        )

    def _remaining(self, deadline: float) -> float:
        return max(0.0, deadline - self._monotonic())

    def _projection(self, identifier: str) -> dict[str, Any]:
        view = self._visibility.view_for(identifier)
        projection: dict[str, Any] = view["agent"]
        directory = {"peers": view["peers"], "communities": view["communities"]}
        projection["prompt"] += "\n\nHandover directory metadata:\n" + json.dumps(
            directory, sort_keys=True
        )
        return projection

    async def _dispose(
        self, scope: AbstractAsyncContextManager[RealtimeSession], deadline: float
    ) -> None:
        try:
            async with asyncio.timeout(self._remaining(deadline)):
                await scope.__aexit__(None, None, None)
        except (Exception, asyncio.CancelledError):
            pass

    async def own(self, session_id: str, *, physical_owner_id: str, phone: HandoverPhone) -> None:
        if session_id in self._calls or session_id in self._admitting:
            raise PermissionError("session_already_owned")
        self._admitting.add(session_id)
        deadline = self._monotonic() + self._budget
        scope = None
        entered = False
        try:
            async with asyncio.timeout(self._remaining(deadline)):
                document = await self._repository.read(session_id)
                if document is None:
                    raise PermissionError("session_not_owned")
                metadata = metadata_model(document)
                if (
                    metadata.physical_owner_id != physical_owner_id
                    or metadata.active_agent_id is None
                    or metadata.configuration_revision_id != self._visibility.revision_id
                    or metadata.state == LifecycleState.ENDED
                    or metadata.lease_until <= datetime.now(UTC)
                ):
                    raise PermissionError("session_not_owned")
                projection = self._projection(metadata.active_agent_id)
                scope = self._sdk.open_session(
                    agent_projection=projection,
                    context={"session_id": session_id, "revision_id": self._visibility.revision_id},
                )
                session = await scope.__aenter__()
                entered = True
                if await self._repository.read(session_id) != document:
                    raise PermissionError("session_not_owned")
                self._calls[session_id] = OwnedHandover(
                    metadata, phone, scope, session, window=self._window_factory()
                )
        except BaseException:
            if scope is not None and entered:
                await self._dispose(scope, deadline)
            raise
        finally:
            self._admitting.discard(session_id)

    def current(self, session_id: str) -> RealtimeSession:
        return self._calls[session_id].session

    def phone_for(self, session_id: str) -> HandoverPhone:
        return self._calls[session_id].phone

    def metadata_for(self, session_id: str) -> CallSession:
        return self._calls[session_id].metadata

    async def transfer(
        self,
        session_id: str,
        destination_id: str,
        *,
        context: Mapping[str, Any],
        deadline: float | None = None,
    ) -> TransferResult:
        absolute = self._monotonic() + self._budget
        started = time.perf_counter()
        try:
            result = await self._transfer(
                session_id,
                destination_id,
                context=context,
                deadline=min(absolute, deadline) if deadline is not None else absolute,
            )
        except asyncio.CancelledError:
            if self._metrics is not None:
                self._metrics.handover("canceled", min(3600, time.perf_counter() - started))
            raise
        except Exception:
            if self._metrics is not None:
                self._metrics.handover("failed", min(3600, time.perf_counter() - started))
            raise
        if self._metrics is not None:
            self._metrics.handover(result.outcome, min(3600, time.perf_counter() - started))
        return result

    async def _transfer(
        self,
        session_id: str,
        destination_id: str,
        *,
        context: Mapping[str, Any],
        deadline: float,
        terminal: bool = False,
    ) -> TransferResult:
        owned = self._calls[session_id]
        if owned.ended:
            return TransferResult("canceled", "caller_disconnected")
        if owned.work is not None and not owned.work.done():
            return TransferResult("failed", "transfer_in_progress")
        if terminal and destination_id != self._visibility.lifecycle_target("goodbye"):
            return TransferResult("failed", "policy_rejected")
        if not terminal and not owned.window.allowed():
            return TransferResult("failed", "loop_limited")
        source = owned.metadata.active_agent_id
        if source is None:
            return TransferResult("failed", "policy_rejected")
        try:
            self._visibility.resolve_handover(source, destination_id)
            projection = self._projection(destination_id)
            selected = self._context_policy.filter(
                self._visibility, source, destination_id, context
            )
        except (PermissionError, ValueError):
            return TransferResult("failed", "policy_rejected")
        now = datetime.now(UTC)
        owned.requested = HandoverRecord(
            handoverId=str(uuid4()),
            sessionId=session_id,
            configurationRevisionId=owned.metadata.configuration_revision_id,
            physicalOwnerId=owned.metadata.physical_owner_id,
            connectionEpoch=owned.metadata.connection_epoch,
            sourceAgentId=source,
            destinationAgentId=destination_id,
            sourceAgentEpoch=owned.metadata.agent_epoch,
            destinationAgentEpoch=owned.metadata.agent_epoch + 1,
            requestedAt=now,
        )
        owned.work = asyncio.create_task(
            self._perform(owned, projection, selected, deadline, count_commit=not terminal)
        )
        try:
            return await asyncio.shield(owned.work)
        except asyncio.CancelledError:
            owned.work.cancel()
            await owned.work
            raise

    @staticmethod
    def _record(
        requested: HandoverRecord, outcome: HandoverOutcome, reason: HandoverReason
    ) -> HandoverRecord:
        return HandoverRecord.model_validate(
            requested.model_dump()
            | {"outcome": outcome, "reason_code": reason, "completed_at": datetime.now(UTC)}
        )

    async def _perform(
        self,
        owned: OwnedHandover,
        projection: Mapping[str, Any],
        selected: dict[str, str],
        deadline: float,
        *,
        count_commit: bool,
    ) -> TransferResult:
        requested = owned.requested
        assert requested is not None
        scope = self._sdk.open_session(
            agent_projection=projection,
            context={
                "session_id": requested.session_id,
                "revision_id": self._visibility.revision_id,
            },
            transfer_context=selected,
            preparation_timeout=max(0.000001, self._remaining(deadline)),
        )
        entered = committed = False
        reason: HandoverReason = "destination_unavailable"
        try:
            async with asyncio.timeout(
                max(0.0, self._remaining(deadline) - min(0.25, self._budget / 5))
            ):
                await owned.phone.clear(owned.metadata.provider_call_ref)
                destination = await scope.__aenter__()
                entered = True
                async with owned.lock:
                    if owned.ended:
                        return TransferResult("canceled", "caller_disconnected")
                    if owned.metadata.lease_until <= datetime.now(UTC):
                        raise SessionConflict("session_owner_conflict")
                    state = {
                        "routing": LifecycleState.ROUTING,
                        "goodbye": LifecycleState.CLOSING,
                    }.get(str(projection["role"]), LifecycleState.ACTIVE)
                    candidate = CallSession.model_validate(
                        owned.metadata.model_dump()
                        | {
                            "agent_epoch": requested.destination_agent_epoch,
                            "active_agent_id": requested.destination_agent_id,
                            "active_community_id": projection["communityId"],
                            "state": state,
                        }
                    )
                    stored = await self._repository.commit_handover(
                        requested.session_id,
                        candidate.model_dump(by_alias=True),
                        self._record(requested, "committed", "completed"),
                    )
                    previous = owned.scope
                    owned.metadata = metadata_model(stored)
                    owned.scope, owned.session = scope, destination
                    owned.requested = None
                    committed = True
                    if count_commit:
                        owned.window.committed()
                await self._dispose(previous, deadline)
                return TransferResult("committed", "completed")
        except TimeoutError:
            reason = "deadline_exceeded"
        except asyncio.CancelledError:
            return (
                TransferResult("committed", "completed")
                if committed
                else TransferResult("canceled", "caller_disconnected")
            )
        except SessionConflict:
            reason = "owner_lost"
        except RuntimeError:
            reason = "store_unavailable" if entered else "destination_unavailable"
        except Exception:
            reason = "destination_unavailable"
        finally:
            selected.clear()
            if entered and not committed:
                await self._dispose(scope, deadline)
        if not committed and not owned.ended:
            try:
                async with asyncio.timeout(self._remaining(deadline)):
                    async with owned.lock:
                        if not owned.ended:
                            stored = await self._repository.commit_handover(
                                requested.session_id,
                                owned.metadata.model_dump(by_alias=True),
                                self._record(requested, "failed", reason),
                            )
                            owned.metadata = metadata_model(stored)
            except Exception:
                owned.ended = True
                try:
                    async with asyncio.timeout(self._remaining(deadline)):
                        await owned.phone.hangup(owned.metadata.provider_call_ref)
                except Exception:
                    pass
                try:
                    async with asyncio.timeout(self._remaining(deadline)):
                        latest = await self._repository.read(requested.session_id)
                        if latest is not None:
                            current = metadata_model(latest)
                            if (
                                current.physical_owner_id == owned.metadata.physical_owner_id
                                and current.connection_epoch == owned.metadata.connection_epoch
                                and current.state != LifecycleState.ENDED
                            ):
                                ended = current.transition(
                                    LifecycleState.ENDED,
                                    at=datetime.now(UTC),
                                    terminal_reason="operation_failed",
                                )
                                stored = await self._repository.replace(
                                    requested.session_id, ended.model_dump(by_alias=True)
                                )
                                owned.metadata = metadata_model(stored)
                except Exception:
                    pass
                await self._dispose(owned.scope, deadline)
        return TransferResult("failed", reason)

    async def emit(self, session_id: str, agent_epoch: int, payload: bytes) -> bool:
        owned = self._calls[session_id]
        async with owned.lock:
            if owned.ended or agent_epoch != owned.metadata.agent_epoch:
                return False
            try:
                async with asyncio.timeout(self._budget):
                    document = await self._repository.read(session_id)
                    if document is None:
                        return False
                    current = metadata_model(document)
                    if (
                        current.state == LifecycleState.ENDED
                        or current.lease_until <= datetime.now(UTC)
                        or current.physical_owner_id != owned.metadata.physical_owner_id
                        or current.connection_epoch != owned.metadata.connection_epoch
                        or current.agent_epoch != agent_epoch
                    ):
                        return False
                    await owned.phone.send_audio(payload)
                    return True
            except Exception:
                return False

    async def disconnect(
        self, session_id: str, *, reason: TerminalReason = "caller_disconnected"
    ) -> None:
        await self._disconnect(session_id, reason=reason, deadline=self._monotonic() + self._budget)

    async def _disconnect(
        self, session_id: str, *, reason: TerminalReason, deadline: float
    ) -> None:
        owned = self._calls[session_id]
        async with owned.lock:
            if owned.ended:
                return
            owned.ended = True
            pending = owned.work is not None and not owned.work.done()
            if pending:
                assert owned.work is not None
                owned.work.cancel()
            try:
                async with asyncio.timeout(self._remaining(deadline)):
                    candidate = owned.metadata.transition(
                        LifecycleState.ENDED, at=datetime.now(UTC), terminal_reason=reason
                    )
                    if pending and owned.requested is not None:
                        stored = await self._repository.commit_handover(
                            session_id,
                            candidate.model_dump(by_alias=True),
                            self._record(owned.requested, "canceled", "caller_disconnected"),
                        )
                    else:
                        stored = await self._repository.replace(
                            session_id, candidate.model_dump(by_alias=True)
                        )
                    owned.metadata = metadata_model(stored)
            except Exception:
                pass
        if pending and owned.work is not None:
            try:
                async with asyncio.timeout(self._remaining(deadline)):
                    await owned.work
            except (Exception, asyncio.CancelledError):
                pass
        await self._dispose(owned.scope, deadline)

    async def finish(
        self, session_id: str, *, goodbye: Callable[[str], Awaitable[None]]
    ) -> TransferResult:
        owned = self._calls[session_id]
        if owned.ended:
            return TransferResult("canceled", "caller_disconnected")
        deadline = self._monotonic() + self._budget
        reserve = min(0.5, self._budget / 4)
        result = await self._transfer(
            session_id,
            self._visibility.lifecycle_target("goodbye"),
            context={},
            deadline=deadline - reserve,
            terminal=True,
        )
        reason: TerminalReason = (
            "completed" if result.outcome == "committed" else "operation_failed"
        )
        try:
            if result.outcome == "committed":
                async with asyncio.timeout(max(0.0, self._remaining(deadline) - reserve)):
                    await goodbye(session_id)
        except Exception:
            reason = "operation_failed"
        finally:
            try:
                async with asyncio.timeout(max(0.0, self._remaining(deadline) - reserve / 2)):
                    await owned.phone.hangup(owned.metadata.provider_call_ref)
            except Exception:
                reason = "operation_failed"
            await self._disconnect(session_id, reason=reason, deadline=deadline)
        return result

    async def shutdown(self) -> None:
        for session_id in self._calls:
            await self.disconnect(session_id, reason="operation_failed")
