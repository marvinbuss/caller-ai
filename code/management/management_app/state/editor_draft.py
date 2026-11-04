"""Detached unsaved configuration drafts and the in-process request path they save through."""

import asyncio
import copy
import json
import math
from dataclasses import dataclass, field
from typing import Any, Self
from uuid import uuid4

import httpx
from fastapi import FastAPI

from management_app.models.capabilities import ApprovedCapabilities
from management_app.models.configuration import GraphPosition
from management_app.reflex_security import ReflexEventGuard
from management_app.services.configuration_validation import (
    GRAPH_FIELDS,
    relationship_rule,
    required_relationship_pairs,
)

Document = dict[str, Any]


@dataclass(frozen=True, slots=True)
class EditorServices:
    api: FastAPI = field(repr=False)
    guard: ReflexEventGuard = field(repr=False)
    reviewed: ApprovedCapabilities = field(default_factory=ApprovedCapabilities, repr=False)

    async def request(
        self,
        sid: str,
        token: str,
        method: str,
        *,
        graph: Document | None = None,
        etag: str | None = None,
    ) -> httpx.Response:
        headers = self.guard.request_headers(sid, token, mutation=method == "PUT")
        if etag is not None:
            headers["If-Match"] = etag
        async with asyncio.timeout(10):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.api),
                base_url=self.guard.identity.policy.origin,
                trust_env=False,
            ) as client:
                return await client.request(
                    method, "/v2/configuration", headers=headers, json=graph
                )


