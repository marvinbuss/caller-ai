"""Declarative public read-only capability configuration, never executable grants."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, TypeAdapter

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True, hide_input_in_errors=True
    )


class CapabilityAssignment(ConfigurationModel):
    agent_id: Identifier = Field(alias="agentId")
    tool_id: Identifier = Field(alias="toolId")
    permitted_action_names: list[Name] = Field(alias="permittedActionNames", min_length=1)
    allowed_input_constraints: dict[str, JsonValue] = Field(alias="allowedInputConstraints")


class ReviewedCapability(ConfigurationModel):
    action_name: Name = Field(alias="actionName")
    input_schema: dict[str, JsonValue] = Field(alias="inputSchema")
    output_allowlist: list[Name] = Field(alias="outputAllowlist", min_length=1)
    effect: Literal["read_only"]
    classification: Literal["public"]


class ToolDefinition(ConfigurationModel):
    tool_id: Identifier = Field(alias="toolId")
    name: Name
    description: str
    schema_version: Literal["1.0.0"] = Field(alias="schemaVersion")
    input_schema: dict[str, JsonValue] = Field(alias="inputSchema")
    output_allowlist: list[Name] = Field(alias="outputAllowlist", min_length=1)
    enabled: bool
    approved_destination_ref: Identifier = Field(alias="approvedDestinationRef")
    mcp_connection_id: Identifier | None = Field(default=None, alias="mcpConnectionId")
    effect: Literal["read_only"]
    classification: Literal["public"]


class MCPConnection(ConfigurationModel):
    connection_id: Identifier = Field(alias="connectionId")
    name: Name
    approved_endpoint_ref: Identifier = Field(alias="approvedEndpointRef")
    enabled: bool
    credential_ref: str = Field(alias="credentialRef")
    reviewed_capability_catalog: list[ReviewedCapability] = Field(alias="reviewedCapabilityCatalog")


@dataclass(frozen=True, slots=True)
class ApprovedCapabilities:
    """Trusted server review input, never constructed from a manager's submitted graph."""

    _tools: bytes = field(default=b"[]", repr=False)
    _connections: bytes = field(default=b"[]", repr=False)
    _direct_actions: bytes = field(default=b"{}", repr=False)
    credential_refs: tuple[str, ...] = ()

    @classmethod
    def from_reviewed(
        cls,
        graph: dict[str, Any],
        *,
        direct_actions: Mapping[str, Sequence[str]] | None = None,
        credential_refs: Sequence[str] | None = None,
    ) -> Self:
        tools = [
            ToolDefinition.model_validate(item).model_dump(mode="json", by_alias=True)
            for item in graph.get("tools", [])
        ]
        connections = [
            MCPConnection.model_validate(item).model_dump(mode="json", by_alias=True)
            for item in graph.get("mcpConnections", [])
        ]
        if len(tools) > 256 or len(connections) > 128:
            raise ValueError("reviewed_catalog_too_large")
        for rows, key in ((tools, "toolId"), (connections, "connectionId")):
            if len({row[key] for row in rows}) != len(rows):
                raise ValueError("duplicate_reviewed_capability")
        connection_ids = {row["connectionId"] for row in connections}
        if any(
            tool["mcpConnectionId"] is not None and tool["mcpConnectionId"] not in connection_ids
            for tool in tools
        ):
            raise ValueError("unreviewed_capability")
        refs = tuple(
            sorted(
                set(
                    credential_refs
                    if credential_refs is not None
                    else [row["credentialRef"] for row in connections]
                )
            )
        )
        if any(not re.fullmatch(r"secret://[a-z][a-z0-9-]{2,62}", ref) for ref in refs):
            raise ValueError("invalid_reviewed_credential_reference")
        if any(row["credentialRef"] not in refs for row in connections):
            raise ValueError("unreviewed_credential_reference")
        direct = {
            key: TypeAdapter(list[Name]).validate_python(list(values))
            for key, values in (direct_actions or {}).items()
        }
        direct_ids = {tool["toolId"] for tool in tools if tool["mcpConnectionId"] is None}
        if not direct.keys() <= direct_ids or any(
            len(values) != len(set(values)) for values in direct.values()
        ):
            raise ValueError("unreviewed_capability")
        documents = [
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            for value in (tools, connections, direct)
        ]
        if sum(map(len, documents)) > 512 * 1024:
            raise ValueError("reviewed_catalog_too_large")
        return cls(documents[0], documents[1], documents[2], refs)

    @property
    def tools(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = json.loads(self._tools)
        return rows

    @property
    def connections(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = json.loads(self._connections)
        return rows

    def tool(self, identifier: str) -> dict[str, Any]:
        for item in self.tools:
            if item["toolId"] == identifier:
                return item
        raise ValueError("unreviewed_capability")

    def connection(self, identifier: str) -> dict[str, Any]:
        for item in self.connections:
            if item["connectionId"] == identifier:
                return item
        raise ValueError("unreviewed_capability")

    def actions(self, tool_id: str) -> list[str]:
        tool = self.tool(tool_id)
        if tool["mcpConnectionId"] is not None:
            connection = self.connection(tool["mcpConnectionId"])
            return [item["actionName"] for item in connection["reviewedCapabilityCatalog"]]
        direct: dict[str, list[str]] = json.loads(self._direct_actions)
        return list(direct.get(tool_id, []))

    def permits_tool(self, tool: ToolDefinition) -> bool:
        try:
            approved = self.tool(tool.tool_id)
        except ValueError:
            return False
        submitted = tool.model_dump(mode="json", by_alias=True)
        return all(
            submitted[key] == value
            for key, value in approved.items()
            if key not in {"name", "description", "enabled"}
        )

    def permits_connection(self, connection: MCPConnection) -> bool:
        try:
            approved = self.connection(connection.connection_id)
        except ValueError:
            return False
        submitted = connection.model_dump(mode="json", by_alias=True)
        return (
            connection.credential_ref in self.credential_refs
            and all(
                item in approved["reviewedCapabilityCatalog"]
                for item in submitted["reviewedCapabilityCatalog"]
            )
            and all(
                submitted[key] == value
                for key, value in approved.items()
                if key not in {"name", "enabled", "credentialRef", "reviewedCapabilityCatalog"}
            )
        )
