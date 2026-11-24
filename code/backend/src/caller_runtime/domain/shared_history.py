"""The shared history of one conversation: typed, untrusted entries that every agent continues with.

One `SharedHistory` holds what happened in a conversation, in both modalities (feature 007,
FR-001 to FR-006): user text and caller speech, the voice assistant's spoken transcript, agent
answers and voice delegation results, tool calls and results, final or waiting progress, and every
handover with its outcome. It renders to Responses input items exactly as
`specs/007-shared-conversation-history/contracts/shared-history.md` defines, so every model step
can be built from it without stored model state.

It never holds agent instructions, layers, voice instructions, delegation updates, tool
definitions, reasoning items, credentials or configuration: there is no entry kind for them. Every
entry's content is untrusted and is rendered as conversation, never as instructions. Voice entries
are ordered by session time (`at_ms`); text entries by insertion. One entry holds at most 64 KiB,
and a history at most 2,048 entries; condensing (R11) keeps it below its limits.
"""

import json
from bisect import bisect_right
from collections.abc import Iterable
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from caller_runtime.domain.conversation import Identifier, Metadata

CONTENT_LIMIT = 64 * 1024
ENTRY_LIMIT = 2048
CONDENSE_ABOVE = 192 * 1024
CONDENSE_BELOW = 128 * 1024
HISTORY_LIMIT = 256 * 1024
CUT_MARK = "[cut]"

Kind = Literal[
    "user_text",
    "caller_speech",
    "assistant_speech",
    "agent_answer",
    "delegation_result",
    "tool_call",
    "tool_result",
    "handover",
    "progress",
    "condensed",
]
Delivery = Literal["transcribed", "partial", "not_delivered"]
ProgressState = Literal["completed", "failed", "cancellation_confirmed", "waiting_for_input"]
Role = Literal["welcome", "routing", "goodbye", "ordinary"]
HandoverPath = Literal["peer", "direct", "routing", "lifecycle"]
HandoverReason = Literal[
    "applied", "refused_graph", "refused_stale", "refused_budget", "refused_step_limit"
]
TOOL_ITEMS = frozenset({"tool_call", "tool_result", "handover"})
DELIVERIES: dict[str, frozenset[str | None]] = {
    "caller_speech": frozenset({"transcribed", "partial"}),
    "assistant_speech": frozenset({"transcribed"}),
    "delegation_result": frozenset({None, "not_delivered"}),
}


class Handover(Metadata):
    """One agent change: its source, destination, path and outcome (FR-002, FR-008)."""

    source_agent_id: Identifier = Field(alias="sourceAgentId")
    source_role: Role = Field(alias="sourceRole")
    destination_agent_id: Identifier = Field(alias="destinationAgentId")
    destination_role: Role = Field(alias="destinationRole")
    target_community_id: Identifier | None = Field(default=None, alias="targetCommunityId")
    path: HandoverPath
    outcome: Literal["applied", "refused"]
    reason: HandoverReason

    @model_validator(mode="after")
    def outcome_matches_reason(self) -> Self:
        if (self.outcome == "applied") != (self.reason == "applied"):
            raise ValueError("handover_outcome_mismatch")
        return self


