"""Immutable, validated snapshots of the published configuration and the cache that pins them.

Input: the revision document management serves; output: `RevisionSnapshot`s whose runtime
projection is hash-checked and bounded (16 KiB prompts, 4 KiB community descriptions, at most two
transfer context fields). A snapshot is a policy projection, never the current permission
authority: management checks revocations on every capability call. A session keeps the snapshot it
started with.
"""

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from caller_runtime.domain.visibility import CommunityVisibility

GRAPH_FIELDS = frozenset(
    {
        "schemaVersion",
        "agents",
        "communities",
        "relationships",
        "communityConnections",
        "tools",
        "mcpConnections",
    }
)
# Schema 2.1.0 additions; absent means the environment defaults.
OPTIONAL_GRAPH_FIELDS = frozenset({"conversationSettings"})
ETAG = re.compile(r'^"[A-Za-z0-9_-]{1,128}"$')
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Name = Annotated[str, Field(min_length=1, max_length=128)]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


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

    @field_validator("prompt")
    @classmethod
    def bounded_prompt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 16 * 1024:
            raise ValueError("agent_prompt_too_large")
        return value


class _Community(_Strict):
    communityId: Identifier
    name: Name
    routingDescription: str
    enabled: bool
    entryAgentId: Identifier
    routable: bool = True

    @field_validator("routingDescription")
    @classmethod
    def bounded_description(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 4 * 1024:
            raise ValueError("community_description_too_large")
        return value


class _Relationship(_Strict):
    relationshipId: Identifier
    sourceAgentId: Identifier
    targetAgentId: Identifier
    allowedContextFields: list[Literal["request_summary", "transfer_reason"]] = Field(max_length=2)


class _CommunityConnection(_Strict):
    connectionId: Identifier
    sourceCommunityId: Identifier
    targetCommunityId: Identifier
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
    reviewedCapabilityCatalog: list[_ReviewedCapability]


class _ConversationSettings(_Strict):
    voice: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")] | None = None
    maxOutputTokens: int | None = Field(default=None, ge=1, le=4096)
    idleTimeoutSeconds: int | None = Field(default=None, ge=30, le=120)


class _Revision(_Strict):
    id: Identifier
    organizationId: Literal["default"]
    schemaVersion: Literal["2.0.0", "2.1.0"]
    revisionNumber: int = Field(ge=1)
    createdAt: datetime
    createdBy: Identifier
    graphHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policyHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    agents: list[_Agent] = Field(max_length=200)
    communities: list[_Community] = Field(max_length=50)
    relationships: list[_Relationship] = Field(max_length=1000)
    communityConnections: list[_CommunityConnection] = Field(max_length=1000)
    tools: list[_Tool] = Field(max_length=256)
    mcpConnections: list[_Connection] = Field(max_length=128)
    conversationSettings: _ConversationSettings | None = None


def encoded(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


@dataclass(frozen=True, slots=True)
class RevisionSnapshot:
    revision_id: str
    revision_number: int
    graph_hash: str
    policy_hash: str
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
            graph = {key: detached[key] for key in GRAPH_FIELDS} | {
                key: detached[key] for key in OPTIONAL_GRAPH_FIELDS if key in detached
            }
            if hashlib.sha256(encoded(graph)).hexdigest() != model.policyHash:
                raise ValueError("invalid_configuration_hash")
            if len(model.relationships) + len(model.communityConnections) > 1000:
                raise ValueError("configuration_connection_limit")
            agents = {agent.agentId: agent for agent in model.agents}
            communities = {community.communityId: community for community in model.communities}
            if len({edge.relationshipId for edge in model.relationships}) != len(
                model.relationships
            ):
                raise ValueError("duplicate_configuration_relationship")
            for edge in model.relationships:
                source_agent = agents.get(edge.sourceAgentId)
                target_agent = agents.get(edge.targetAgentId)
                if (
                    source_agent is None
                    or target_agent is None
                    or source_agent.role != "ordinary"
                    or target_agent.role != "ordinary"
                    or source_agent.communityId != target_agent.communityId
                ):
                    raise ValueError("invalid_configuration_relationship")
            community_pairs: set[tuple[str, str]] = set()
            community_edge_ids: set[str] = set()
            for community_edge in model.communityConnections:
                pair = (community_edge.sourceCommunityId, community_edge.targetCommunityId)
                if (
                    pair[0] not in communities
                    or pair[1] not in communities
                    or pair[0] == pair[1]
                    or pair in community_pairs
                    or community_edge.connectionId in community_edge_ids
                    or len(community_edge.allowedContextFields)
                    != len(set(community_edge.allowedContextFields))
                ):
                    raise ValueError("invalid_configuration_community_connection")
                community_pairs.add(pair)
                community_edge_ids.add(community_edge.connectionId)
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
                if (
                    sum(len(item.permittedActionNames) for item in agent.capabilityAssignments)
                    > 128
                ):
                    raise ValueError("configuration_effective_tool_limit")
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
                revision_id=model.id,
                revision_number=model.revisionNumber,
                graph_hash=model.graphHash,
                policy_hash=model.policyHash,
                schema_version=model.schemaVersion,
                etag=etag,
                _content=content,
            )
        except (KeyError, TypeError, ValueError, PermissionError, RecursionError):
            raise ValueError("invalid_configuration_snapshot") from None

    def for_runtime(self) -> dict[str, Any]:
        """Detached trusted orchestration graph; never give this full graph to an agent."""
        document: dict[str, Any] = json.loads(self._content)
        return (
            {key: document[key] for key in GRAPH_FIELDS}
            | {key: document[key] for key in OPTIONAL_GRAPH_FIELDS if key in document}
            | {"revisionId": self.revision_id}
        )

    def conversation_settings(self) -> dict[str, Any]:
        """Published per-conversation values; an absent key means the environment default."""
        settings: dict[str, Any] = json.loads(self._content).get("conversationSettings") or {}
        return {key: value for key, value in settings.items() if value is not None}

    def routable(self, community_id: str) -> bool:
        for community in json.loads(self._content)["communities"]:
            if community["communityId"] == community_id:
                return community.get("routable", True) is True
        return False

    def view_for(self, agent_id: str) -> dict[str, Any]:
        return CommunityVisibility(self.for_runtime()).view_for(agent_id)


class RevisionCache:
    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._current: RevisionSnapshot | None = None
        self._monotonic = monotonic
        self._validated_at: float | None = None

    @property
    def current(self) -> RevisionSnapshot | None:
        return self._current

    def _observation_time(self, validated_at: float | None) -> float:
        now = self._monotonic()
        observed = now if validated_at is None else validated_at
        if (
            not math.isfinite(now)
            or not math.isfinite(observed)
            or observed > now
            or (self._validated_at is not None and observed < self._validated_at)
        ):
            raise ValueError("invalid_configuration_clock")
        return observed

    def install(
        self, document: dict[str, Any], *, etag: str, validated_at: float | None = None
    ) -> RevisionSnapshot:
        observed = self._observation_time(validated_at)
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
        self._validated_at = observed
        return candidate

    def revalidate(self, snapshot: RevisionSnapshot, *, validated_at: float | None = None) -> None:
        if snapshot is not self._current:
            raise ValueError("configuration_revision_conflict")
        self._validated_at = self._observation_time(validated_at)

    def pin(self) -> RevisionSnapshot:
        now = self._monotonic()
        if (
            self._current is None
            or self._validated_at is None
            or not math.isfinite(now)
            or not 0 <= now - self._validated_at < 30
        ):
            raise PermissionError("configuration_unavailable")
        if not any(
            community["enabled"] is True for community in self._current.for_runtime()["communities"]
        ):
            raise PermissionError("configuration_unavailable")
        return self._current
