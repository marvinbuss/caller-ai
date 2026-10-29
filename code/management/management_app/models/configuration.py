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


class Community(ConfigurationModel):
    community_id: Identifier = Field(alias="communityId")
    name: Name
    routing_description: str = Field(alias="routingDescription")
    enabled: bool
    entry_agent_id: Identifier = Field(alias="entryAgentId")


class Relationship(ConfigurationModel):
    relationship_id: Identifier = Field(alias="relationshipId")
    source_agent_id: Identifier = Field(alias="sourceAgentId")
    target_agent_id: Identifier = Field(alias="targetAgentId")
    allowed_context_fields: list[Literal["request_summary", "transfer_reason"]] = Field(
        alias="allowedContextFields", max_length=2
    )


class ConfigurationGraph(ConfigurationModel):
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    agents: list[Agent]
    communities: list[Community]
    relationships: list[Relationship]
    tools: list[ToolDefinition]
    mcp_connections: list[MCPConnection] = Field(alias="mcpConnections")


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