class HistoryEntry(Metadata):
    """One untrusted item of the shared history; also its stored form in text conversations."""

    kind: Kind
    content: str = Field(repr=False)
    agent_id: Identifier | None = Field(default=None, alias="agentId")
    community_id: Identifier | None = Field(default=None, alias="communityId")
    at_ms: float | None = Field(default=None, alias="atMs", ge=0, le=3_600_000, allow_inf_nan=False)
    delivery: Delivery | None = None
    call_id: str | None = Field(default=None, alias="callId", pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    handover: Handover | None = None
    state: ProgressState | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        size = len(self.content.encode("utf-8"))
        if size > CONTENT_LIMIT or (size == 0 and self.kind != "tool_result"):
            raise ValueError("history_entry_content")
        tool = self.kind in TOOL_ITEMS
        if tool != (self.call_id is not None and self.name is not None):
            raise ValueError("history_entry_call")
        if (self.kind == "handover") != (self.handover is not None):
            raise ValueError("history_entry_handover")
        if (self.kind == "progress") != (self.state is not None):
            raise ValueError("history_entry_state")
        if self.delivery not in DELIVERIES.get(self.kind, frozenset({None})):
            raise ValueError("history_entry_delivery")
        return self

    def items(self) -> list[dict[str, Any]]:
        """The Responses input items of this entry (contracts/shared-history.md)."""
        if self.kind == "user_text":
            return [{"role": "user", "content": self.content}]
        if self.kind == "caller_speech":
            rest = " [rest not transcribed]" if self.delivery == "partial" else ""
            return [{"role": "user", "content": "[Caller, spoken] " + self.content + rest}]
        if self.kind == "assistant_speech":
            return [{"role": "assistant", "content": "[Voice assistant, spoken] " + self.content}]
        if self.kind == "agent_answer":
            return [{"role": "assistant", "content": self.content}]
        if self.kind == "delegation_result":
            prefix = (
                "[Not delivered] "
                if self.delivery == "not_delivered"
                else "[Result given to the voice assistant] "
            )
            return [{"role": "assistant", "content": prefix + self.content}]
        if self.kind == "tool_call":
            return [
                {
                    "type": "function_call",
                    "call_id": self.call_id,
                    "name": self.name,
                    "arguments": self.content,
                }
            ]
        if self.kind == "tool_result":
            return [
                {"type": "function_call_output", "call_id": self.call_id, "output": self.content}
            ]
        if self.kind == "handover" and self.handover is not None:
            output = (
                "applied"
                if self.handover.outcome == "applied"
                else "refused: " + self.handover.reason
            )
            return [
                {
                    "type": "function_call",
                    "call_id": self.call_id,
                    "name": self.name,
                    "arguments": self.content,
                },
                {"type": "function_call_output", "call_id": self.call_id, "output": output},
            ]
        if self.kind == "progress":
            return [{"role": "assistant", "content": f"Status ({self.state}): {self.content}"}]
        return [
            {
                "role": "user",
                "content": "[Condensed earlier conversation, untrusted]\n" + self.content,
            }
        ]


def bounded(content: str) -> str:
    """Cut content above the entry limit at a character boundary and mark it with `[cut]`."""
    data = content.encode("utf-8")
    if len(data) <= CONTENT_LIMIT:
        return content
    return data[: CONTENT_LIMIT - len(CUT_MARK)].decode("utf-8", errors="ignore") + CUT_MARK


class SharedHistory:
    """The ordered, bounded entries of one conversation (data-model.md, SharedHistory)."""

    def __init__(self, entries: Iterable[HistoryEntry] = (), *, timeline: bool = False) -> None:
        self._timeline = timeline
        self._entries: list[HistoryEntry] = []
        for entry in entries:
            self.record(entry)

    @property
    def entries(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._entries)

    @property
    def timeline(self) -> bool:
        """True in voice: entries are ordered by session time, not by insertion."""
        return self._timeline

    @property
    def condensed(self) -> bool:
        return any(entry.kind == "condensed" for entry in self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def record(self, entry: HistoryEntry) -> HistoryEntry:
        if len(self._entries) >= ENTRY_LIMIT:
            raise ValueError("history_entry_limit")
        if self._timeline:
            if entry.at_ms is None:
                raise ValueError("history_time_required")
            index = bisect_right(self._entries, entry.at_ms, key=lambda item: item.at_ms or 0.0)
            self._entries.insert(index, entry)
        else:
            if entry.at_ms is not None:
                raise ValueError("history_time_forbidden")
            self._entries.append(entry)
        return entry

    def _add(self, kind: Kind, content: str, **fields: Any) -> HistoryEntry:
        return self.record(HistoryEntry(kind=kind, content=bounded(content), **fields))

    def record_user_text(
        self, content: str, *, agent_id: str | None = None, community_id: str | None = None
    ) -> HistoryEntry:
        return self._add("user_text", content, agent_id=agent_id, community_id=community_id)

    def record_caller_speech(
        self, content: str, *, at_ms: float, delivery: Delivery = "transcribed"
    ) -> HistoryEntry:
        return self._add("caller_speech", content, at_ms=at_ms, delivery=delivery)

    def record_assistant_speech(self, content: str, *, at_ms: float) -> HistoryEntry:
        return self._add("assistant_speech", content, at_ms=at_ms, delivery="transcribed")

    def record_agent_answer(
        self, content: str, *, agent_id: str, community_id: str | None = None
    ) -> HistoryEntry:
        return self._add("agent_answer", content, agent_id=agent_id, community_id=community_id)

    def record_delegation_result(
        self,
        content: str,
        *,
        at_ms: float,
        agent_id: str,
        community_id: str | None = None,
        delivery: Literal["not_delivered"] | None = None,
    ) -> HistoryEntry:
        return self._add(
            "delegation_result",
            content,
            at_ms=at_ms,
            agent_id=agent_id,
            community_id=community_id,
            delivery=delivery,
        )

    def record_tool_call(
        self,
        name: str,
        call_id: str,
        arguments: str,
        *,
        agent_id: str,
        community_id: str | None = None,
        at_ms: float | None = None,
    ) -> HistoryEntry:
        return self._add(
            "tool_call",
            arguments,
            name=name,
            call_id=call_id,
            agent_id=agent_id,
            community_id=community_id,
            at_ms=at_ms,
        )

    def record_tool_result(
        self,
        name: str,
        call_id: str,
        output: str,
        *,
        agent_id: str,
        community_id: str | None = None,
        at_ms: float | None = None,
    ) -> HistoryEntry:
        return self._add(
            "tool_result",
            output,
            name=name,
            call_id=call_id,
            agent_id=agent_id,
            community_id=community_id,
            at_ms=at_ms,
        )

    def record_handover(
        self,
        handover: Handover,
        call_id: str,
        name: str,
        arguments: str,
        *,
        agent_id: str,
        community_id: str | None = None,
        at_ms: float | None = None,
    ) -> HistoryEntry:
        return self._add(
            "handover",
            arguments,
            handover=handover,
            name=name,
            call_id=call_id,
            agent_id=agent_id,
            community_id=community_id,
            at_ms=at_ms,
        )

    def record_progress(
        self,
        state: ProgressState,
        content: str,
        *,
        agent_id: str,
        community_id: str | None = None,
        at_ms: float | None = None,
    ) -> HistoryEntry:
        return self._add(
            "progress",
            content,
            state=state,
            agent_id=agent_id,
            community_id=community_id,
            at_ms=at_ms,
        )

    def record_condensed(self, content: str, *, at_ms: float | None = None) -> HistoryEntry:
        return self._add("condensed", content, at_ms=at_ms)

    def render(self) -> list[dict[str, Any]]:
        """Responses input items for a model step, in order; never instructions or reasoning."""
        return [item for entry in self._entries for item in entry.items()]

    @property
    def rendered_bytes(self) -> int:
        rendered = json.dumps(self.render(), ensure_ascii=False, separators=(",", ":"))
        return len(rendered.encode("utf-8"))

    def needs_condensing(self) -> bool:
        return self.rendered_bytes > CONDENSE_ABOVE or len(self._entries) >= ENTRY_LIMIT
