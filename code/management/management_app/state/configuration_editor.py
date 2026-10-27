"""Detached unsaved configuration drafts; browser/session orchestration is explicit."""

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Self
from uuid import uuid4

import httpx
import reflex as rx
from fastapi import FastAPI
from reflex_base.registry import RegistrationContext

from management_app.models.capabilities import ApprovedCapabilities
from management_app.security import ReflexEventGuard
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
                    method, "/v1/configuration", headers=headers, json=graph
                )


@dataclass(slots=True)
class EditorDraft:
    graph: Document = field(repr=False)
    etag: str
    errors: dict[str, str] = field(default_factory=dict)
    conflict: bool = False
    labels: dict[str, str] = field(default_factory=dict, repr=False)
    reviewed: ApprovedCapabilities = field(default_factory=ApprovedCapabilities, repr=False)

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
        old_entry = community["entryAgentId"]
        community[name] = value
        if name == "entryAgentId" and old_entry != value:
            routing = next(
                agent["agentId"] for agent in self.graph["agents"] if agent["role"] == "routing"
            )
            self.graph["relationships"] = [
                relationship
                for relationship in self.graph["relationships"]
                if not (
                    relationship["sourceAgentId"] == routing
                    and relationship["targetAgentId"] == old_entry
                )
            ]
            self._path(routing, str(value))

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
        for role in ("routing", "goodbye"):
            target = next(
                agent["agentId"] for agent in self.graph["agents"] if agent["role"] == role
            )
            self._path(identifier, target)
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
        routing = next(
            agent["agentId"] for agent in self.graph["agents"] if agent["role"] == "routing"
        )
        self._path(routing, entry_id)
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

    def delete_community(self, identifier: str) -> None:
        community = self._community(identifier)
        members = [agent for agent in self.graph["agents"] if agent["communityId"] == identifier]
        if any(agent["role"] != "ordinary" for agent in members):
            raise ValueError("protected_role")
        removed = {agent["agentId"] for agent in members}
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
        self.conflict = status == 412
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