@dataclass(slots=True)
class EditorDraft:
    graph: Document = field(repr=False)
    etag: str
    errors: dict[str, str] = field(default_factory=dict)
    conflict: bool = False
    labels: dict[str, str] = field(default_factory=dict, repr=False)
    reviewed: ApprovedCapabilities = field(default_factory=ApprovedCapabilities, repr=False)
    _identity: str = field(default_factory=lambda: uuid4().hex, init=False, repr=False)

    @classmethod
    def from_revision(
        cls, revision: Document, etag: str, *, reviewed: ApprovedCapabilities | None = None
    ) -> Self:
        return cls(
            {key: copy.deepcopy(value) for key, value in revision.items() if key in GRAPH_FIELDS},
            etag,
            labels={agent["agentId"]: agent["name"] for agent in revision["agents"]},
            reviewed=reviewed if reviewed is not None else ApprovedCapabilities(),
        )

    def _agent(self, identifier: str) -> Document:
        agents: list[Document] = self.graph["agents"]
        for agent in agents:
            if agent["agentId"] == identifier:
                return agent
        raise ValueError("unknown_agent")

    def _community(self, identifier: str) -> Document:
        communities: list[Document] = self.graph["communities"]
        for community in communities:
            if community["communityId"] == identifier:
                return community
        raise ValueError("unknown_community")

    def edit_agent(self, identifier: str, name: str, value: str | bool) -> None:
        agent = self._agent(identifier)
        if name not in {"name", "prompt", "enabled", "communityId"}:
            raise ValueError("protected_role")
        if agent["role"] != "ordinary" and name in {"enabled", "communityId"}:
            raise ValueError("protected_role")
        if (name == "enabled") != isinstance(value, bool):
            raise ValueError("invalid_field_type")
        if name == "communityId":
            if not isinstance(value, str):
                raise ValueError("invalid_community")
            self._community(value)
            if value != agent["communityId"] and any(
                community["entryAgentId"] == identifier for community in self.graph["communities"]
            ):
                raise ValueError("entry_agent_required")
            if value != agent["communityId"]:
                if any(
                    identifier in {edge["sourceAgentId"], edge["targetAgentId"]}
                    for edge in self.graph["relationships"]
                ):
                    raise ValueError("agent_connections_require_repair")
                positions = self.graph["layout"]["agentsByCommunity"]
                old_community = agent["communityId"]
                if old_community in positions:
                    positions[old_community] = [
                        position
                        for position in positions[old_community]
                        if position["nodeId"] != identifier
                    ]
        agent[name] = value

    def edit_community(self, identifier: str, name: str, value: str | bool) -> None:
        community = self._community(identifier)
        if name not in {"name", "routingDescription", "enabled", "entryAgentId"}:
            raise ValueError("invalid_community_field")
        if (name == "enabled") != isinstance(value, bool):
            raise ValueError("invalid_field_type")
        if name == "entryAgentId" and value not in {
            agent["agentId"] for agent in self.entry_choices(identifier)
        }:
            raise ValueError("invalid_entry_agent")
        community[name] = value

    def entry_choices(self, community_id: str) -> list[Document]:
        return [
            copy.deepcopy(agent)
            for agent in self.graph["agents"]
            if agent["role"] == "ordinary"
            and agent["communityId"] == community_id
            and agent["enabled"]
        ]

    def _path(self, source: str, target: str) -> None:
        self.graph["relationships"].append(
            {
                "relationshipId": "path-" + uuid4().hex,
                "sourceAgentId": source,
                "targetAgentId": target,
                "allowedContextFields": ["request_summary", "transfer_reason"],
            }
        )

    def _relationship_nodes(self) -> tuple[dict[str, Document], dict[str, Document]]:
        return (
            {agent["agentId"]: agent for agent in self.graph["agents"]},
            {community["communityId"]: community for community in self.graph["communities"]},
        )

    @property
    def required_relationship_ids(self) -> set[str]:
        agents, communities = self._relationship_nodes()
        required = required_relationship_pairs(agents, communities)
        return {
            item["relationshipId"]
            for item in self.graph["relationships"]
            if (item["sourceAgentId"], item["targetAgentId"]) in required
        }

    def relationship_targets(self, source_id: str) -> list[Document]:
        self._agent(source_id)
        agents, communities = self._relationship_nodes()
        existing = {
            (item["sourceAgentId"], item["targetAgentId"]) for item in self.graph["relationships"]
        }
        return [
            copy.deepcopy(agent)
            for identifier, agent in agents.items()
            if (source_id, identifier) not in existing
            and relationship_rule(source_id, identifier, agents, communities) is None
        ]

    def add_relationship(self, source_id: str, target_id: str) -> str:
        agents, communities = self._relationship_nodes()
        if relationship_rule(source_id, target_id, agents, communities) is not None:
            raise ValueError("relationship_prohibited")
        if any(
            item["sourceAgentId"] == source_id and item["targetAgentId"] == target_id
            for item in self.graph["relationships"]
        ):
            raise ValueError("duplicate_relationship")
        self._path(source_id, target_id)
        identifier: str = self.graph["relationships"][-1]["relationshipId"]
        return identifier

    def delete_relationship(self, identifier: str) -> None:
        if identifier in self.required_relationship_ids:
            raise ValueError("required_lifecycle_path")
        self.graph["relationships"].remove(
            self._capability("relationships", "relationshipId", identifier)
        )

    def set_context_field(self, identifier: str, name: str, enabled: bool) -> None:
        if name not in {"request_summary", "transfer_reason"}:
            raise ValueError("context_field_prohibited")
        if not isinstance(enabled, bool):
            raise ValueError("invalid_field_type")
        relationship = self._capability("relationships", "relationshipId", identifier)
        fields = set(relationship["allowedContextFields"])
        fields.add(name) if enabled else fields.discard(name)
        relationship["allowedContextFields"] = sorted(fields)

    def add_agent(self, community_id: str, name: str, prompt: str) -> str:
        self._community(community_id)
        identifier = "agent-" + uuid4().hex
        self.graph["agents"].append(
            {
                "agentId": identifier,
                "name": name,
                "prompt": prompt,
                "enabled": True,
                "role": "ordinary",
                "communityId": community_id,
                "capabilityAssignments": [],
            }
        )
        self.labels[identifier] = name or "New agent"
        return identifier

    def add_community(self, name: str, description: str, entry_name: str, prompt: str) -> str:
        identifier = "community-" + uuid4().hex
        self.graph["communities"].append(
            {
                "communityId": identifier,
                "name": name,
                "routingDescription": description,
                "enabled": True,
                "entryAgentId": "pending-entry",
            }
        )
        entry_id = self.add_agent(identifier, entry_name, prompt)
        self.graph["communities"][-1]["entryAgentId"] = entry_id
        return identifier

    def delete_agent(self, identifier: str) -> None:
        agent = self._agent(identifier)
        if agent["role"] != "ordinary":
            raise ValueError("protected_role")
        if any(community["entryAgentId"] == identifier for community in self.graph["communities"]):
            raise ValueError("entry_agent_required")
        self.graph["agents"].remove(agent)
        self.graph["relationships"] = [
            relationship
            for relationship in self.graph["relationships"]
            if identifier not in {relationship["sourceAgentId"], relationship["targetAgentId"]}
        ]
        positions = self.graph["layout"]["agentsByCommunity"]
        if agent["communityId"] in positions:
            positions[agent["communityId"]] = [
                position
                for position in positions[agent["communityId"]]
                if position["nodeId"] != identifier
            ]

    def set_agent_position(self, community_id: str, identifier: str, x: float, y: float) -> None:
        self._community(community_id)
        agent = self._agent(identifier)
        if agent["role"] != "ordinary" or agent["communityId"] != community_id:
            raise ValueError("invalid_agent_position")
        position = GraphPosition(nodeId=identifier, x=x, y=y).model_dump(by_alias=True)
        positions = self.graph["layout"]["agentsByCommunity"]
        positions[community_id] = [
            current
            for current in positions.get(community_id, [])
            if current["nodeId"] != identifier
        ] + [position]

    def add_community_connection(self, source_id: str, target_id: str) -> str:
        self._community(source_id)
        self._community(target_id)
        if source_id == target_id:
            raise ValueError("community_connection_prohibited")
        if any(
            edge["sourceCommunityId"] == source_id and edge["targetCommunityId"] == target_id
            for edge in self.graph["communityConnections"]
        ):
            raise ValueError("duplicate_community_connection")
        if len(self.graph["relationships"]) + len(self.graph["communityConnections"]) >= 1000:
            raise ValueError("connection_limit")
        identifier = "community-path-" + uuid4().hex
        self.graph["communityConnections"].append(
            {
                "connectionId": identifier,
                "sourceCommunityId": source_id,
                "targetCommunityId": target_id,
                "allowedContextFields": ["request_summary", "transfer_reason"],
            }
        )
        return identifier

    def delete_community_connection(self, identifier: str) -> None:
        edge = self._capability("communityConnections", "connectionId", identifier)
        self.graph["communityConnections"].remove(edge)

    def set_community_position(self, identifier: str, x: float, y: float) -> None:
        self._community(identifier)
        if any(
            type(coordinate) not in (int, float)
            or not math.isfinite(coordinate)
            or not -100000 <= coordinate <= 100000
            for coordinate in (x, y)
        ):
            raise ValueError("invalid_graph_position")
        positions = [
            position
            for position in self.graph["layout"]["communities"]
            if position["nodeId"] != identifier
        ]
        positions.append({"nodeId": identifier, "x": x, "y": y})
        self.graph["layout"]["communities"] = positions

    def delete_community(self, identifier: str, *, confirmed: bool = False) -> None:
        community = self._community(identifier)
        members = [agent for agent in self.graph["agents"] if agent["communityId"] == identifier]
        if any(agent["role"] != "ordinary" for agent in members):
            raise ValueError("protected_role")
        removed = {agent["agentId"] for agent in members}
        if not confirmed and (
            any(
                identifier in {edge["sourceCommunityId"], edge["targetCommunityId"]}
                for edge in self.graph["communityConnections"]
            )
            or any(
                removed.intersection({edge["sourceAgentId"], edge["targetAgentId"]})
                for edge in self.graph["relationships"]
            )
        ):
            raise ValueError("community_connections_require_confirmation")
        self.graph["agents"] = [
            agent for agent in self.graph["agents"] if agent["agentId"] not in removed
        ]
        self.graph["relationships"] = [
            relationship
            for relationship in self.graph["relationships"]
            if not removed.intersection(
                {relationship["sourceAgentId"], relationship["targetAgentId"]}
            )
        ]
        self.graph["communities"].remove(community)
        self.graph["communityConnections"] = [
            edge
            for edge in self.graph["communityConnections"]
            if identifier not in {edge["sourceCommunityId"], edge["targetCommunityId"]}
        ]
        self.graph["layout"]["communities"] = [
            position
            for position in self.graph["layout"]["communities"]
            if position["nodeId"] != identifier
        ]
        self.graph["layout"]["agentsByCommunity"].pop(identifier, None)

    def _capability(self, collection: str, key: str, identifier: str) -> Document:
        rows: list[Document] = self.graph[collection]
        for item in rows:
            if item[key] == identifier:
                return item
        raise ValueError("unknown_capability")

    def add_connection(self, identifier: str) -> None:
        item = self.reviewed.connection(identifier)
        if any(row["connectionId"] == identifier for row in self.graph["mcpConnections"]):
            raise ValueError("already_configured")
        item["enabled"] = False
        self.graph["mcpConnections"].append(item)

    def add_tool(self, identifier: str) -> None:
        item = self.reviewed.tool(identifier)
        if any(row["toolId"] == identifier for row in self.graph["tools"]):
            raise ValueError("already_configured")
        if item["mcpConnectionId"] is not None:
            self._capability("mcpConnections", "connectionId", item["mcpConnectionId"])
        item["enabled"] = False
        self.graph["tools"].append(item)

    def edit_tool(self, identifier: str, name: str, value: str | bool) -> None:
        self.reviewed.tool(identifier)
        if name not in {"name", "description", "enabled"}:
            raise ValueError("reviewed_metadata")
        if (name == "enabled") != isinstance(value, bool):
            raise ValueError("invalid_field_type")
        self._capability("tools", "toolId", identifier)[name] = value

    def edit_connection(self, identifier: str, name: str, value: str | bool) -> None:
        self.reviewed.connection(identifier)
        if name not in {"name", "enabled", "credentialRef"}:
            raise ValueError("reviewed_metadata")
        if (name == "enabled") != isinstance(value, bool):
            raise ValueError("invalid_field_type")
        if name == "credentialRef" and value not in self.reviewed.credential_refs:
            raise ValueError("unreviewed_credential_reference")
        self._capability("mcpConnections", "connectionId", identifier)[name] = value

    def set_action(self, agent_id: str, tool_id: str, action: str, enabled: bool) -> None:
        agent = self._agent(agent_id)
        if agent["role"] != "ordinary":
            raise ValueError("protected_role")
        if action not in self.reviewed.actions(tool_id):
            raise ValueError("unreviewed_capability")
        self._capability("tools", "toolId", tool_id)
        assignments: list[Document] = agent["capabilityAssignments"]
        assignment = next((item for item in assignments if item["toolId"] == tool_id), None)
        if enabled and assignment is None:
            assignment = {
                "agentId": agent_id,
                "toolId": tool_id,
                "permittedActionNames": [],
                "allowedInputConstraints": {},
            }
            assignments.append(assignment)
        if assignment is not None:
            actions = set(assignment["permittedActionNames"])
            actions.add(action) if enabled else actions.discard(action)
            assignment["permittedActionNames"] = sorted(actions)
            if not actions:
                assignments.remove(assignment)

    def edit_constraints(self, agent_id: str, tool_id: str, text: str) -> None:
        agent = self._agent(agent_id)
        if agent["role"] != "ordinary":
            raise ValueError("protected_role")
        self.reviewed.tool(tool_id)
        try:
            if len(text.encode()) > 4096:
                raise ValueError("invalid_input_constraints")
            constraints = json.loads(text)
            if not isinstance(constraints, dict):
                raise ValueError("invalid_input_constraints")
            json.dumps(constraints, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise ValueError("invalid_input_constraints") from None
        assignment = next(
            (item for item in agent["capabilityAssignments"] if item["toolId"] == tool_id), None
        )
        if assignment is None:
            raise ValueError("assignment_required")
        assignment["allowedInputConstraints"] = constraints

    def delete_tool(self, identifier: str) -> None:
        tool = self._capability("tools", "toolId", identifier)
        self.graph["tools"].remove(tool)
        for agent in self.graph["agents"]:
            agent["capabilityAssignments"] = [
                item for item in agent["capabilityAssignments"] if item["toolId"] != identifier
            ]

    def delete_connection(self, identifier: str) -> None:
        if any(item["mcpConnectionId"] == identifier for item in self.graph["tools"]):
            raise ValueError("connection_in_use")
        self.graph["mcpConnections"].remove(
            self._capability("mcpConnections", "connectionId", identifier)
        )

    def rejected(self, status: int, problem: Document) -> None:
        self.conflict = status == 409
        self.errors = {
            item["path"]: item["code"]
            for item in problem.get("errors", [])
            if isinstance(item.get("path"), str) and isinstance(item.get("code"), str)
        }

    def committed(self, revision: Document, etag: str) -> None:
        accepted = self.from_revision(revision, etag, reviewed=self.reviewed)
        self.graph, self.etag, self.labels = accepted.graph, accepted.etag, accepted.labels
        self.errors.clear()
        self.conflict = False
