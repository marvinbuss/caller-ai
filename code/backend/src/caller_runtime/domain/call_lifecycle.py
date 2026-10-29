"""Call-local lifecycle over injected ports; no live route or SDK construction."""

import asyncio
import copy
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from caller_runtime.domain.cleanup import interrupt as interrupt_work
from caller_runtime.domain.cleanup import terminal_cleanup
from caller_runtime.domain.session import (
    CallSession,
    LifecycleState,
    TerminalReason,
    VolatileCallContext,
)


class RepositoryPort(Protocol):
    async def create(self, metadata: Mapping[str, Any]) -> bool: ...
    async def read(self, session_id: str) -> dict[str, Any] | None: ...
    async def replace(self, session_id: str, metadata: Mapping[str, Any]) -> Any: ...


class AgentPort(Protocol):
    async def run(
        self, agent_id: str, *, session_id: str, context: VolatileCallContext, deadline: float
    ) -> str | None: ...
    async def cancel(self, session_id: str) -> None: ...


class PhonePort(Protocol):
    async def clear(self, provider_call_ref: str) -> None: ...
    async def hangup(self, provider_call_ref: str) -> bool: ...


@dataclass
class OwnedCall:
    context: VolatileCallContext = field(default_factory=VolatileCallContext)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    work: asyncio.Task[str | None] | None = None
    started: bool = False
    cancelled_work: set[asyncio.Task[str | None]] = field(default_factory=set)
    ending: bool = False
    interrupted: bool = False
    finish: asyncio.Task[None] | None = None
    ended_at: float | None = None


