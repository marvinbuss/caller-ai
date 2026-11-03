"""Pure graph validation before any publication; issues contain metadata only."""

import json
from collections.abc import Mapping, Sequence

from jsonschema import Draft202012Validator, SchemaError
from pydantic import ValidationError

from management_app.models.capabilities import ApprovedCapabilities, ConfigurationModel
from management_app.models.configuration import ConfigurationGraph
from management_app.services.secret_detection import Issue, SecretDetector, field_path

SNAPSHOT_LIMIT = 512 * 1024
BATCH_LIMIT = 768 * 1024
GRAPH_FIELDS = frozenset(
    {"schemaVersion", "agents", "communities", "relationships", "tools", "mcpConnections"}
)


def encoded(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def invalid_schema(schema: Mapping[str, object]) -> bool:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return True
    pending: list[object] = [schema]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(value, str) or not value.startswith("#")
                ):
                    return True
                pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    return False


def relationship_rule(
    source_id: str,
    target_id: str,
    agents: Mapping[str, Mapping[str, object]],
    communities: Mapping[str, Mapping[str, object]],
) -> str | None:
    source, target = agents.get(source_id), agents.get(target_id)
    if source is None or target is None:
        return "relationship_reference"
    if source_id == target_id:
        return "unique_path"
    source_role, target_role = source.get("role"), target.get("role")
    if source_role == "goodbye" or target_role == "welcome":
        return "lifecycle_path"
    if source_role == "welcome" and target_role not in {"routing", "goodbye"}:
        return "lifecycle_path"
    if source_role == target_role == "ordinary" and source.get("communityId") != target.get(
        "communityId"
    ):
        return "foreign_direct_path"
    if source_role == "routing" and target_role == "ordinary":
        community_id = target.get("communityId")
        community = communities.get(community_id) if isinstance(community_id, str) else None
        if community is None or community.get("entryAgentId") != target_id:
            return "routing_entry_path"
    return None


def required_relationship_pairs(
    agents: Mapping[str, Mapping[str, object]], communities: Mapping[str, Mapping[str, object]]
) -> set[tuple[str, str]]:
    roles = {
        role: [
            identifier
            for identifier, agent in agents.items()
            if agent.get("role") == role
            and agent.get("enabled") is True
            and agent.get("communityId") is None
        ]
        for role in ("welcome", "routing", "goodbye")
    }
    if any(len(members) != 1 for members in roles.values()):
        return set()
    welcome, routing, goodbye = (roles[role][0] for role in ("welcome", "routing", "goodbye"))
    required = {(welcome, routing), (routing, goodbye)}
    for identifier, agent in agents.items():
        if agent.get("role") == "ordinary" and agent.get("enabled") is True:
            required.update({(identifier, routing), (identifier, goodbye)})
    for community in communities.values():
        entry = community.get("entryAgentId")
        if community.get("enabled") is True and isinstance(entry, str):
            required.add((routing, entry))
    return required


