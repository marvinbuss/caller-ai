"""Pinned trusted discovery and target authorization; model text has no authority."""

import json
from collections.abc import Mapping
from typing import Any

Document = dict[str, Any]


class CommunityVisibility:
    """Own a detached validated-shape snapshot; expose only explicit model projections."""

    def __init__(self, configuration: Mapping[str, Any]) -> None:
        try:
            snapshot = json.dumps(
                dict(configuration), ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
            if len(snapshot) > 512 * 1024:
                raise ValueError("oversized_snapshot")
            source = json.loads(snapshot)
            revision = source["revisionId"]
            if not isinstance(revision, str) or not revision.strip() or len(revision) > 128:
                raise ValueError("invalid_revision")
            agents, communities, relationships = (
                source["agents"],
                source["communities"],
                source["relationships"],
            )
            if not all(isinstance(rows, list) for rows in (agents, communities, relationships)):
                raise ValueError("invalid_graph")
            identifiers = [agent["agentId"] for agent in agents]
            community_ids = [community["communityId"] for community in communities]
            if len(identifiers) != len(set(identifiers)) or len(community_ids) != len(
                set(community_ids)
            ):
                raise ValueError("duplicate_identity")
            for identifier in identifiers + community_ids:
                if (
                    not isinstance(identifier, str)
                    or not identifier.strip()
                    or len(identifier) > 128
                ):
                    raise ValueError("invalid_identity")
            for agent in agents:
                if (
                    agent["role"] not in {"ordinary", "welcome", "routing", "goodbye"}
                    or not isinstance(agent["enabled"], bool)
                    or not isinstance(agent["name"], str)
                    or not agent["name"].strip()
                    or not isinstance(agent["prompt"], str)
                ):
                    raise ValueError("invalid_agent")
                if agent["enabled"] and not agent["prompt"].strip():
                    raise ValueError("invalid_prompt")
                if agent["role"] == "ordinary" and agent["communityId"] not in community_ids:
                    raise ValueError("invalid_membership")
                if agent["role"] != "ordinary" and agent["communityId"] is not None:
                    raise ValueError("invalid_membership")
            for role in ("welcome", "routing", "goodbye"):
                matching = [agent for agent in agents if agent["role"] == role]
                if len(matching) != 1 or matching[0]["enabled"] is not True:
                    raise ValueError("invalid_protected_role")
            for community in communities:
                if (
                    not isinstance(community["enabled"], bool)
                    or not isinstance(community["name"], str)
                    or not isinstance(community["routingDescription"], str)
                    or community["entryAgentId"] not in identifiers
                ):
                    raise ValueError("invalid_community")
                entry = agents[identifiers.index(community["entryAgentId"])]
                if entry["role"] != "ordinary" or entry["communityId"] != community["communityId"]:
                    raise ValueError("invalid_entry")
            pairs = []
            for relationship in relationships:
                pair = relationship["sourceAgentId"], relationship["targetAgentId"]
                fields = relationship["allowedContextFields"]
                if (
                    pair[0] not in identifiers
                    or pair[1] not in identifiers
                    or pair[0] == pair[1]
                    or not isinstance(fields, list)
                    or len(fields) != len(set(fields))
                    or not set(fields) <= {"request_summary", "transfer_reason"}
                ):
                    raise ValueError("invalid_relationship")
                pairs.append(pair)
            if len(pairs) != len(set(pairs)):
                raise ValueError("duplicate_relationship")
        except (KeyError, TypeError, ValueError, RecursionError, AttributeError):
            raise ValueError("invalid_configuration_snapshot") from None
        self._snapshot = snapshot
        self._revision_id = revision

    @property
    def revision_id(self) -> str:
        return self._revision_id

    def _graph(self) -> Document:
        graph: Document = json.loads(self._snapshot)
        return graph

    def lifecycle_target(self, role: str) -> str:
        if role not in {"welcome", "routing", "goodbye"}:
            raise PermissionError("handover_not_allowed")
        for agent in self._graph()["agents"]:
            if agent["role"] == role:
                identifier: str = agent["agentId"]
                return identifier
        raise PermissionError("handover_not_allowed")

    @staticmethod
    def _enabled(graph: Document, identifier: str) -> Document:
        for agent in graph["agents"]:
            if agent["agentId"] != identifier or agent["enabled"] is not True:
                continue
            if agent["role"] == "ordinary" and not any(
                community["communityId"] == agent["communityId"] and community["enabled"] is True
                for community in graph["communities"]
            ):
                break
            result: Document = agent
            return result
        raise PermissionError("handover_not_allowed")

    @staticmethod
    def _projection(agent: Document) -> Document:
        return {
            key: agent[key]
            for key in ("agentId", "name", "prompt", "enabled", "role", "communityId")
        } | {"capabilityAssignments": []}

    def view_for(self, identifier: str) -> Document:
        graph = self._graph()
        agent = self._enabled(graph, identifier)
        peers = []
        communities = []
        if agent["role"] == "ordinary":
            peers = [
                {key: peer[key] for key in ("agentId", "name", "communityId")}
                for peer in graph["agents"]
                if peer["role"] == "ordinary"
                and peer["enabled"] is True
                and peer["communityId"] == agent["communityId"]
                and peer["agentId"] != identifier
            ]
        elif agent["role"] == "routing":
            for community in graph["communities"]:
                if community["enabled"] is not True:
                    continue
                try:
                    self._enabled(graph, community["entryAgentId"])
                except PermissionError:
                    continue
                communities.append(
                    {
                        key: community[key]
                        for key in ("communityId", "name", "routingDescription", "entryAgentId")
                    }
                )
        return {"agent": self._projection(agent), "peers": peers, "communities": communities}

    def resolve_handover(self, source_id: str, destination_id: str) -> Document:
        graph = self._graph()
        source, destination = self._enabled(graph, source_id), self._enabled(graph, destination_id)
        if (
            source_id == destination_id
            or source["role"] == "goodbye"
            or destination["role"] == "welcome"
            or (source["role"] == "welcome" and destination["role"] not in {"routing", "goodbye"})
            or (
                source["role"] == destination["role"] == "ordinary"
                and source["communityId"] != destination["communityId"]
            )
        ):
            raise PermissionError("handover_not_allowed")
        if (
            source["role"] == "routing"
            and destination["role"] == "ordinary"
            and not any(
                community["communityId"] == destination["communityId"]
                and community["entryAgentId"] == destination_id
                and community["enabled"] is True
                for community in graph["communities"]
            )
        ):
            raise PermissionError("handover_not_allowed")
        if not any(
            relationship["sourceAgentId"] == source_id
            and relationship["targetAgentId"] == destination_id
            for relationship in graph["relationships"]
        ):
            raise PermissionError("handover_not_allowed")
        return self._projection(destination)

    def allowed_context_fields(self, source_id: str, destination_id: str) -> frozenset[str]:
        self.resolve_handover(source_id, destination_id)
        for relationship in self._graph()["relationships"]:
            if (
                relationship["sourceAgentId"] == source_id
                and relationship["targetAgentId"] == destination_id
            ):
                return frozenset(relationship["allowedContextFields"])
        raise PermissionError("handover_not_allowed")
