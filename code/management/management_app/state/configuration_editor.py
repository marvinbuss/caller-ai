"""Reflex state for the Community and Agent page: browser events, derived rows and publishing.

`ConfigurationEditorState` holds one detached `EditorDraft` per browser session and turns it into
the graph nodes, outline, settings panels and rows the page renders. Every event first
re-authorizes through the socket guard, checks the graph rules in `services.graph_view` (a
refusal names the rule and the item, records `configuration.rule_refused` and changes nothing),
applies one change to the draft and bumps `generation` so the page knows it has unpublished
edits. `save` publishes the draft with its entity tag through the in-process API and handles
three outcomes: accepted, a conflict or validation problem (edits are kept and the items named)
and a save that raced with a newer edit (only the tag moves on). `check_revision` runs every
15 seconds while the page is open and raises a notice when another manager published; the
draft is never replaced without Reload.
"""

import copy
import json
import math
import re
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import reflex as rx
from fastapi import FastAPI
from reflex.event import EventHandler
from reflex_base.registry import RegistrationContext

from management_app.services import graph_view
from management_app.services.default_prompts import (
    ADDITION_LIMIT,
    GOODBYE_PROMPT,
    ROUTING_PROMPT,
    WELCOME_PROMPT,
)
from management_app.state.editor_draft import Document, EditorDraft, EditorServices
from management_app.theme import COLORS, NODE_STYLES

COMMUNITY_WIDTH, AGENT_WIDTH, AGENT_HEIGHT, GAP = 340, 150, 64, 60
PATH_ITEM = re.compile(r"^(agents|communities|relationships|tools)\[(\d+)\]")
DEFAULT_PROMPTS = {"welcome": WELCOME_PROMPT, "routing": ROUTING_PROMPT, "goodbye": GOODBYE_PROMPT}


def node_id(kind: str, identifier: str) -> str:
    return ("c-" if kind == "community" else "a-") + identifier


def community_height(agents: int) -> int:
    return 84 + max(1, math.ceil(agents / 2)) * (AGENT_HEIGHT + 12)


