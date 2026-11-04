"""Pinned active-agent position and the speech-session continuation it drives.

`ConversationPolicy` moves a session between agents and communities only along the pinned
configuration revision and only through admission transitions, so model text can never widen what
a session may reach. `SpeechSegments` carries Live speech across such a move by retiring the old
speech session and continuing on a new generation; a failed continuation fails the session.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from caller_runtime.adapters.capability_client import ConversationCapabilityClient
from caller_runtime.adapters.gpt_live import GptLiveSession
from caller_runtime.domain.configuration import RevisionSnapshot
from caller_runtime.domain.conversation import TestSlot, WorkBudget
from caller_runtime.domain.visibility import CommunityVisibility
from caller_runtime.services.admission import AdmissionError, TestAdmissionService


class ConversationPolicy:
    def __init__(
        self,
        *,
        slot: TestSlot,
        snapshot: RevisionSnapshot,
        admission: TestAdmissionService,
        retire_speech: Callable[[TestSlot, WorkBudget], Awaitable[None]] | None = None,
        continue_speech: Callable[[TestSlot, WorkBudget], Awaitable[None]] | None = None,
    ) -> None:
        if (
            slot.revision_id != snapshot.revision_id
            or slot.policy_hash != snapshot.policy_hash
            or slot.graph_hash != snapshot.graph_hash
        ):
            raise ValueError("conversation_revision_mismatch")
        self.slot, self.snapshot, self.admission = slot, snapshot, admission
        self.visibility = CommunityVisibility(snapshot.for_runtime())
        self._agents: dict[str, dict[str, Any]] = {
            agent["agentId"]: agent for agent in snapshot.for_runtime()["agents"]
        }
        self._lock = asyncio.Lock()
        self._retire_speech = retire_speech
        self._continue_speech = continue_speech
        self._pending_destination: str | None = None
        self.transfer_context: dict[str, str] = {}

    @property
    def active_agent_id(self) -> str:
        return self.slot.active_agent_id

    @property
    def active_community_id(self) -> str | None:
        return self.slot.active_community_id

    @property
    def agent_epoch(self) -> int:
        return self.slot.agent_epoch

    def _source(
        self, agent_id: str | None = None, agent_epoch: int | None = None
    ) -> dict[str, Any]:
        if (agent_id is not None and agent_id != self.active_agent_id) or (
            agent_epoch is not None and agent_epoch != self.agent_epoch
        ):
            raise PermissionError("stale_agent_epoch")
        return self._agents[self.active_agent_id]

    async def _move(self, destination: str, community: str | None, work: WorkBudget) -> None:
        if self.slot.mode == "speech":
            if self._retire_speech is None:
                raise PermissionError("speech_transition_unavailable")
            await self._retire_speech(self.slot, work)
        try:
            self.slot = await self.admission.transition(
                self.slot.session_id,
                self.slot.owner_epoch,
                source_agent_id=self.active_agent_id,
                source_agent_epoch=self.agent_epoch,
                target_agent_id=destination,
                target_community_id=community,
                work=work,
            )
        except AdmissionError as error:
            raise PermissionError(error.code) from None
        if self.slot.mode == "speech" and self._continue_speech is not None:
            await self._continue_speech(self.slot, work)

    async def restore_position(
        self, agent_id: str, community_id: str | None, work: WorkBudget
    ) -> bool:
        """Resume a stored conversation at its agent if the pinned revision still allows it."""
        async with self._lock:
            agent = self._agents.get(agent_id)
            graph = self.snapshot.for_runtime()
            if agent is None or agent["enabled"] is not True:
                return False
            if agent["role"] == "ordinary" and community_id != agent.get("communityId"):
                return False
            if community_id is not None and not any(
                community["communityId"] == community_id and community["enabled"] is True
                for community in graph["communities"]
            ):
                return False
            if (agent_id, community_id) != (self.active_agent_id, self.active_community_id):
                await self._move(agent_id, community_id, work)
            return True

    async def enter_routing(
        self,
        work: WorkBudget,
        *,
        source_agent_id: str | None = None,
        source_agent_epoch: int | None = None,
    ) -> None:
        async with self._lock:
            if self._source(source_agent_id, source_agent_epoch)["role"] != "welcome":
                raise PermissionError("handover_not_allowed")
            await self._move(self.visibility.lifecycle_target("routing"), None, work)

    async def route(
        self,
        community_id: str,
        work: WorkBudget,
        *,
        source_agent_id: str | None = None,
        source_agent_epoch: int | None = None,
    ) -> None:
        async with self._lock:
            if self._source(source_agent_id, source_agent_epoch)["role"] != "routing":
                raise PermissionError("handover_not_allowed")
            if self.active_community_id is None:
                entry = self.visibility.initial_entry(self.active_agent_id, community_id)
            else:
                if community_id != self._pending_destination:
                    raise PermissionError("handover_not_allowed")
                entry = self.visibility.transfer_entry(self.active_community_id, community_id)
            await self._move(entry["agentId"], community_id, work)
            self._pending_destination = None

    async def route_to_peer(
        self,
        destination_id: str,
        work: WorkBudget,
        *,
        source_agent_id: str,
        source_agent_epoch: int,
        context: dict[str, str] | None = None,
    ) -> None:
        async with self._lock:
            self._source(source_agent_id, source_agent_epoch)
            destination = self.visibility.handover_target(source_agent_id, destination_id)
            edge = next(
                edge
                for edge in self.snapshot.for_runtime()["relationships"]
                if edge["sourceAgentId"] == source_agent_id
                and edge["targetAgentId"] == destination_id
            )
            await self._move(destination["agentId"], destination["communityId"], work)
            self.transfer_context = {
                key: value
                for key, value in (context or {}).items()
                if key in edge["allowedContextFields"]
            }

    async def transfer_community(
        self,
        destination_id: str,
        work: WorkBudget,
        *,
        source_agent_id: str | None = None,
        source_agent_epoch: int | None = None,
        context: dict[str, str] | None = None,
    ) -> None:
        async with self._lock:
            self._source(source_agent_id, source_agent_epoch)
            transfer = self.visibility.community_transfer(self.active_agent_id, destination_id)
            edge = next(
                edge
                for edge in self.snapshot.for_runtime()["communityConnections"]
                if edge["sourceCommunityId"] == transfer["sourceCommunityId"]
                and edge["targetCommunityId"] == destination_id
            )
            await self._move(transfer["routing"]["agentId"], transfer["sourceCommunityId"], work)
            self._pending_destination = destination_id
            self.transfer_context = {
                key: value
                for key, value in (context or {}).items()
                if key in edge["allowedContextFields"]
            }

    async def interrupt(self, work: WorkBudget) -> None:
        async with self._lock:
            if (
                self.slot.mode != "speech"
                or self._retire_speech is None
                or self._continue_speech is None
            ):
                raise PermissionError("speech_transition_unavailable")
            previous = self.slot
            await self._retire_speech(previous, work)
            fenced = await self.admission.check_output(
                previous.session_id, previous.owner_epoch, generation=previous.generation + 1
            )
            if fenced.resources["live"].attempted != fenced.resources["live"].settled:
                raise PermissionError("live_segment_not_closed")
            self.slot = fenced
            await self._continue_speech(self.slot, work)

    async def capability(
        self,
        client: ConversationCapabilityClient,
        tool_id: str,
        action_id: str,
        inputs: dict[str, Any],
        work: WorkBudget,
        *,
        source_agent_id: str | None = None,
        source_agent_epoch: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._source(source_agent_id, source_agent_epoch)
            return await client.execute(
                snapshot=self.snapshot,
                slot=self.slot,
                tool_id=tool_id,
                action_id=action_id,
                inputs=inputs,
                work=work,
            )

    async def goodbye(
        self,
        work: WorkBudget,
        *,
        source_agent_id: str | None = None,
        source_agent_epoch: int | None = None,
    ) -> None:
        async with self._lock:
            if self._source(source_agent_id, source_agent_epoch)["role"] == "goodbye":
                raise PermissionError("handover_not_allowed")
            await self._move(
                self.visibility.lifecycle_target("goodbye"), self.active_community_id, work
            )


class SpeechSegments:
    def __init__(
        self,
        *,
        current: GptLiveSession,
        instructions_for: Callable[[TestSlot], str],
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._current: GptLiveSession | None = current
        self._adapter = current.adapter
        self._instructions_for, self._emit = instructions_for, emit
        self._lock = asyncio.Lock()
        self._confirmed: TestSlot | None = None
        self._failed = False
        self._changed = asyncio.Event()
        self._transition_work: WorkBudget | None = None
        self._session_id, self._owner_epoch = current.intent.session_id, current.intent.owner_epoch

    @property
    def current(self) -> GptLiveSession:
        if self._current is None:
            raise PermissionError("live_unavailable")
        return self._current

    async def _fail(self, slot: TestSlot | None = None) -> None:
        self._failed = True
        self._changed.set()
        try:
            async with asyncio.timeout(1):
                await self._adapter.admission.mark_terminal(self._session_id, self._owner_epoch)
        except Exception:
            pass

    async def retire(self, slot: TestSlot, work: WorkBudget) -> None:
        async with self._lock:
            if self._failed or self._current is None or self._confirmed is not None:
                raise PermissionError("speech_transition_unavailable")
            previous = self._current
            if (
                previous.intent.session_id != slot.session_id
                or previous.intent.generation != slot.generation
            ):
                raise PermissionError("stale_generation")
            self._changed.clear()
            self._transition_work = work
            previous.retire_output()
            try:
                async with asyncio.timeout(work.remaining(self._adapter.utc_now())):
                    fenced = await self._adapter.admission.retire_generation(
                        slot.session_id, slot.owner_epoch, generation=slot.generation, work=work
                    )
                    await self._emit(
                        {
                            "type": "generation.changed",
                            "generation": fenced.generation,
                            "reason": "segment_retired",
                        }
                    )
                    await previous.close(
                        deadline=asyncio.get_running_loop().time()
                        + work.remaining(self._adapter.utc_now())
                    )
                    work.require(self._adapter.utc_now())
                    self._confirmed = fenced
                    self._current = None
            except BaseException:
                await self._fail(slot)
                raise PermissionError("speech_transition_unavailable") from None

    async def continue_with(self, slot: TestSlot, work: WorkBudget) -> None:
        async with self._lock:
            fenced = self._confirmed
            if self._failed or self._current is not None or fenced is None:
                raise PermissionError("speech_transition_unavailable")
            if (
                slot.session_id != fenced.session_id
                or slot.owner_epoch != fenced.owner_epoch
                or slot.generation < fenced.generation
                or slot.revision_id != fenced.revision_id
                or slot.actor_key != fenced.actor_key
                or slot.expires_at != fenced.expires_at
            ):
                raise PermissionError("stale_owner")
            self._confirmed = None
            try:
                work.require(self._adapter.utc_now())
                self._current = await self._adapter.start(
                    slot, instructions=self._instructions_for(slot), work=work
                )
                work.require(self._adapter.utc_now())
                self._transition_work = None
                self._changed.set()
            except BaseException:
                await self._fail(slot)
                raise PermissionError("speech_transition_unavailable") from None

    async def _wait_continuation(self) -> None:
        work = self._transition_work
        if self._failed or work is None:
            raise PermissionError("speech_transition_unavailable")
        async with asyncio.timeout(work.remaining(self._adapter.utc_now())):
            await self._changed.wait()
        if self._failed:
            raise PermissionError("speech_transition_unavailable")
        work.require(self._adapter.utc_now())

    async def next_event(self) -> tuple[int, dict[str, Any]]:
        while not self._failed:
            previous = self._current
            if previous is None:
                await self._wait_continuation()
                continue
            try:
                event = await previous.next_event()
            except PermissionError:
                if self._current is not previous and not self._failed:
                    continue
                if self._transition_work is not None and not self._failed:
                    await self._wait_continuation()
                    continue
                await self._fail()
                raise PermissionError("live_unavailable") from None
            if self._current is not previous or self._transition_work is not None:
                continue
            if event["type"] == "session.closed":
                await self._fail()
                raise PermissionError("live_unavailable")
            return previous.intent.generation, event
        raise PermissionError("live_unavailable")
