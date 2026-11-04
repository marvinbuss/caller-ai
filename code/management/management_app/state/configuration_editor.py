"""Reflex state for the configuration editor: browser events, derived rows and saving."""

import copy
import json
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import reflex as rx
from fastapi import FastAPI
from reflex.event import EventHandler, EventSpec
from reflex_base.registry import RegistrationContext

from management_app.state.editor_draft import Document, EditorDraft, EditorServices


class ConfigurationEditorState(rx.State):
    authorized: bool = False
    busy: bool = False
    status: str = ""
    error: str = ""
    active_tab: str = "Communities"
    relationship_source: str = ""
    selected_community_id: str = ""
    selection_error: str = ""
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
        scoped = [
            (index, agent)
            for index, agent in enumerate(self._draft.graph["agents"])
            if not self.selected_community_id
            or (agent["role"] == "ordinary" and agent["communityId"] == self.selected_community_id)
        ]
        positions = {
            position["nodeId"]: position
            for rows in self._draft.graph["layout"]["agentsByCommunity"].values()
            for position in rows
        }
        ordinals: dict[str, int] = {}
        counts: dict[str, int] = {}
        for configured in self._draft.graph["agents"]:
            if configured["role"] != "ordinary":
                continue
            community_id = configured["communityId"]
            ordinals[configured["agentId"]] = counts.get(community_id, 0)
            counts[community_id] = counts.get(community_id, 0) + 1
        return [
            {
                **copy.deepcopy(agent),
                "index": index,
                "groupLabel": self._draft.labels.get(agent["agentId"], agent["name"]),
                "protected": agent["role"] != "ordinary",
                "positionX": positions.get(agent["agentId"], {}).get(
                    "x", 40 + ordinals.get(agent["agentId"], 0) % 2 * 260
                ),
                "positionY": positions.get(agent["agentId"], {}).get(
                    "y", 40 + ordinals.get(agent["agentId"], 0) // 2 * 220
                ),
                "positionXText": format(
                    positions.get(agent["agentId"], {}).get(
                        "x", 40 + ordinals.get(agent["agentId"], 0) % 2 * 260
                    ),
                    ".2f",
                ),
                "positionYText": format(
                    positions.get(agent["agentId"], {}).get(
                        "y", 40 + ordinals.get(agent["agentId"], 0) // 2 * 220
                    ),
                    ".2f",
                ),
                "communityOptions": [
                    {"id": community["communityId"], "name": community["name"]}
                    for community in self._draft.graph["communities"]
                    if not self.selected_community_id
                    or community["communityId"] == self.selected_community_id
                ],
            }
            for index, agent in scoped
        ]

    @rx.var(cache=False)
    def community_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        positions = {
            position["nodeId"]: position for position in self._draft.graph["layout"]["communities"]
        }
        return [
            {
                **copy.deepcopy(community),
                "index": index,
                "positionX": positions.get(community["communityId"], {}).get("x", 40),
                "positionY": positions.get(community["communityId"], {}).get("y", 40 + index * 220),
                "positionXText": format(
                    positions.get(community["communityId"], {}).get("x", 40), ".2f"
                ),
                "positionYText": format(
                    positions.get(community["communityId"], {}).get("y", 40 + index * 220), ".2f"
                ),
                "entryOptions": [
                    {"id": agent["agentId"], "name": agent["name"]}
                    for agent in self._draft.entry_choices(community["communityId"])
                ],
            }
            for index, community in enumerate(self._draft.graph["communities"])
        ]

    @rx.var(cache=False)
    def dirty(self) -> bool:
        return self.status in {"Unsaved", "Saving"}

    @rx.var(cache=False)
    def selected_community_name(self) -> str:
        if not self.authorized or self._draft is None:
            return ""
        return next(
            (
                community["name"]
                for community in self._draft.graph["communities"]
                if community["communityId"] == self.selected_community_id
            ),
            "",
        )

    @rx.var(cache=False)
    def selected_community_entry(self) -> str:
        if not self.authorized or self._draft is None:
            return ""
        return next(
            (
                row["entryAgentId"]
                for row in self._draft.graph["communities"]
                if row["communityId"] == self.selected_community_id
            ),
            "",
        )

    @rx.var(cache=False)
    def selected_entry_choices(self) -> list[Document]:
        if not self.authorized or self._draft is None or not self.selected_community_id:
            return []
        return self._draft.entry_choices(self.selected_community_id)

    @rx.var(cache=False)
    def agent_community_options(self) -> list[Document]:
        return [
            row
            for row in self.community_rows
            if not self.selected_community_id or row["communityId"] == self.selected_community_id
        ]

    @rx.var(cache=False)
    def agent_nodes(self) -> list[Document]:
        if not self.selected_community_id:
            return []
        return [
            {
                "id": agent["agentId"],
                "data": {"label": agent["name"] or "Unnamed agent"},
                "position": {"x": agent["positionX"], "y": agent["positionY"]},
                "sourcePosition": "bottom",
                "targetPosition": "top",
                "deletable": False,
                "ariaLabel": "Select agent " + agent["name"],
                "style": {
                    "width": 200,
                    "minHeight": 64,
                    "borderRadius": 4,
                    "fontSize": 18,
                    "lineHeight": "1.2",
                    "fontWeight": 600,
                    "whiteSpace": "normal",
                    "overflowWrap": "anywhere",
                    "borderColor": "#197767",
                    "opacity": 1 if agent["enabled"] else 0.65,
                },
            }
            for agent in self.agent_rows
            if not agent["protected"]
        ]

    @rx.var(cache=False)
    def agent_edges(self) -> list[Document]:
        if not self.authorized or self._draft is None or not self.selected_community_id:
            return []
        members = {agent["agentId"] for agent in self.agent_rows if not agent["protected"]}
        return [
            {
                "id": edge["relationshipId"],
                "source": edge["sourceAgentId"],
                "target": edge["targetAgentId"],
                "deletable": False,
                "markerEnd": {"type": "arrowclosed", "color": "#197767"},
                "style": {"stroke": "#197767", "strokeWidth": 2},
            }
            for edge in self._draft.graph["relationships"]
            if edge["sourceAgentId"] in members and edge["targetAgentId"] in members
        ]

    @rx.var(cache=False)
    def community_nodes(self) -> list[Document]:
        return [
            {
                "id": community["communityId"],
                "data": {"label": community["name"]},
                "position": {"x": community["positionX"], "y": community["positionY"]},
                "sourcePosition": "bottom",
                "targetPosition": "top",
                "deletable": False,
                "ariaLabel": "Open community " + community["name"],
                "style": {
                    "width": 200,
                    "minHeight": 64,
                    "borderRadius": 4,
                    "fontSize": 18,
                    "lineHeight": "1.2",
                    "fontWeight": 600,
                    "whiteSpace": "normal",
                    "overflowWrap": "anywhere",
                    "borderColor": "#197767" if community["enabled"] else "#8a9290",
                    "opacity": 1 if community["enabled"] else 0.65,
                },
            }
            for community in self.community_rows
        ]

    @rx.var(cache=False)
    def community_edges(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        return [
            {
                "id": edge["connectionId"],
                "source": edge["sourceCommunityId"],
                "target": edge["targetCommunityId"],
                "deletable": False,
                "markerEnd": {"type": "arrowclosed", "color": "#197767"},
                "style": {"stroke": "#197767", "strokeWidth": 2},
            }
            for edge in self._draft.graph["communityConnections"]
        ]

    @rx.var(cache=False)
    def community_connection_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        names = {
            community["communityId"]: community["name"]
            for community in self._draft.graph["communities"]
        }
        return [
            {
                "id": edge["connectionId"],
                "source": names.get(edge["sourceCommunityId"], "Unavailable"),
                "target": names.get(edge["targetCommunityId"], "Unavailable"),
            }
            for edge in self._draft.graph["communityConnections"]
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
            if self.selected_community_id and agent["communityId"] != self.selected_community_id:
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
            if agent["role"] == "ordinary"
            and (
                not self.selected_community_id or agent["communityId"] == self.selected_community_id
            )
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
            if not self.selected_community_id or agent["communityId"] == self.selected_community_id
        ]

    @rx.var(cache=False)
    def relationship_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        required = self._draft.required_relationship_ids
        members = {agent["agentId"] for agent in self.agent_rows if not agent["protected"]}
        rows = []
        for index, relationship in enumerate(self._draft.graph["relationships"]):
            if self.selected_community_id and (
                relationship["sourceAgentId"] not in members
                or relationship["targetAgentId"] not in members
            ):
                continue
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
    async def enter_view(self) -> None:
        try:
            self._services()
            if self._draft is None:
                await cast(EventHandler, ConfigurationEditorState.load).fn(self)
            if not self.authorized or self._draft is None:
                return
            selected = self.router.page.params.get("community_id", "")
            if not isinstance(selected, str):
                self.selected_community_id = ""
                self.selection_error = "Community unavailable"
                return
            self.selected_community_id = selected
            self.selection_error = ""
            if selected and not any(
                community["communityId"] == selected
                for community in self._draft.graph["communities"]
            ):
                self.selection_error = "Community unavailable"
        except Exception:
            self.authorized = False
            self._draft = None
            self.selection_error = "Configuration unavailable"

    @rx.event
    def open_community(self, identifier: str) -> EventSpec | None:
        try:
            self._editing()._community(identifier)
            return rx.redirect("/configuration/communities/" + identifier)
        except ValueError:
            self.selection_error = "Community unavailable"
            return None

    @rx.event
    def select_community_entry(self, identifier: str) -> None:
        try:
            self._editing().edit_community(self.selected_community_id, "entryAgentId", identifier)
            self._changed()
        except ValueError:
            self.error = "Invalid community entry agent"

    @rx.event
    def move_agent(self, identifier: str, x: float, y: float) -> None:
        try:
            self._editing().set_agent_position(self.selected_community_id, identifier, x, y)
            self._changed()
        except ValueError:
            self.error = "Invalid agent position"

    @rx.event
    def set_agent_coordinate(self, identifier: str, axis: str, value: str | float) -> None:
        try:
            if axis not in {"x", "y"} or isinstance(value, bool):
                raise ValueError("invalid_graph_axis")
            agent = next(row for row in self.agent_rows if row["agentId"] == identifier)
            position = {"x": agent["positionX"], "y": agent["positionY"]}
            position[axis] = float(value)
            self._editing().set_agent_position(
                agent["communityId"], identifier, position["x"], position["y"]
            )
            self._changed()
        except (ValueError, TypeError, StopIteration):
            self.error = "Invalid agent position"

    @rx.event
    def connect_agents(self, source: str, target: str) -> None:
        try:
            draft = self._editing()
            if any(
                draft._agent(identifier)["communityId"] != self.selected_community_id
                for identifier in (source, target)
            ):
                raise ValueError("foreign_agent")
            draft.add_relationship(source, target)
            self._changed()
        except ValueError:
            self.error = "Agent relationship unavailable"

    @rx.event
    def focus_agent(self, identifier: str) -> EventSpec | None:
        try:
            agent = self._editing()._agent(identifier)
            if agent["role"] != "ordinary" or agent["communityId"] != self.selected_community_id:
                raise ValueError("foreign_agent")
            return rx.call_script(
                "document.getElementById("
                + json.dumps(identifier)
                + ")?.scrollIntoView({block:'center'})"
            )
        except ValueError:
            self.error = "Agent unavailable"
            return None

    @rx.event
    def move_community(self, identifier: str, x: float, y: float) -> None:
        try:
            self._editing().set_community_position(identifier, x, y)
            self._changed()
        except ValueError:
            self.error = "Invalid community position"

    @rx.event
    def set_community_coordinate(self, identifier: str, axis: str, value: str | float) -> None:
        try:
            if axis not in {"x", "y"} or isinstance(value, bool):
                raise ValueError("invalid_graph_axis")
            community = next(row for row in self.community_rows if row["communityId"] == identifier)
            position = {"x": community["positionX"], "y": community["positionY"]}
            position[axis] = float(value)
            self._editing().set_community_position(identifier, position["x"], position["y"])
            self._changed()
        except (ValueError, TypeError, StopIteration):
            self.error = "Invalid community position"

    @rx.event
    def connect_communities(self, source: str, target: str) -> None:
        try:
            self._editing().add_community_connection(source, target)
            self._changed()
        except ValueError:
            self.error = "Community connection unavailable"

    @rx.event
    def add_community_connection(self, data: dict[str, Any]) -> None:
        source, target = data.get("source"), data.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            self.error = "Community connection unavailable"
            return
        cast(EventHandler, ConfigurationEditorState.connect_communities).fn(self, source, target)

    @rx.event
    def delete_community_connection(self, identifier: str) -> None:
        try:
            self._editing().delete_community_connection(identifier)
            self._changed()
        except ValueError:
            self.error = "Community connection unavailable"

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
            submitted_graph = copy.deepcopy(draft.graph)
            submitted_generation = self.generation
            submitted_identity = draft._identity
            submitted_etag = draft.etag
            self.busy = True
            self.status = "Saving"
            services = self._services()
            yield
            response = await services.request(
                self.router.session.session_id,
                self.router.session.client_token,
                "PUT",
                graph=submitted_graph,
                etag=submitted_etag,
            )
            current_draft = self._editing()
            if current_draft._identity != submitted_identity:
                return
            draft = current_draft
            if response.status_code == 200:
                if self.generation == submitted_generation:
                    draft.committed(response.json(), response.headers["ETag"])
                    self.status = (
                        "Saved"
                        if response.headers.get("X-Audit-Status") != "pending"
                        else "Saved; audit pending"
                    )
                else:
                    draft.etag = response.headers["ETag"]
                    draft.errors, draft.conflict = {}, False
                    self.status = "Unsaved"
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
            self.status = "Unsaved"
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
            self._editing().delete_community(identifier, confirmed=True)
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
