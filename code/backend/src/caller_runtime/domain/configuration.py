"""Immutable service-authenticated ordinary revisions, never current permission authority."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from caller_runtime.domain.visibility import CommunityVisibility

GRAPH_FIELDS = frozenset(
    {"schemaVersion", "agents", "communities", "relationships", "tools", "mcpConnections"}
)
ETAG = re.compile(r'^"[A-Za-z0-9_-]{1,128}"$')
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Name = Annotated[str, Field(min_length=1, max_length=128)]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class _Assignment(_Strict):
    agentId: Identifier
    toolId: Identifier
    permittedActionNames: list[Name] = Field(min_length=1)
    allowedInputConstraints: dict[str, JsonValue]


class _Agent(_Strict):
    agentId: Identifier
    name: Name
    prompt: str
    enabled: bool
    role: Literal["ordinary", "welcome", "routing", "goodbye"]
    communityId: Identifier | None
    capabilityAssignments: list[_Assignment]


class _Community(_Strict):
    communityId: Identifier
    name: Name
    routingDescription: str
    enabled: bool
    entryAgentId: Identifier


class _Relationship(_Strict):
    relationshipId: Identifier
    sourceAgentId: Identifier
    targetAgentId: Identifier
    allowedContextFields: list[Literal["request_summary", "transfer_reason"]] = Field(max_length=2)


class _ReviewedCapability(_Strict):
    actionName: Name
    inputSchema: dict[str, JsonValue]
    outputAllowlist: list[Name] = Field(min_length=1)
    effect: Literal["read_only"]
    classification: Literal["public"]


class _Tool(_Strict):
    toolId: Identifier
    name: Name
    description: str
    schemaVersion: Literal["1.0.0"]
    inputSchema: dict[str, JsonValue]
    outputAllowlist: list[Name] = Field(min_length=1)
    enabled: bool
    approvedDestinationRef: Identifier
    mcpConnectionId: Identifier | None = None
    effect: Literal["read_only"]
    classification: Literal["public"]


class _Connection(_Strict):
    connectionId: Identifier
    name: Name
    approvedEndpointRef: Identifier
    enabled: bool
    credentialRef: str = Field(pattern=r"^secret://[a-z][a-z0-9-]{2,62}$")
    reviewedCapabilityCatalog: list[_ReviewedCapability]


class _Revision(_Strict):
    id: Identifier
    organizationId: Literal["default"]
    schemaVersion: Literal["1.0.0"]
    revisionNumber: int = Field(ge=1)
    createdAt: datetime
    createdBy: Identifier
    graphHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    agents: list[_Agent]
    communities: list[_Community]
    relationships: list[_Relationship]
    tools: list[_Tool]
    mcpConnections: list[_Connection]


def encoded(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


@dataclass(frozen=True, slots=True)
class RevisionSnapshot:
    revision_id: str
    revision_number: int
    graph_hash: str
    schema_version: str
    etag: str
    _content: bytes = field(repr=False)

    @classmethod
    def validate(cls, document: dict[str, Any], *, etag: str) -> "RevisionSnapshot":
        try:
            if not isinstance(etag, str) or not ETAG.fullmatch(etag):
                raise ValueError("invalid_configuration_etag")
            content = encoded(document)
            if len(content) > 512 * 1024:
                raise ValueError("oversized_configuration")
            model = _Revision.model_validate_json(content)
            if model.createdAt.tzinfo is None or model.createdAt.utcoffset() is None:
                raise ValueError("invalid_revision_time")
            detached: dict[str, Any] = json.loads(content)
            graph = {key: detached[key] for key in GRAPH_FIELDS}
            if hashlib.sha256(encoded(graph)).hexdigest() != model.graphHash:
                raise ValueError("invalid_configuration_hash")
            tools = {tool.toolId: tool for tool in model.tools}
            connections = {
                connection.connectionId: connection for connection in model.mcpConnections
            }
            if len(tools) != len(model.tools) or len(connections) != len(model.mcpConnections):
                raise ValueError("duplicate_configuration_catalog")
            if any(
                tool.mcpConnectionId is not None and tool.mcpConnectionId not in connections
                for tool in model.tools
            ):
                raise ValueError("unknown_configuration_connection")
            for agent in model.agents:
                if agent.role != "ordinary" and agent.capabilityAssignments:
                    raise ValueError("protected_capability_assignment")
                seen = set()
                for assignment in agent.capabilityAssignments:
                    if (
                        assignment.agentId != agent.agentId
                        or assignment.toolId not in tools
                        or assignment.toolId in seen
                        or len(assignment.permittedActionNames)
                        != len(set(assignment.permittedActionNames))
                    ):
                        raise ValueError("invalid_configuration_assignment")
                    seen.add(assignment.toolId)
            visibility = CommunityVisibility(graph | {"revisionId": model.id})
            for community in model.communities:
                if community.enabled:
                    visibility.view_for(community.entryAgentId)
            return cls(
                model.id, model.revisionNumber, model.graphHash, model.schemaVersion, etag, content
            )
        except (KeyError, TypeError, ValueError, PermissionError, RecursionError):
            raise ValueError("invalid_configuration_snapshot") from None

    def for_runtime(self) -> dict[str, Any]:
        """Detached trusted orchestration graph; never give this full graph to an agent."""
        document: dict[str, Any] = json.loads(self._content)
        return {key: document[key] for key in GRAPH_FIELDS} | {"revisionId": self.revision_id}

    def view_for(self, agent_id: str) -> dict[str, Any]:
        return CommunityVisibility(self.for_runtime()).view_for(agent_id)


class RevisionCache:
    def __init__(self) -> None:
        self._current: RevisionSnapshot | None = None

    @property
    def current(self) -> RevisionSnapshot | None:
        return self._current

    def install(self, document: dict[str, Any], *, etag: str) -> RevisionSnapshot:
        candidate = RevisionSnapshot.validate(document, etag=etag)
        previous = self._current
        if previous is not None:
            if (
                candidate.revision_number < previous.revision_number
                or (
                    candidate.revision_number == previous.revision_number
                    and (
                        candidate.revision_id != previous.revision_id
                        or candidate._content != previous._content
                    )
                )
                or (
                    candidate.revision_id == previous.revision_id
                    and candidate._content != previous._content
                )
            ):
                raise ValueError("configuration_revision_conflict")
        self._current = candidate
        return candidate

    def pin(self) -> RevisionSnapshot:
        if self._current is None:
            raise PermissionError("configuration_unavailable")
        return self._current
