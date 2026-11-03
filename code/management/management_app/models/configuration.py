"""Single-organization declarative graphs and immutable-publication envelopes."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator

from management_app.models.capabilities import (
    CapabilityAssignment,
    ConfigurationModel,
    Identifier,
    MCPConnection,
    Name,
    ToolDefinition,
)


class Agent(ConfigurationModel):
    agent_id: Identifier = Field(alias="agentId")
    name: Name
    prompt: str
    enabled: bool
    role: Literal["ordinary", "welcome", "routing", "goodbye"]
    community_id: Identifier | None = Field(alias="communityId")
    capability_assignments: list[CapabilityAssignment] = Field(alias="capabilityAssignments")

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


class ConfigurationGraph(ConfigurationModel):
    schema_version: Literal["2.0.0"] = Field(alias="schemaVersion")
    agents: list[Agent] = Field(max_length=200)
    communities: list[Community] = Field(max_length=50)
    relationships: list[Relationship] = Field(max_length=1000)
    community_connections: list[CommunityConnection] = Field(
        alias="communityConnections", max_length=1000
    )
    layout: GraphLayout
    tools: list[ToolDefinition] = Field(max_length=256)
    mcp_connections: list[MCPConnection] = Field(alias="mcpConnections", max_length=128)


class ConfigurationRevision(ConfigurationGraph):
    id: Identifier
    organization_id: Literal["default"] = Field(alias="organizationId")
    revision_number: int = Field(alias="revisionNumber", ge=1)
    created_at: datetime = Field(alias="createdAt")
    created_by: Identifier = Field(alias="createdBy")
    graph_hash: str = Field(alias="graphHash", pattern=r"^[0-9a-f]{64}$")

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