class ConfigurationEditorState(rx.State):
    authorized: bool = False
    busy: bool = False
    status: str = ""
    error: str = ""
    relationship_source: str = ""
    selected_community_id: str = ""
    selected_agent_id: str = ""
    selection_error: str = ""
    errors: dict[str, str] = {}
    generation: int = 0
    actor_ref: str = ""
    loaded_revision: int = 0
    notice_revision: int = 0
    loading: bool = False
    _draft: EditorDraft | None = None
    _stamps: dict[str, Document] = {}
    _settings: Document = {}
    _loaded_etag: str = ""

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
        customs = [
            {"id": community["communityId"], "name": community["name"]}
            for community in self._draft.graph["communities"]
        ]
        return [
            {
                **copy.deepcopy(agent),
                "index": index,
                "groupLabel": self._draft.labels.get(agent["agentId"], agent["name"]),
                "protected": agent["role"] != "ordinary",
                "communityOptions": customs,
                "lastChanged": self._stamp_text(agent["agentId"]),
                **{field: agent.get(field, "") for field in ("voiceAddition", "textAddition")},
                **{
                    field + "Bytes": f"{len(agent.get(field, '').encode('utf-8'))} of "
                    f"{ADDITION_LIMIT} bytes"
                    for field in ("voiceAddition", "textAddition")
                },
            }
            for index, agent in enumerate(self._draft.graph["agents"])
        ]

    def _stamp_text(self, agent_id: str) -> str:
        stamp = self._stamps.get(agent_id)
        if not stamp:
            return "Not yet published"
        when = str(stamp.get("changedAt", ""))[:16].replace("T", " ")
        who = stamp.get("changedByName") or stamp.get("changedBy", "")
        return f"Revision {stamp.get('revisionNumber')} on {when} UTC by {who}"

    @rx.var(cache=False)
    def community_rows(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        view = graph_view.project(self._draft.graph)
        flags = {row["communityId"]: row for row in view["communities"]}
        return [
            {
                **copy.deepcopy(community),
                "routable": community.get("routable", True),
                "index": index,
                "unreachable": bool(flags[community["communityId"]].get("unreachable")),
                "invalid": bool(flags[community["communityId"]].get("invalid")),
                "entryOptions": [
                    {"id": agent["agentId"], "name": agent["name"]}
                    for agent in self._draft.entry_choices(community["communityId"])
                ],
                "removedConnections": ", ".join(
                    graph_view.connections_touching(self._draft.graph, community["communityId"])
                ),
            }
            for index, community in enumerate(self._draft.graph["communities"])
        ]

    @rx.var(cache=False)
    def dirty(self) -> bool:
        return self.status in {"Unsaved", "Saving"}

    def _layout(self, view: Document) -> dict[str, tuple[int, int, int]]:
        """Deterministic non-overlapping community boxes: defaults first, then 3 per row."""
        boxes: dict[str, tuple[int, int, int]] = {}
        rows: list[list[Document]] = []
        defaults = [row for row in view["communities"] if row["fixed"]]
        customs = [row for row in view["communities"] if not row["fixed"]]
        rows.append(defaults)
        rows.extend(customs[index : index + 3] for index in range(0, len(customs), 3))
        y = 0
        for row in rows:
            tallest = 0
            for column, community in enumerate(row):
                height = community_height(len(community["agentIds"]))
                boxes[community["communityId"]] = (column * (COMMUNITY_WIDTH + GAP), y, height)
                tallest = max(tallest, height)
            y += tallest + GAP
        return boxes

    def _badges(self, *names: str) -> list[Document]:
        badges = []
        for name in names:
            style = NODE_STYLES[name]
            glyph = {"lock": "\U0001f512", "pencil": "\u270e", "flag": "\u2691"}.get(
                style["icon"], "\u26a0"
            )
            badges.append(
                {
                    "label": style["label"],
                    "glyph": glyph,
                    "color": style.get("color", COLORS["text"]),
                }
            )
        return badges

    @rx.var(cache=False)
    def graph_nodes(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        view = graph_view.project(self._draft.graph)
        agents = {agent["agentId"]: agent for agent in self._draft.graph["agents"]}
        boxes = self._layout(view)
        nodes: list[Document] = []
        for community in view["communities"]:
            kind = "default" if community["fixed"] else "custom"
            style = NODE_STYLES[kind]
            state = [
                name
                for name, flag in (
                    ("warning", community.get("unreachable")),
                    ("danger", community.get("invalid")),
                )
                if flag
            ]
            x, y, height = boxes[community["communityId"]]
            community_node = node_id("community", community["communityId"])
            nodes.append(
                {
                    "id": community_node,
                    "position": {"x": x, "y": y},
                    "style": {"width": COMMUNITY_WIDTH, "height": height},
                    "selected": self.selected_community_id == community["communityId"]
                    and not self.selected_agent_id,
                    "deletable": False,
                    "ariaLabel": community["name"] + " community node",
                    "data": {
                        "kind": "community",
                        "variant": kind,
                        "label": community["name"],
                        "glyph": "\U0001f512" if community["fixed"] else "\u270e",
                        "fill": style["fill"],
                        "border": style["border"],
                        "borderStyle": style["border_style"],
                        "enabled": community["enabled"],
                        "badges": self._badges(kind, *state),
                        "note": community.get("note", ""),
                        "count": len(community["agentIds"]),
                    },
                }
            )
            for index, agent_id in enumerate(community["agentIds"]):
                agent = agents[agent_id]
                agent_kind = "default" if agent["role"] != "ordinary" else "custom"
                agent_style = NODE_STYLES[agent_kind]
                labels = [agent_kind]
                if agent_id == community["entryAgentId"]:
                    labels.append("entry")
                nodes.append(
                    {
                        "id": node_id("agent", agent_id),
                        "parentId": community_node,
                        "extent": "parent",
                        "position": {
                            "x": 12 + index % 2 * (AGENT_WIDTH + 14),
                            "y": 72 + index // 2 * (AGENT_HEIGHT + 12),
                        },
                        "style": {"width": AGENT_WIDTH, "height": AGENT_HEIGHT},
                        "selected": self.selected_agent_id == agent_id,
                        "deletable": False,
                        "ariaLabel": (agent["name"] or "Unnamed agent") + " agent node",
                        "data": {
                            "kind": "agent",
                            "variant": agent_kind,
                            "label": agent["name"] or "Unnamed agent",
                            "glyph": "\U0001f512" if agent_kind == "default" else "\u270e",
                            "fill": agent_style["fill"],
                            "border": agent_style["border"],
                            "borderStyle": agent_style["border_style"],
                            "enabled": agent["enabled"],
                            "badges": self._badges(*labels),
                            "note": "" if agent["enabled"] else "Disabled",
                        },
                    }
                )
        return nodes

    @rx.var(cache=False)
    def graph_edges(self) -> list[Document]:
        if not self.authorized or self._draft is None:
            return []
        view = graph_view.project(self._draft.graph)
        edges: list[Document] = []
        for link in view["links"]:
            fixed = link["kind"] == "fixed"
            edges.append(
                {
                    "id": "l-" + link["id"],
                    "source": node_id("community", link["source"]),
                    "target": node_id("community", link["target"]),
                    "deletable": False,
                    "label": "Fixed" if fixed else "",
                    "markerEnd": {"type": "arrowclosed", "color": COLORS["brand"]},
                    "style": {
                        "stroke": COLORS["default-border"] if fixed else COLORS["brand"],
                        "strokeWidth": 2,
                        "strokeDasharray": "6 4" if fixed else "0",
                    },
                }
            )
        for handover in view["handovers"]:
            edges.append(
                {
                    "id": "h-" + handover["id"],
                    "source": node_id("agent", handover["source"]),
                    "target": node_id("agent", handover["target"]),
                    "deletable": False,
                    "markerEnd": {"type": "arrowclosed", "color": COLORS["brand-strong"]},
                    "style": {"stroke": COLORS["brand-strong"], "strokeWidth": 2},
                }
            )
        return edges

    @rx.var(cache=False)
    def outline(self) -> list[Document]:
        """The keyboard and screen-reader list of every node, in graph order."""
        if not self.authorized or self._draft is None:
            return []
        view = graph_view.project(self._draft.graph)
        agents = {agent["agentId"]: agent for agent in self._draft.graph["agents"]}
        rows = []
        for community in view["communities"]:
            flags = ["Default" if community["fixed"] else "Custom"]
            if community.get("unreachable"):
                flags.append("Unreachable")
            if community.get("invalid"):
                flags.append("Invalid")
            rows.append(
                {
                    "id": node_id("community", community["communityId"]),
                    "name": community["name"],
                    "labels": ", ".join(flags),
                    "selected": self.selected_community_id == community["communityId"]
                    and not self.selected_agent_id,
                    "agents": [
                        {
                            "id": node_id("agent", agent_id),
                            "name": agents[agent_id]["name"] or "Unnamed agent",
                            "labels": ", ".join(
                                ["Default" if agents[agent_id]["role"] != "ordinary" else "Custom"]
                                + (["Entry"] if agent_id == community["entryAgentId"] else [])
                                + ([] if agents[agent_id]["enabled"] else ["Disabled"])
                            ),
                            "selected": self.selected_agent_id == agent_id,
                        }
                        for agent_id in community["agentIds"]
                    ],
                }
            )
        return rows

    @rx.var(cache=False)
    def graph_errors(self) -> list[str]:
        if not self.authorized or self._draft is None:
            return []
        errors: list[str] = graph_view.project(self._draft.graph)["errors"]
        return errors

    @rx.var(cache=False)
    def selection_kind(self) -> str:
        if self.selected_agent_id and self.selected_agent_row:
            return "agent"
        if self.selected_community_id in graph_view.DEFAULT_IDS:
            return "default"
        if self.selected_community_id and self.selected_community_row:
            return "community"
        return ""

    @rx.var(cache=False)
    def selected_agent_row(self) -> Document:
        return next(
            (row for row in self.agent_rows if row["agentId"] == self.selected_agent_id), {}
        )

    @rx.var(cache=False)
    def selected_community_row(self) -> Document:
        return next(
            (
                row
                for row in self.community_rows
                if row["communityId"] == self.selected_community_id
            ),
            {},
        )

    @rx.var(cache=False)
    def selected_default(self) -> Document:
        if not self.authorized or self._draft is None:
            return {}
        for row in graph_view.project(self._draft.graph)["communities"]:
            if row["fixed"] and row["communityId"] == self.selected_community_id:
                entry = next(
                    agent["name"]
                    for agent in self._draft.graph["agents"]
                    if agent["agentId"] == row["entryAgentId"]
                )
                return {"name": row["name"], "entry": entry, "note": row["note"]}
        return {}

    @rx.var(cache=False)
    def selected_handovers(self) -> list[Document]:
        """Handovers from or to the selected agent; the panel names them before a delete."""
        return [
            row
            for row in self.relationship_rows
            if self.selected_agent_id in {row["sourceId"], row["targetId"]}
        ]

    @rx.var(cache=False)
    def selected_handover_names(self) -> str:
        return ", ".join(row["source"] + " to " + row["target"] for row in self.selected_handovers)

    @rx.var(cache=False)
    def selected_assignment(self) -> Document:
        return next(
            (row for row in self.assignment_rows if row["id"] == self.selected_agent_id), {}
        )

    @rx.var(cache=False)
    def publish_summary(self) -> list[str]:
        if not self.authorized or self._draft is None:
            return []
        lines = [f"Publishing creates revision {self.loaded_revision + 1}."]
        names = {row["communityId"]: row["name"] for row in self._draft.graph["communities"]}
        blocked = [names[item] for item in graph_view.unreachable(self._draft.graph)]
        if blocked:
            lines.append("Unreachable after publishing: " + ", ".join(blocked) + ".")
        broken = [names[item] for item in graph_view.invalid(self._draft.graph)]
        if broken:
            lines.append("Publishing will be refused; no enabled entry agent: " + ", ".join(broken))
        return lines

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
            if self.selected_community_id in {edge["sourceCommunityId"], edge["targetCommunityId"]}
        ]

    @rx.var(cache=False)
    def connection_targets(self) -> list[Document]:
        """Custom communities the selected community may connect to."""
        if not self.authorized or self._draft is None:
            return []
        existing = {
            edge["targetCommunityId"]
            for edge in self._draft.graph["communityConnections"]
            if edge["sourceCommunityId"] == self.selected_community_id
        }
        return [
            {"id": row["communityId"], "name": row["name"]}
            for row in self._draft.graph["communities"]
            if row["communityId"] != self.selected_community_id
            and row["communityId"] not in existing
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
                "usedBy": ", ".join(
                    self._draft.labels.get(agent["agentId"], agent["name"])
                    for agent in self._draft.graph["agents"]
                    if any(
                        item["toolId"] == tool["toolId"] for item in agent["capabilityAssignments"]
                    )
                ),
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
    def relationship_destinations(self) -> list[dict[str, str]]:
        """Agents the selected agent may hand over to: same community, not yet linked."""
        source = self.selected_agent_id
        if (
            not self.authorized
            or self._draft is None
            or not source
            or not any(agent["agentId"] == source for agent in self._draft.graph["agents"])
        ):
            return []
        return [
            {
                "id": agent["agentId"],
                "name": str(
                    self._draft.labels.get(agent["agentId"]) or agent["name"] or "Unnamed agent"
                ),
            }
            for agent in self._draft.relationship_targets(source)
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
                    "sourceId": relationship["sourceAgentId"],
                    "targetId": relationship["targetAgentId"],
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
    async def enter_view(self) -> AsyncIterator[None]:
        try:
            self._services()
            if self._draft is None:
                async for _update in cast(EventHandler, ConfigurationEditorState.load).fn(self):
                    yield
            if not self.authorized or self._draft is None:
                return
            known = {row["communityId"] for row in self._draft.graph["communities"]}
            if self.selected_community_id and self.selected_community_id not in (
                known | graph_view.DEFAULT_IDS
            ):
                self.selected_community_id = ""
            if self.selected_agent_id and not any(
                agent["agentId"] == self.selected_agent_id for agent in self._draft.graph["agents"]
            ):
                self.selected_agent_id = ""
            self.selection_error = ""
        except Exception:
            self.authorized = False
            self._draft = None
            self.selection_error = "Configuration unavailable"

    def _refuse(self, refusal: graph_view.Refusal | None, item_kind: str) -> bool:
        """Show and record a refused rule; True means the edit must not happen."""
        if refusal is None:
            return False
        rule, message = refusal
        self.error = message
        api = RegistrationContext.ensure_context().app.api_transformer
        telemetry = getattr(getattr(api, "state", None), "telemetry", None)
        if telemetry is not None:
            telemetry.record(
                "configuration.rule_refused", outcome="refused", rule=rule, item_kind=item_kind
            )
        return True

    @rx.event
    def pick(self, identifier: str) -> None:
        """Select a graph or outline node: `c-<community>` or `a-<agent>`."""
        try:
            draft = self._editing()
            kind, _, value = identifier.partition("-")
            if kind == "c":
                if value not in graph_view.DEFAULT_IDS:
                    draft._community(value)
                self.selected_community_id, self.selected_agent_id = value, ""
            elif kind == "a":
                agent = draft._agent(value)
                self.selected_agent_id = value
                self.selected_community_id = graph_view.community_of(agent) or ""
            else:
                raise ValueError("unknown_node")
            self.selection_error = ""
        except ValueError:
            self.selection_error = "Item unavailable"

    @rx.event
    def open_community(self, identifier: str) -> None:
        cast(EventHandler, ConfigurationEditorState.pick).fn(self, "c-" + identifier)

    @rx.event
    def close_community(self) -> None:
        self.selected_community_id = ""
        self.selected_agent_id = ""
        self.selection_error = ""

    @rx.event
    def link(self, source: str, target: str) -> None:
        """A link drawn on the graph: communities connect, agents hand over."""
        try:
            draft = self._editing()
            source_kind, _, source_id = source.partition("-")
            target_kind, _, target_id = target.partition("-")
            if source_kind == target_kind == "c":
                if self._refuse(
                    graph_view.check_link(draft.graph, source_id, target_id), "connection"
                ):
                    return
                if source_id == "default-routing":
                    draft._community(target_id)["routable"] = True
                else:
                    draft.add_community_connection(source_id, target_id)
            elif source_kind == target_kind == "a":
                if self._refuse(
                    graph_view.check_handover(draft.graph, source_id, target_id), "handover"
                ):
                    return
                draft.add_relationship(source_id, target_id)
            else:
                self._refuse(
                    ("handover_scope", "Links join two communities or two agents."), "handover"
                )
                return
            self._changed()
        except ValueError:
            self.error = "This link already exists or is not allowed"

    @rx.event
    def set_routable(self, identifier: str, value: bool) -> None:
        try:
            if self._refuse(
                graph_view.check_community_change(self._editing().graph, identifier), "community"
            ):
                return
            self._editing()._community(identifier)["routable"] = bool(value)
            self._changed()
        except ValueError:
            self.error = "Community change unavailable"

    @rx.event
    def connect_communities(self, source: str, target: str) -> None:
        cast(EventHandler, ConfigurationEditorState.link).fn(self, "c-" + source, "c-" + target)

    @rx.event
    def add_community_connection(self, data: dict[str, Any]) -> None:
        target = data.get("target")
        if not isinstance(target, str) or not self.selected_community_id:
            self.error = "Community connection unavailable"
            return
        cast(EventHandler, ConfigurationEditorState.connect_communities).fn(
            self, self.selected_community_id, target
        )

    @rx.event
    def delete_community_connection(self, identifier: str) -> None:
        try:
            self._editing().delete_community_connection(identifier)
            self._changed()
        except ValueError:
            self.error = "Community connection unavailable"

    @rx.event
    async def check_revision(self) -> None:
        """Background check for another manager's publication; the draft stays as it is."""
        try:
            services = self._services()
            response = await services.request(
                self.router.session.session_id, self.router.session.client_token, "GET"
            )
            if response.status_code != 200 or self._draft is None:
                return
            number = response.json().get("revisionNumber", 0)
            if response.headers.get("ETag") != self._loaded_etag and number > self.loaded_revision:
                self.notice_revision = number
        except Exception:
            return

    @rx.event
    async def load(self) -> AsyncIterator[None]:
        self.loading, self.error = True, ""
        yield
        try:
            services = self._services()
            sid, token = self.router.session.session_id, self.router.session.client_token
            response = await services.request(sid, token, "GET")
            if response.status_code != 200:
                raise ValueError("configuration_unavailable")
            body = response.json()
            self._draft = EditorDraft.from_revision(
                body, response.headers["ETag"], reviewed=services.reviewed
            )
            self._remember(body, response.headers["ETag"])
            self.actor_ref = services.guard.authorize(sid, token)
            self.authorized = True
            self.relationship_source = ""
            self.error, self.status, self.errors = "", "", {}
            self.generation += 1
        except Exception:
            self.authorized = False
            self._draft = None
            self.error = "Configuration unavailable"
        finally:
            self.loading = False

    def _remember(self, revision: Document, etag: str) -> None:
        self._stamps = copy.deepcopy((revision.get("publication") or {}).get("agentStamps", {}))
        self._settings = copy.deepcopy(revision.get("conversationSettings") or {})
        self.loaded_revision = int(revision.get("revisionNumber", 0))
        self._loaded_etag = etag
        self.notice_revision = 0

    def _named_issues(self, errors: dict[str, str]) -> str:
        """The items a refused publication names, from the issue paths only."""
        if self._draft is None:
            return ""
        names = []
        for path in errors:
            match = PATH_ITEM.match(path)
            if match is None:
                continue
            rows = self._draft.graph[match.group(1)]
            index = int(match.group(2))
            if index < len(rows):
                row = rows[index]
                label = row.get("name") or row.get("relationshipId") or ""
                if label and label not in names:
                    names.append(label)
        return ", ".join(names)

    @rx.event
    async def save(self) -> AsyncIterator[None]:
        if self.busy:
            return
        try:
            draft = self._editing()
            submitted_graph = copy.deepcopy(draft.graph)
            if self._settings:
                # Settings have their own draft; the If-Match keeps this copy current.
                submitted_graph["conversationSettings"] = copy.deepcopy(self._settings)
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
                # The draft was reloaded while the save was in flight; its result is stale.
                return
            draft = current_draft
            if response.status_code == 200:
                body = response.json()
                if self.generation == submitted_generation:
                    draft.committed(body, response.headers["ETag"])
                    warned = {item["communityId"] for item in body.get("warnings", [])}
                    blocked = [
                        row["name"]
                        for row in body.get("communities", [])
                        if row["communityId"] in warned
                    ]
                    self.status = f"Published revision {body.get('revisionNumber', '')}" + (
                        "; unreachable: " + ", ".join(blocked) if blocked else ""
                    )
                    if response.headers.get("X-Audit-Status") == "pending":
                        self.status += "; audit pending"
                else:
                    # Edits arrived during the save: keep them, take only the new tag so the
                    # next save does not conflict with our own earlier one.
                    draft.etag = response.headers["ETag"]
                    draft.errors, draft.conflict = {}, False
                    self.status = "Unsaved"
                self._remember(body, response.headers["ETag"])
                self.errors, self.error = {}, ""
            else:
                draft.rejected(response.status_code, response.json())
                self.errors = dict(draft.errors)
                named = self._named_issues(self.errors)
                self.error = (
                    "Publish conflict: another manager published. Unpublished edits retained."
                    if draft.conflict
                    else "Publishing refused" + (": check " + named if named else "") + "."
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
            draft = self._editing()
            if name == "communityId" and self._refuse(
                graph_view.check_agent_change(draft.graph, identifier, "move")
                or graph_view.check_community_change(draft.graph, value),
                "agent",
            ):
                return
            draft.edit_agent(identifier, name, value)
            self._changed()
        except ValueError:
            self.error = "Agent change unavailable"

    @rx.event
    def restore_default_prompt(self, identifier: str) -> None:
        """Set a pre-defined agent's draft prompt to the current shipped default."""
        try:
            draft = self._editing()
            role = next(
                agent["role"] for agent in draft.graph["agents"] if agent["agentId"] == identifier
            )
            default = DEFAULT_PROMPTS.get(role)
            if default is None:
                raise ValueError("restore_default_unavailable")
            draft.edit_agent(identifier, "prompt", default)
            self._changed()
        except (StopIteration, ValueError):
            self.error = "Restore default unavailable"

    @rx.event
    def set_agent_enabled(self, identifier: str, value: bool) -> None:
        draft = self._editing()
        change = "enable" if value else "disable"
        if self._refuse(graph_view.check_agent_change(draft.graph, identifier, change), "agent"):
            return
        draft.edit_agent(identifier, "enabled", value)
        self._changed()

    @rx.event
    def delete_agent(self, identifier: str) -> None:
        try:
            draft = self._editing()
            if self._refuse(
                graph_view.check_agent_change(draft.graph, identifier, "delete"), "agent"
            ):
                return
            draft.delete_agent(identifier)
            if self.selected_agent_id == identifier:
                self.selected_agent_id = ""
            self._changed()
        except ValueError:
            self.error = "Agent deletion unavailable"

    @rx.event
    def edit_community(self, identifier: str, name: str, value: str) -> None:
        try:
            draft = self._editing()
            if self._refuse(
                graph_view.check_community_change(draft.graph, identifier), "community"
            ):
                return
            draft.edit_community(identifier, name, value)
            self._changed()
        except ValueError:
            self.error = "Community change unavailable"

    @rx.event
    def set_community_enabled(self, identifier: str, value: bool) -> None:
        draft = self._editing()
        if self._refuse(graph_view.check_community_change(draft.graph, identifier), "community"):
            return
        draft.edit_community(identifier, "enabled", value)
        self._changed()

    @rx.event
    def add_community(self, data: dict[str, Any]) -> None:
        name = data.get("name", "")
        if name.strip().casefold() in {"welcome", "routing", "goodbye"}:
            self._refuse(
                ("reserved_name", f"{name.strip()} is the name of a default community."),
                "community",
            )
            return
        self.selected_community_id = self._editing().add_community(
            name,
            data.get("description", ""),
            data.get("entry", ""),
            data.get("prompt", ""),
        )
        self.selected_agent_id = ""
        self._changed()

    @rx.event
    def add_agent(self, data: dict[str, Any]) -> None:
        community = data.get("community", "") or self.selected_community_id
        draft = self._editing()
        if self._refuse(graph_view.check_community_change(draft.graph, community), "community"):
            return
        try:
            identifier = draft.add_agent(community, data.get("name", ""), data.get("prompt", ""))
        except ValueError:
            self.error = "Choose a custom community for the new agent"
            return
        self.selected_agent_id = identifier
        self._changed()

    @rx.event
    def delete_community(self, identifier: str) -> None:
        try:
            draft = self._editing()
            if self._refuse(
                graph_view.check_community_change(draft.graph, identifier), "community"
            ):
                return
            draft.delete_community(identifier, confirmed=True)
            if self.selected_community_id == identifier:
                self.selected_community_id, self.selected_agent_id = "", ""
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
        if self._refuse(
            graph_view.check_agent_change(self._editing().graph, agent_id, "tools"), "tool"
        ):
            return
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
    def add_relationship(self, data: dict[str, Any]) -> None:
        target = str(data.get("target", ""))
        try:
            draft = self._editing()
            if self._refuse(
                graph_view.check_handover(draft.graph, self.selected_agent_id, target), "handover"
            ):
                return
            draft.add_relationship(self.selected_agent_id, target)
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
