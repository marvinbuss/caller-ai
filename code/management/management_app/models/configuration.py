"""Pydantic models of the configuration graph and its immutable revisions.

An agent has one of four roles (welcome, routing, goodbye, ordinary), belongs to at most one
community and carries bounded text (prompts up to 16 KiB). Models forbid unknown fields, so a
submitted graph cannot smuggle extra data into a revision. A published revision is immutable and
numbered; the active pointer is a separate document.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, Field, StringConstraints, field_validator

from management_app.models.capabilities import (
    CapabilityAssignment,
    ConfigurationModel,
    Identifier,
    MCPConnection,
    Name,
    ToolDefinition,
)


def _absent(value: object) -> bool:
    return value is None


def _no_nul(value: str | None) -> str | None:
    if value is not None and "\x00" in value:
        raise ValueError("nul_character")
    return value


# Schema 2.2.0 text fields; an absent field means the shipped default (`default_prompts.py`).
# Their byte limits are checked by the validator, which names the field (FR-033).
OptionalText = Annotated[str | None, AfterValidator(_no_nul)]


class Agent(ConfigurationModel):
    agent_id: Identifier = Field(alias="agentId")
    name: Name
    prompt: str
    enabled: bool
    role: Literal["ordinary", "welcome", "routing", "goodbye"]
    community_id: Identifier | None = Field(alias="communityId")
    capability_assignments: list[CapabilityAssignment] = Field(alias="capabilityAssignments")
    voice_addition: OptionalText = Field(default=None, alias="voiceAddition", exclude_if=_absent)
    text_addition: OptionalText = Field(default=None, alias="textAddition", exclude_if=_absent)

    @field_validator("prompt")
    @classmethod
    def bounded_prompt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 16 * 1024:
            raise ValueError("agent_prompt_too_large")
        return value


class Community(ConfigurationModel):
    community_id: Identifier = Field(alias="communityId")
    name: Name
    routing_description: str = Field(alias="routingDescription")
    enabled: bool
    entry_agent_id: Identifier = Field(alias="entryAgentId")
    routable: bool = True

    @field_validator("routing_description")
    @classmethod
    def bounded_description(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 4 * 1024:
            raise ValueError("community_description_too_large")
        return value


class Relationship(ConfigurationModel):
    relationship_id: Identifier = Field(alias="relationshipId")
    source_agent_id: Identifier = Field(alias="sourceAgentId")
    target_agent_id: Identifier = Field(alias="targetAgentId")
    allowed_context_fields: list[Literal["request_summary", "transfer_reason"]] = Field(
        alias="allowedContextFields", max_length=2
    )


class CommunityConnection(ConfigurationModel):
    connection_id: Identifier = Field(alias="connectionId")
    source_community_id: Identifier = Field(alias="sourceCommunityId")
    target_community_id: Identifier = Field(alias="targetCommunityId")
    allowed_context_fields: list[Literal["request_summary", "transfer_reason"]] = Field(
        alias="allowedContextFields", max_length=2
    )


class GraphPosition(ConfigurationModel):
    node_id: Identifier = Field(alias="nodeId")
    x: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    y: float = Field(ge=-100000, le=100000, allow_inf_nan=False)


class GraphLayout(ConfigurationModel):
    communities: list[GraphPosition]
    agents_by_community: dict[Identifier, list[GraphPosition]] = Field(alias="agentsByCommunity")


class ConversationSettings(ConfigurationModel):
    """Per-conversation values; an absent field means the runtime's environment default, or for
    the 2.2.0 text fields and `quietHandover` the shipped default."""

    voice: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")] | None = Field(
        default=None, exclude_if=_absent
    )
    max_output_tokens: int | None = Field(
        default=None, alias="maxOutputTokens", ge=1, le=4096, exclude_if=_absent
    )
    idle_timeout_seconds: int | None = Field(
        default=None, alias="idleTimeoutSeconds", ge=30, le=120, exclude_if=_absent
    )
    voice_instructions: OptionalText = Field(
        default=None, alias="voiceInstructions", exclude_if=_absent
    )
    voice_platform_rules: OptionalText = Field(
        default=None, alias="voicePlatformRules", exclude_if=_absent
    )
    voice_global_prompt: OptionalText = Field(
        default=None, alias="voiceGlobalPrompt", exclude_if=_absent
    )
    text_platform_rules: OptionalText = Field(
        default=None, alias="textPlatformRules", exclude_if=_absent
    )
    text_global_prompt: OptionalText = Field(
        default=None, alias="textGlobalPrompt", exclude_if=_absent
    )
    quiet_handover: bool | None = Field(default=None, alias="quietHandover", exclude_if=_absent)


EMPTY_LAYOUT: Final[dict[str, Any]] = {"communities": [], "agentsByCommunity": {}}
SCHEMA_VERSION: Final = "2.1.0"
MODALITY_SCHEMA_VERSION: Final = "2.2.0"
# The 2.2.0 fields: their presence alone makes a published revision 2.2.0.
MODALITY_SETTINGS: Final = (
    "voiceInstructions",
    "voicePlatformRules",
    "voiceGlobalPrompt",
    "textPlatformRules",
    "textGlobalPrompt",
    "quietHandover",
)
AGENT_ADDITIONS: Final = ("voiceAddition", "textAddition")


def uses_modality_fields(graph: dict[str, Any]) -> bool:
    settings = graph.get("conversationSettings") or {}
    return any(key in settings for key in MODALITY_SETTINGS) or any(
        key in agent for agent in graph.get("agents", []) for key in AGENT_ADDITIONS
    )


class ConfigurationGraph(ConfigurationModel):
    schema_version: Literal["2.0.0", "2.1.0", "2.2.0"] = Field(alias="schemaVersion")
    agents: list[Agent] = Field(max_length=200)
    communities: list[Community] = Field(max_length=50)
    relationships: list[Relationship] = Field(max_length=1000)
    community_connections: list[CommunityConnection] = Field(
        alias="communityConnections", max_length=1000
    )
    layout: GraphLayout
    tools: list[ToolDefinition] = Field(max_length=256)
    mcp_connections: list[MCPConnection] = Field(alias="mcpConnections", max_length=128)
    conversation_settings: ConversationSettings | None = Field(
        default=None, alias="conversationSettings", exclude_if=_absent
    )


class AgentStamp(ConfigurationModel):
    """When and by whom an agent last changed in a published revision."""

    revision_number: int = Field(alias="revisionNumber", ge=1)
    changed_at: str = Field(alias="changedAt", max_length=64)
    changed_by: Identifier = Field(alias="changedBy")
    changed_by_name: str = Field(alias="changedByName", max_length=256)


class PublicationRecord(ConfigurationModel):
    """Editor-only publication metadata; outside both hashes and the runtime projection."""

    published_at: str = Field(alias="publishedAt", max_length=64)
    published_by: Identifier = Field(alias="publishedBy")
    published_by_name: str = Field(alias="publishedByName", max_length=256)
    agent_stamps: dict[Identifier, AgentStamp] = Field(alias="agentStamps", max_length=200)


class ConfigurationRevision(ConfigurationGraph):
    id: Identifier
    organization_id: Literal["default"] = Field(alias="organizationId")
    revision_number: int = Field(alias="revisionNumber", ge=1)
    created_at: datetime = Field(alias="createdAt")
    created_by: Identifier = Field(alias="createdBy")
    graph_hash: str = Field(alias="graphHash", pattern=r"^[0-9a-f]{64}$")
    publication: PublicationRecord | None = Field(default=None, exclude_if=_absent)

    @field_validator("created_at")
    @classmethod
    def aware_creation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aware_timestamp_required")
        return value.astimezone(UTC)


class ActiveConfiguration(ConfigurationModel):
    id: Literal["active"] = "active"
    organization_id: Literal["default"] = Field(default="default", alias="organizationId")
    revision_id: Identifier = Field(alias="revisionId")
    revision_number: int = Field(alias="revisionNumber", ge=1)
    published_at: datetime = Field(alias="publishedAt")
    etag: str = Field(alias="_etag")

    @field_validator("published_at")
    @classmethod
    def aware_publication_time(cls, value: datetime) -> datetime:
        return ConfigurationRevision.aware_creation_time(value)
