"""Stored text conversation: owner-keyed text turns plus the active agent position.

This is the only conversation content the runtime persists. It holds user and assistant text
only (never audio, transcripts or tool payloads), is bounded by the conversation context limit,
and expires through the store's item expiry. Only the owning manager's key can address it.
"""

from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from caller_runtime.domain.conversation import AwareTime, Digest, Identifier, Metadata

MAX_HISTORY_BYTES = 256 * 1024
MAX_TURN_BYTES = 64 * 1024
MAX_TURNS = 512


class StoredTurn(Metadata):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_TURN_BYTES, repr=False)


class TextConversation(Metadata):
    id: Identifier
    owner_key: Digest = Field(alias="ownerKey")
    turns: list[StoredTurn] = Field(max_length=MAX_TURNS, repr=False)
    active_agent_id: Identifier = Field(alias="activeAgentId")
    active_community_id: Identifier | None = Field(alias="activeCommunityId")
    created_at: AwareTime = Field(alias="createdAt")
    updated_at: AwareTime = Field(alias="updatedAt")

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if sum(len(turn.content.encode("utf-8")) for turn in self.turns) > MAX_HISTORY_BYTES:
            raise ValueError("conversation_context_limit")
        if self.updated_at < self.created_at:
            raise ValueError("invalid_conversation_times")
        return self

    def messages(self) -> list[dict[str, str]]:
        """Return the turns in the shape the model runner takes as conversation input."""
        return [{"role": turn.role, "content": turn.content} for turn in self.turns]


def new_conversation_id() -> str:
    return uuid4().hex