class ConfigurationValidator:
    def __init__(
        self,
        *,
        policy: dict[str, object],
        approved_secret_aliases: frozenset[str],
        approved_destination_refs: frozenset[str],
        reviewed: ApprovedCapabilities | None = None,
    ) -> None:
        self.detector = SecretDetector(policy, approved_secret_aliases)
        self.destinations = approved_destination_refs
        self.reviewed = reviewed if reviewed is not None else ApprovedCapabilities()

    def validate(
        self,
        payload: dict[str, object],
        *,
        authority_bytes: int = 0,
        previous_revision: dict[str, object] | None = None,
    ) -> list[Issue]:
        errors: list[Issue] = []

        def reject(path: str, code: str = "invalid_configuration", rule: str = "graph") -> None:
            errors.append(self.detector.issue(path, code, rule))

        try:
            size = len(encoded(payload))
        except (TypeError, ValueError, RecursionError):
            return [self.detector.issue("configuration", "invalid_configuration", "schema")]
        errors.extend(self.detector.scan(payload))
        if any(error["rule_id"] == "document_depth" for error in errors):
            return errors
        if size > SNAPSHOT_LIMIT:
            reject("configuration", "configuration_too_large", "snapshot_bytes")
        if authority_bytes < 0 or size + authority_bytes + 4096 >= BATCH_LIMIT:
            reject("configuration", "publication_too_large", "batch_bytes")
        try:
            graph = ConfigurationGraph.model_validate(payload)
        except ValidationError as failure:
            for error in failure.errors(include_input=False, include_url=False):
                path = ""
                for component in error["loc"]:
                    path = field_path(path, component)
                reject(path, rule="schema")
            return errors

        groups: list[tuple[Sequence[ConfigurationModel], str, str]] = [
            (graph.agents, "agent_id", "agents"),
            (graph.communities, "community_id", "communities"),
            (graph.relationships, "relationship_id", "relationships"),
            (graph.tools, "tool_id", "tools"),
            (graph.mcp_connections, "connection_id", "mcpConnections"),
        ]
        for items, attribute, path in groups:
            identifiers = [getattr(item, attribute) for item in items]
            if len(identifiers) != len(set(identifiers)):
                reject(path, rule="unique_id")
        names = [agent.name for agent in graph.agents]
        if len(names) != len(set(names)):
            reject("agents", rule="unique_name")

        agents = {agent.agent_id: agent for agent in graph.agents}
        communities = {community.community_id: community for community in graph.communities}
        tools = {tool.tool_id: tool for tool in graph.tools}
        connections = {connection.connection_id: connection for connection in graph.mcp_connections}
        protected = {}
        for role in ("welcome", "routing", "goodbye"):
            members = [agent for agent in graph.agents if agent.role == role]
            if len(members) != 1 or not members[0].enabled or members[0].community_id is not None:
                reject("agents", rule="protected_role")
            else:
                protected[role] = members[0].agent_id

        if previous_revision is not None:
            old = {
                str(key): value for key, value in previous_revision.items() if key in GRAPH_FIELDS
            }
            previous = ConfigurationGraph.model_validate(old)
            for agent in previous.agents:
                if agent.role != "ordinary" and protected.get(agent.role) != agent.agent_id:
                    reject("agents", rule="protected_identity")

        for index, agent in enumerate(graph.agents):
            path = f"agents[{index}]"
            if agent.enabled and not agent.prompt.strip():
                reject(path + ".prompt", rule="enabled_prompt")
            if agent.role == "ordinary" and agent.community_id not in communities:
                reject(path + ".communityId", rule="community_membership")
            if agent.role != "ordinary" and agent.capability_assignments:
                reject(path + ".capabilityAssignments", rule="protected_capability")
            seen_tools: set[str] = set()
            for assignment_index, assignment in enumerate(agent.capability_assignments):
                assignment_path = f"{path}.capabilityAssignments[{assignment_index}]"
                tool = tools.get(assignment.tool_id)
                if (
                    assignment.agent_id != agent.agent_id
                    or tool is None
                    or assignment.tool_id in seen_tools
                ):
                    reject(assignment_path, rule="assignment_reference")
                seen_tools.add(assignment.tool_id)
                if len(assignment.permitted_action_names) != len(
                    set(assignment.permitted_action_names)
                ):
                    reject(assignment_path, rule="assignment_actions")
                if tool is not None and tool.mcp_connection_id in connections:
                    catalog = {
                        item.action_name
                        for item in connections[tool.mcp_connection_id].reviewed_capability_catalog
                    }
                    if not set(assignment.permitted_action_names) <= catalog:
                        reject(assignment_path, rule="unreviewed_action")
                if tool is not None:
                    try:
                        if not set(assignment.permitted_action_names) <= set(
                            self.reviewed.actions(tool.tool_id)
                        ):
                            reject(assignment_path, rule="capability_review")
                    except ValueError:
                        reject(assignment_path, rule="capability_review")

        for index, community in enumerate(graph.communities):
            entry = agents.get(community.entry_agent_id)
            if (
                entry is None
                or entry.role != "ordinary"
                or entry.community_id != community.community_id
                or (community.enabled and not entry.enabled)
            ):
                reject(f"communities[{index}].entryAgentId", rule="community_entry")

        agent_nodes = {
            agent.agent_id: {
                "role": agent.role,
                "communityId": agent.community_id,
                "enabled": agent.enabled,
            }
            for agent in graph.agents
        }
        community_nodes = {
            community.community_id: {
                "entryAgentId": community.entry_agent_id,
                "enabled": community.enabled,
            }
            for community in graph.communities
        }
        paths: set[tuple[str, str]] = set()
        for index, relationship in enumerate(graph.relationships):
            path = f"relationships[{index}]"
            pair = (relationship.source_agent_id, relationship.target_agent_id)
            if pair in paths or pair[0] == pair[1]:
                reject(path, rule="unique_path")
            paths.add(pair)
            rule = relationship_rule(pair[0], pair[1], agent_nodes, community_nodes)
            if rule is not None:
                reject(path, rule=rule)
            if len(relationship.allowed_context_fields) != len(
                set(relationship.allowed_context_fields)
            ):
                reject(path, rule="context_fields")
        if not required_relationship_pairs(agent_nodes, community_nodes) <= paths:
            reject("relationships", rule="required_lifecycle_path")

        for index, connection in enumerate(graph.mcp_connections):
            path = f"mcpConnections[{index}]"
            if not self.reviewed.permits_connection(connection):
                reject(path, rule="capability_review")
            errors.extend(
                self.detector.reference(connection.credential_ref, path + ".credentialRef")
            )
            if connection.approved_endpoint_ref not in self.destinations:
                reject(path + ".approvedEndpointRef", rule="approved_destination")
            actions = [item.action_name for item in connection.reviewed_capability_catalog]
            if len(actions) != len(set(actions)):
                reject(path, rule="reviewed_actions")
            for item in connection.reviewed_capability_catalog:
                if invalid_schema(item.input_schema):
                    reject(path + ".reviewedCapabilityCatalog", rule="input_schema")
        for index, tool in enumerate(graph.tools):
            path = f"tools[{index}]"
            if not self.reviewed.permits_tool(tool):
                reject(path, rule="capability_review")
            if tool.approved_destination_ref not in self.destinations:
                reject(path + ".approvedDestinationRef", rule="approved_destination")
            if tool.mcp_connection_id is not None and tool.mcp_connection_id not in connections:
                reject(path + ".mcpConnectionId", rule="connection_reference")
            if invalid_schema(tool.input_schema):
                reject(path + ".inputSchema", rule="input_schema")
        return errors
