"""Pinned active-agent position, handovers along the published graph, and the one Live session.

`ConversationPolicy` moves a session between agents and communities only along the pinned
configuration revision and only through admission transitions, so model text can never widen what
a session may reach. `handover` is the one way to change the active agent (feature 007): it
rechecks the destination against the same rules that offered it (`handover_rules`), applies the
transition within the work budget and returns the `Handover` record. A refusal raises with a fixed
code and leaves the position unchanged. `SpeechSegments` holds the one Live session of a voice
test, which an agent change never replaces.
"""

import asyncio
from typing import Any

from caller_runtime.adapters.capability_client import ConversationCapabilityClient
from caller_runtime.adapters.gpt_live import GptLiveSession
from caller_runtime.domain.configuration import RevisionSnapshot
from caller_runtime.domain.conversation import TestSlot, WorkBudget
from caller_runtime.domain.shared_history import Handover, HandoverReason
from caller_runtime.domain.visibility import CommunityVisibility
from caller_runtime.services.admission import AdmissionError, TestAdmissionService
from caller_runtime.services.handover_rules import (
    Destination,
    HandoverPath,
    destinations,
    enabled,
)

# Policy and admission codes a handover refusal reports to the model (FR-008).
REFUSALS: dict[str, HandoverReason] = {
    "stale_agent_epoch": "refused_stale",
    "handover_not_allowed": "refused_graph",
    "work_deadline_exceeded": "refused_budget",
    "work_step_limit": "refused_step_limit",
}


class ConversationPolicy:
    def __init__(
        self,
        *,
        slot: TestSlot,
        snapshot: RevisionSnapshot,
        admission: TestAdmissionService,
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
        self._pending_destination: str | None = None

    @property
    def active_agent_id(self) -> str:
        return self.slot.active_agent_id

    @property
    def active_community_id(self) -> str | None:
        return self.slot.active_community_id

    @property
    def agent_epoch(self) -> int:
        return self.slot.agent_epoch

    @property
    def pending_destination(self) -> str | None:
        """The community a transfer through Routing is heading to, if one is in progress."""
        return self._pending_destination

    def restore_pending(self, community_id: str | None) -> None:
        """Resume a stored Routing transfer; ignored unless Routing is active and may reach it."""
        self._pending_destination = None
        if community_id is not None and any(
            destination.path == "routing" and destination.community_id == community_id
            for destination in self.offered()
        ):
            self._pending_destination = community_id

    def offered(self) -> list[Destination]:
        """The handovers the active agent may request now, in their published order."""
        return destinations(self.snapshot, self.active_agent_id)

    def enabled(self, destination: Destination) -> bool:
        if self._agents[self.active_agent_id]["role"] != "routing":
            return True
        return enabled(destination, self._pending_destination)

    async def settled(self) -> TestSlot:
        """Return the slot once any transition this policy is applying has finished."""
        async with self._lock:
            return self.slot

    def _source(
        self, agent_id: str | None = None, agent_epoch: int | None = None
    ) -> dict[str, Any]:
        if (agent_id is not None and agent_id != self.active_agent_id) or (
            agent_epoch is not None and agent_epoch != self.agent_epoch
        ):
            raise PermissionError("stale_agent_epoch")
        return self._agents[self.active_agent_id]

    async def _move(self, destination: str, community: str | None, work: WorkBudget) -> None:
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

    def record(
        self,
        destination_id: str,
        path: HandoverPath,
        *,
        community_id: str | None = None,
        reason: HandoverReason = "applied",
    ) -> Handover:
        """The Handover record of a request by the active agent, applied or refused."""
        source = self._agents[self.active_agent_id]
        destination = self._agents.get(destination_id, source)
        target = community_id if path == "routing" and community_id is not None else None
        if target is None:
            target = destination.get("communityId")
        return Handover.model_validate(
            {
                "sourceAgentId": source["agentId"],
                "sourceRole": source["role"],
                "destinationAgentId": destination["agentId"],
                "destinationRole": destination["role"],
                "targetCommunityId": target,
                "path": path,
                "outcome": "applied" if reason == "applied" else "refused",
                "reason": reason,
            }
        )

    async def handover(
        self,
        destination_id: str,
        path: HandoverPath,
        work: WorkBudget,
        *,
        community_id: str | None = None,
        source_agent_id: str | None = None,
        source_agent_epoch: int | None = None,
    ) -> Handover:
        """Apply one handover of the active agent (contracts/handover.md, applying a handover)."""
        async with self._lock:
            source = self._source(source_agent_id, source_agent_epoch)
            if source["role"] == "welcome":
                # Welcome never runs a request; the application moves it to Routing (R7).
                if (
                    path != "lifecycle"
                    or community_id is not None
                    or destination_id != self.visibility.lifecycle_target("routing")
                ):
                    raise PermissionError("handover_not_allowed")
                community, pending = None, None
            else:
                offered = next(
                    (
                        item
                        for item in self.offered()
                        if item.agent_id == destination_id and item.path == path
                    ),
                    None,
                )
                if offered is None or not self.enabled(offered):
                    raise PermissionError("handover_not_allowed")
                if offered.community_choices:
                    # Ordinary to Routing names its target; the position keeps its community.
                    if community_id not in offered.community_choices:
                        raise PermissionError("handover_not_allowed")
                    community, pending = self.active_community_id, community_id
                elif community_id is not None:
                    raise PermissionError("handover_not_allowed")
                elif offered.role == "goodbye":
                    community, pending = self.active_community_id, None
                else:
                    community, pending = offered.community_id, None
            record = self.record(destination_id, path, community_id=community_id)
            await self._move(destination_id, community, work)
            self._pending_destination = pending
            return record

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


class SpeechSegments:
    """The one Live session of a voice test; it is settled only when the test ends (FR-013)."""

    def __init__(self, *, current: GptLiveSession) -> None:
        self._current = current
        self._adapter = current.adapter
        self._failed = False
        self._session_id, self._owner_epoch = current.intent.session_id, current.intent.owner_epoch

    @property
    def current(self) -> GptLiveSession:
        if self._failed:
            raise PermissionError("live_unavailable")
        return self._current

    async def _fail(self) -> None:
        self._failed = True
        try:
            async with asyncio.timeout(1):
                await self._adapter.admission.mark_terminal(self._session_id, self._owner_epoch)
        except Exception:
            pass

    async def next_event(self) -> tuple[int, dict[str, Any]]:
        if self._failed:
            raise PermissionError("live_unavailable")
        try:
            event = await self._current.next_event()
        except PermissionError:
            await self._fail()
            raise PermissionError("live_unavailable") from None
        if event["type"] == "session.closed":
            telemetry = self._adapter._telemetry
            if telemetry is not None:
                # The reason is one of the provider's fixed close reasons, checked on receipt.
                telemetry.diagnostic(
                    "test.live.failure",
                    mode="speech",
                    error="LiveClosed",
                    code="closed_" + event["reason"],
                )
            await self._fail()
            raise PermissionError("live_unavailable")
        return self._current.intent.generation, event