class CallLifecycle:
    def __init__(
        self,
        *,
        repository: RepositoryPort,
        agents: AgentPort,
        phone: PhonePort,
        configuration: Mapping[str, Any],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._agents = agents
        self._phone = phone
        self._configuration = copy.deepcopy(dict(configuration))
        self._clock = monotonic
        self._owner = uuid4().hex
        self._calls: dict[str, OwnedCall] = {}
        self._admission_lock = asyncio.Lock()
        self._roles: dict[str, str] = {}
        self._definitions = {agent["agentId"]: agent for agent in self._configuration["agents"]}
        for role in ("welcome", "routing", "goodbye"):
            matches = [
                agent["agentId"]
                for agent in configuration["agents"]
                if agent.get("enabled")
                and agent.get("role") == role
                and agent.get("communityId") is None
            ]
            if len(matches) != 1:
                raise ValueError("invalid_protected_role_fixture")
            self._roles[role] = matches[0]
        self._entries = {
            community["entryAgentId"]: community["communityId"]
            for community in configuration["communities"]
            if community.get("enabled")
        }
        if not self._entries or any(
            identifier not in self._definitions
            or not self._definitions[identifier].get("enabled")
            or self._definitions[identifier].get("role") != "ordinary"
            or self._definitions[identifier].get("communityId") != community
            for identifier, community in self._entries.items()
        ):
            raise ValueError("invalid_community_entry_fixture")

    async def admit(
        self, *, session_id: str, provider_call_ref: str, event_id: str, revision_id: str
    ) -> bool:
        if not event_id or revision_id != self._configuration["revisionId"]:
            raise ValueError("invalid_admission_projection")
        now = datetime.now(UTC)
        metadata = CallSession(
            sessionId=session_id,
            providerCallRef=provider_call_ref,
            configurationRevisionId=revision_id,
            physicalOwnerId=self._owner,
            connectionEpoch=1,
            agentEpoch=0,
            state=LifecycleState.ADMITTED,
            startedAt=now,
            leaseUntil=now + timedelta(seconds=30),
        )
        async with self._admission_lock:
            if not await self._repository.create(metadata.to_document()):
                return False
            self._calls[session_id] = OwnedCall()
            return True

    async def _read(self, session_id: str) -> dict[str, Any]:
        metadata = await self._repository.read(session_id)
        if metadata is None or metadata["physicalOwnerId"] != self._owner:
            raise PermissionError("session_not_owned")
        return metadata

    async def _write(self, session_id: str, **changes: Any) -> dict[str, Any]:
        metadata = await self._read(session_id)
        updated = {**metadata, **changes}
        await self._repository.replace(session_id, updated)
        return updated

    async def _run_agent(
        self,
        session_id: str,
        agent_id: str,
        state: LifecycleState,
        deadline: float,
        wall_deadline: float,
    ) -> tuple[bool, str | None]:
        owned = self._calls[session_id]
        async with owned.lock:
            metadata = await self._read(session_id)
            if owned.ending and state != LifecycleState.CLOSING or metadata["state"] == "Ended":
                return False, None
            if self._clock() >= deadline or time.monotonic() >= wall_deadline - 0.25:
                raise TimeoutError("operation_deadline_exceeded")
            await self._write(
                session_id,
                state=state.value,
                activeAgentId=agent_id,
                activeCommunityId=self._entries.get(agent_id),
                agentEpoch=metadata["agentEpoch"] + 1,
            )
            owned.work = asyncio.create_task(
                self._agents.run(
                    agent_id,
                    session_id=session_id,
                    context=owned.context,
                    deadline=deadline,
                )
            )
            task = owned.work
        try:
            result = await asyncio.wait_for(
                task, timeout=max(0, wall_deadline - time.monotonic() - 0.25)
            )
            return True, result
        except asyncio.CancelledError:
            if task in owned.cancelled_work or owned.ending:
                return False, None
            raise
        finally:
            owned.cancelled_work.discard(task)
            if owned.work is task:
                owned.work = None

    async def start(self, session_id: str) -> bool:
        owned = self._calls.get(session_id)
        if owned is None:
            return False
        metadata = await self._read(session_id)
        async with owned.lock:
            if owned.started or owned.ending or metadata["state"] != "Admitted":
                return False
            owned.started = True
        deadline = self._clock() + 10
        wall_deadline = time.monotonic() + 10
        try:
            success, _ = await self._run_agent(
                session_id,
                self._roles["welcome"],
                LifecycleState.INTRODUCING,
                deadline,
                wall_deadline,
            )
            if not success:
                return False
            success, target = await self._run_agent(
                session_id, self._roles["routing"], LifecycleState.ROUTING, deadline, wall_deadline
            )
            if not success:
                return False
            if target is None or target not in self._entries:
                raise PermissionError("routing_target_not_eligible")
            success, _ = await self._run_agent(
                session_id, target, LifecycleState.ACTIVE, deadline, wall_deadline
            )
            return success
        except Exception:
            await self._terminate(session_id, "operation_failed", False, deadline, wall_deadline)
            return False

    async def _cancel(self, session_id: str) -> None:
        owned = self._calls[session_id]
        task = owned.work
        if task is not None and not task.done():
            owned.cancelled_work.add(task)
            try:
                await asyncio.wait_for(self._agents.cancel(session_id), timeout=0.5)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def interrupt(self, session_id: str) -> None:
        owned = self._calls.get(session_id)
        if owned is None or owned.ending or (await self._read(session_id))["state"] == "Ended":
            return
        owned.interrupted = True
        metadata = await self._read(session_id)

        async def cancel() -> None:
            await self._cancel(session_id)

        async def clear() -> None:
            await self._phone.clear(metadata["providerCallRef"])

        if not await interrupt_work(cancel=cancel, clear=clear):
            raise RuntimeError("interruption_unconfirmed")

    async def _terminate(
        self,
        session_id: str,
        reason: TerminalReason,
        normal: bool,
        deadline: float,
        wall_deadline: float,
    ) -> None:
        owned = self._calls.get(session_id)
        if owned is None:
            return
        async with owned.lock:
            metadata = await self._read(session_id)
            if metadata["state"] == "Ended":
                return
            if owned.finish is None:
                owned.ending = True
                owned.finish = asyncio.create_task(
                    self._finish(session_id, reason, normal, deadline, wall_deadline),
                )
            finish = owned.finish
        await asyncio.shield(finish)

    async def _finish(
        self,
        session_id: str,
        reason: TerminalReason,
        normal: bool,
        deadline: float,
        wall_deadline: float,
    ) -> None:
        owned = self._calls[session_id]
        try:
            await self._cancel(session_id)
            if normal:
                await self._run_agent(
                    session_id,
                    self._roles["goodbye"],
                    LifecycleState.CLOSING,
                    deadline,
                    wall_deadline,
                )
        except Exception:
            reason = "operation_failed"
        finally:

            async def hangup() -> bool:
                metadata = await self._read(session_id)
                return await self._phone.hangup(metadata["providerCallRef"])

            async def end(acknowledged: bool) -> None:
                await self._write(
                    session_id,
                    state="Ended",
                    terminatedAt=datetime.now(UTC).isoformat(),
                    terminalReasonCode=reason if acknowledged else "provider_unavailable",
                )

            result = await terminal_cleanup(
                context=owned.context,
                hangup=hangup,
                end=end,
                deadline=asyncio.get_running_loop().time()
                + max(0.001, min(5, wall_deadline - time.monotonic())),
            )
            owned.ended_at = self._clock()
            if not result.terminal_confirmed:
                raise RuntimeError("terminal_metadata_unconfirmed")

    async def close(self, session_id: str, *, reason: TerminalReason) -> None:
        await self._terminate(session_id, reason, True, self._clock() + 10, time.monotonic() + 10)

    async def disconnect(self, session_id: str) -> None:
        await self._terminate(
            session_id, "caller_disconnected", False, self._clock() + 10, time.monotonic() + 10
        )

    async def handle_input(self, session_id: str, text: str) -> bool:
        owned = self._calls.get(session_id)
        if owned is None or owned.ending:
            return False
        metadata = await self._read(session_id)
        async with owned.lock:
            if owned.ending or metadata["state"] != "Active":
                return False
            owned.interrupted = False
            owned.context.conversation.append({"role": "user", "content": text})
        deadline, wall_deadline = self._clock() + 10, time.monotonic() + 10
        try:
            success, _ = await self._run_agent(
                session_id,
                metadata["activeAgentId"],
                LifecycleState.ACTIVE,
                deadline,
                wall_deadline,
            )
            return success
        except Exception:
            await self._terminate(session_id, "operation_failed", False, deadline, wall_deadline)
            return False

    async def execute_tool(self, session_id: str, tool: str, arguments: Mapping[str, Any]) -> None:
        raise PermissionError("capabilities_unavailable")

    def context_for(self, session_id: str) -> VolatileCallContext:
        return self._calls[session_id].context

    async def purge_expired(self) -> None:
        for identifier, owned in tuple(self._calls.items()):
            if owned.ended_at is not None and self._clock() - owned.ended_at >= 300:
                owned.context.purge()
                del self._calls[identifier]
