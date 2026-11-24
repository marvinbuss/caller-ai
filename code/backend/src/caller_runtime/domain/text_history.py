"""Stored text conversation (version 2): the owner-keyed shared history and the agent position.

This is the only conversation content the runtime persists. Version 2 (feature 007) holds every
shared history entry of the conversation: user text, agent answers, tool calls and results,
handovers and final or waiting progress. It never holds audio, transcripts, instructions or tool
definitions. It is bounded by the conversation context limit (256 KiB rendered, 2,048 entries),
expires through the store's item expiry and can only be addressed with the owning manager's key.
A stored document without `historyVersion` is from before this release and is never resumed.
"""

from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from caller_runtime.domain.conversation import AwareTime, Digest, Identifier, Metadata
from caller_runtime.domain.shared_history import (
    ENTRY_LIMIT,
    HISTORY_LIMIT,
    HistoryEntry,
    SharedHistory,
)

MAX_HISTORY_BYTES = HISTORY_LIMIT


class TextConversation(Metadata):
    id: Identifier
    owner_key: Digest = Field(alias="ownerKey")
    history_version: Literal[2] = Field(alias="historyVersion")
    entries: list[HistoryEntry] = Field(max_length=ENTRY_LIMIT, repr=False)
    active_agent_id: Identifier = Field(alias="activeAgentId")
    active_community_id: Identifier | None = Field(alias="activeCommunityId")
    # A transfer through Routing in progress survives between requests (FR-008).
    pending_destination: Identifier | None = Field(default=None, alias="pendingDestination")
    created_at: AwareTime = Field(alias="createdAt")
    updated_at: AwareTime = Field(alias="updatedAt")

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if any(entry.at_ms is not None for entry in self.entries):
            raise ValueError("invalid_text_history")
        if SharedHistory(self.entries).rendered_bytes > MAX_HISTORY_BYTES:
            raise ValueError("conversation_context_limit")
        if self.updated_at < self.created_at:
            raise ValueError("invalid_conversation_times")
        return self


def new_conversation_id() -> str:
    return uuid4().hex