class ConfigurationEditorState(rx.State):
    authorized: bool = False
    busy: bool = False
    status: str = ""
    error: str = ""
    active_tab: str = "Communities"
    relationship_source: str = ""
    errors: dict[str, str] = {}
    generation: int = 0
    _draft: EditorDraft | None = None

    def _services(self) -> EditorServices:
        application = RegistrationContext.ensure_context().app
        api = application.api_transformer
        if not isinstance(api, FastAPI) or not isinstance(
            getattr(api.state, "editor_services", None), EditorServices
        ):
            raise RuntimeError("editor_unavailable")
        services: EditorServices = api.state.editor_services
        services.guard.authorize(self.router.session.session_id, self.router.session.client_token)
        return services

    def _editing(self) -> EditorDraft:
        self._services()
        if self._draft is None:
            raise ValueError("configuration_not_loaded")
        return self._draft

    def _changed(self) -> None:
        self.generation += 1
        self.status = "Unsaved"
        self.error = ""
        self.errors = {}
        if self._draft is not None and not any(
            agent["agentId"] == self.relationship_source for agent in self._draft.graph["agents"]
        ):
            self.relationship_source = ""

    @rx.var(cache=False)
    def agent_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        return [
            {
                **copy.deepcopy(agent),
                "index": index,
                "groupLabel": self._draft.labels.get(agent["agentId"], agent["name"]),
                "protected": agent["role"] != "ordinary",
                "communityOptions": [
                    {"id": community["communityId"], "name": community["name"]}
                    for community in self._draft.graph["communities"]
                ],
            }
            for index, agent in enumerate(self._draft.graph["agents"])
        ]

    @rx.var(cache=False)
    def community_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        return [
            {
                **copy.deepcopy(community),
                "index": index,
                "entryOptions": [
                    {"id": agent["agentId"], "name": agent["name"]}
                    for agent in self._draft.entry_choices(community["communityId"])
                ],
            }
            for index, community in enumerate(self._draft.graph["communities"])
        ]

    @rx.var(cache=False)
    def available_tools(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        configured = {tool["toolId"] for tool in self._draft.graph["tools"]}
        connections = {item["connectionId"] for item in self._draft.graph["mcpConnections"]}
        return [
            {
                "id": item["toolId"],
                "name": item["name"],
                "ready": item["mcpConnectionId"] is None or item["mcpConnectionId"] in connections,
            }
            for item in self._draft.reviewed.tools
            if item["toolId"] not in configured
        ]

    @rx.var(cache=False)
    def available_connections(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        configured = {item["connectionId"] for item in self._draft.graph["mcpConnections"]}
        return [
            {"id": item["connectionId"], "name": item["name"]}
            for item in self._draft.reviewed.connections
            if item["connectionId"] not in configured
        ]

    @rx.var(cache=False)
    def tool_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        return [
            {
                **copy.deepcopy(tool),
                "index": index,
                "groupLabel": self._draft.reviewed.tool(tool["toolId"])["name"],
                "schemaJson": json.dumps(tool["inputSchema"], indent=2),
                "outputs": ", ".join(tool["outputAllowlist"]),
                "connection": tool["mcpConnectionId"] or "None",
            }
            for index, tool in enumerate(self._draft.graph["tools"])
        ]

    @rx.var(cache=False)
    def connection_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        return [
            {
                **copy.deepcopy(item),
                "index": index,
                "groupLabel": self._draft.reviewed.connection(item["connectionId"])["name"],
                "credentialOptions": list(self._draft.reviewed.credential_refs),
            }
            for index, item in enumerate(self._draft.graph["mcpConnections"])
        ]

    @rx.var(cache=False)
    def assignment_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        rows = []
        connections = {item["connectionId"]: item for item in self._draft.graph["mcpConnections"]}
        for agent in self._draft.graph["agents"]:
            if agent["role"] != "ordinary":
                continue
            assignments = {item["toolId"]: item for item in agent["capabilityAssignments"]}
            tools = []
            for tool in self._draft.graph["tools"]:
                assignment = assignments.get(tool["toolId"])
                selected = set(assignment["permittedActionNames"]) if assignment else set()
                connection = connections.get(tool["mcpConnectionId"])
                available = tool["enabled"] and (
                    tool["mcpConnectionId"] is None or bool(connection and connection["enabled"])
                )
                actions = self._draft.reviewed.actions(tool["toolId"])
                if connection is not None:
                    declared = {
                        item["actionName"] for item in connection["reviewedCapabilityCatalog"]
                    }
                    actions = [action for action in actions if action in declared]
                tools.append(
                    {
                        "id": tool["toolId"],
                        "name": tool["name"],
                        "available": available,
                        "assigned": assignment is not None,
                        "constraints": json.dumps(
                            assignment["allowedInputConstraints"] if assignment else {},
                            sort_keys=True,
                        ),
                        "actions": [
                            {"name": action, "checked": action in selected} for action in actions
                        ],
                    }
                )
            rows.append(
                {
                    "id": agent["agentId"],
                    "name": self._draft.labels.get(agent["agentId"], agent["name"]),
                    "tools": tools,
                }
            )
        return rows

    @rx.var(cache=False)
    def validation_issues(self) -> list[dict[str, str]]:
        return [{"path": path, "code": code} for path, code in self.errors.items()]

    @rx.var(cache=False)
    def relationship_sources(self) -> list[dict[str, str]]:
        if not self.authorized or self._draft is None:
            return []
        return [
            {
                "id": agent["agentId"],
                "name": self._draft.labels.get(agent["agentId"], agent["name"]),
            }
            for agent in self._draft.graph["agents"]
            if agent["role"] != "goodbye"
        ]

    @rx.var(cache=False)
    def relationship_destinations(self) -> list[dict[str, str]]:
        if (
            not self.authorized
            or self._draft is None
            or not self.relationship_source
            or not any(
                agent["agentId"] == self.relationship_source
                for agent in self._draft.graph["agents"]
            )
        ):
            return []
        return [
            {
                "id": agent["agentId"],
                "name": str(
                    self._draft.labels.get(agent["agentId"]) or agent["name"] or "Unnamed agent"
                ),
            }
            for agent in self._draft.relationship_targets(self.relationship_source)
        ]

    @rx.var(cache=False)
    def relationship_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        required = self._draft.required_relationship_ids
        rows = []
        for index, relationship in enumerate(self._draft.graph["relationships"]):
            rows.append(
                {
                    "id": relationship["relationshipId"],
                    "source": self._draft.labels.get(
                        relationship["sourceAgentId"], "Unknown agent"
                    ),
                    "target": self._draft.labels.get(
                        relationship["targetAgentId"], "Unknown agent"
                    ),
                    "required": relationship["relationshipId"] in required,
                    "summary": "request_summary" in relationship["allowedContextFields"],
                    "reason": "transfer_reason" in relationship["allowedContextFields"],
                    "error": self.errors.get(f"relationships[{index}].allowedContextFields", ""),
                }
            )
        return rows

    @rx.event
    async def load(self) -> None:
        try:
            services = self._services()
            response = await services.request(
                self.router.session.session_id, self.router.session.client_token, "GET"
            )
            if response.status_code != 200:
                raise ValueError("configuration_unavailable")
            self._draft = EditorDraft.from_revision(
                response.json(), response.headers["ETag"], reviewed=services.reviewed
            )
            self.authorized = True
            self.relationship_source = ""
            self.error, self.status, self.errors = "", "", {}
            self.generation += 1
        except Exception:
            self.authorized = False
            self._draft = None
            self.error = "Configuration unavailable"

    @rx.event
    async def save(self) -> AsyncIterator[None]:
        if self.busy:
            return
        try:
            draft = self._editing()
            self.busy = True
            services = self._services()
            yield
            response = await services.request(
                self.router.session.session_id,
                self.router.session.client_token,
                "PUT",
                graph=draft.graph,
                etag=draft.etag,
            )
            if response.status_code == 200:
                draft.committed(response.json(), response.headers["ETag"])
                self.status = (
                    "Saved"
                    if response.headers.get("X-Audit-Status") != "pending"
                    else "Saved; audit pending"
                )
                self.errors, self.error = {}, ""
            else:
                draft.rejected(response.status_code, response.json())
                self.errors = dict(draft.errors)
                self.error = (
                    "Save conflict. Unsaved edits retained."
                    if draft.conflict
                    else "Invalid configuration"
                )
                self.status = "Unsaved"
            self.generation += 1
        except Exception:
            self.error = "Save unavailable. Unsaved edits retained."
        finally:
            self.busy = False

    @rx.event
    def edit_agent(self, identifier: str, name: str, value: str) -> None:
        try:
            self._editing().edit_agent(identifier, name, value)
            self._changed()
        except ValueError:
            self.error = "Agent change unavailable"

    @rx.event
    def set_agent_enabled(self, identifier: str, value: bool) -> None:
        self._editing().edit_agent(identifier, "enabled", value)
        self._changed()

    @rx.event
    def delete_agent(self, identifier: str) -> None:
        try:
            self._editing().delete_agent(identifier)
            self._changed()
        except ValueError:
            self.error = "Choose another entry agent before deleting this agent"

    @rx.event
    def edit_community(self, identifier: str, name: str, value: str) -> None:
        try:
            self._editing().edit_community(identifier, name, value)
            self._changed()
        except ValueError:
            self.error = "Community change unavailable"

    @rx.event
    def set_community_enabled(self, identifier: str, value: bool) -> None:
        self._editing().edit_community(identifier, "enabled", value)
        self._changed()

    @rx.event
    def add_community(self, data: dict[str, str]) -> None:
        self._editing().add_community(
            data.get("name", ""),
            data.get("description", ""),
            data.get("entry", ""),
            data.get("prompt", ""),
        )
        self._changed()

    @rx.event
    def add_agent(self, data: dict[str, str]) -> None:
        self._editing().add_agent(
            data.get("community", ""), data.get("name", ""), data.get("prompt", "")
        )
        self._changed()

    @rx.event
    def delete_community(self, identifier: str) -> None:
        try:
            self._editing().delete_community(identifier)
            self._changed()
        except ValueError:
            self.error = "Community deletion unavailable"

    def _capability_change(self, operation: Callable[[], None]) -> None:
        try:
            operation()
            self._changed()
        except ValueError:
            self.error = "Capability change unavailable"

    @rx.event
    def add_tool(self, data: dict[str, Any]) -> None:
        self._capability_change(lambda: self._editing().add_tool(str(data.get("tool", ""))))

    @rx.event
    def add_connection(self, data: dict[str, Any]) -> None:
        self._capability_change(
            lambda: self._editing().add_connection(str(data.get("connection", "")))
        )

    @rx.event
    def edit_tool(self, identifier: str, name: str, value: str) -> None:
        self._capability_change(lambda: self._editing().edit_tool(identifier, name, value))

    @rx.event
    def set_tool_enabled(self, identifier: str, value: bool) -> None:
        self._capability_change(lambda: self._editing().edit_tool(identifier, "enabled", value))

    @rx.event
    def edit_connection(self, identifier: str, name: str, value: str) -> None:
        self._capability_change(lambda: self._editing().edit_connection(identifier, name, value))

    @rx.event
    def set_connection_enabled(self, identifier: str, value: bool) -> None:
        self._capability_change(
            lambda: self._editing().edit_connection(identifier, "enabled", value)
        )

    @rx.event
    def delete_tool(self, identifier: str) -> None:
        self._capability_change(lambda: self._editing().delete_tool(identifier))

    @rx.event
    def delete_connection(self, identifier: str) -> None:
        self._capability_change(lambda: self._editing().delete_connection(identifier))

    @rx.event
    def set_action(self, agent_id: str, tool_id: str, action: str, enabled: bool) -> None:
        self._capability_change(
            lambda: self._editing().set_action(agent_id, tool_id, action, enabled)
        )

    @rx.event
    def edit_constraints(self, data: dict[str, Any]) -> None:
        self._capability_change(
            lambda: self._editing().edit_constraints(
                str(data.get("agent", "")),
                str(data.get("tool", "")),
                str(data.get("constraints", "")),
            )
        )

    @rx.event
    def select_relationship_source(self, identifier: str) -> None:
        try:
            if identifier and self._editing()._agent(identifier)["role"] == "goodbye":
                raise ValueError("relationship_prohibited")
            self._editing()
            self.relationship_source = identifier
        except ValueError:
            self.error = "Relationship source unavailable"

    @rx.event
    def add_relationship(self, data: dict[str, Any]) -> None:
        try:
            self._editing().add_relationship(self.relationship_source, str(data.get("target", "")))
            self._changed()
        except ValueError:
            self.error = "Relationship change unavailable"

    @rx.event
    def delete_relationship(self, identifier: str) -> None:
        try:
            self._editing().delete_relationship(identifier)
            self._changed()
        except ValueError:
            self.error = "Required lifecycle relationships cannot be removed"

    @rx.event
    def set_context_field(self, identifier: str, name: str, enabled: bool) -> None:
        try:
            self._editing().set_context_field(identifier, name, enabled)
            self._changed()
        except ValueError:
            self.error = "Transfer field change unavailable"
