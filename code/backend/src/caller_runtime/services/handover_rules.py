"""Which agents the active agent may hand over to, built from the pinned revision (FR-008, FR-028).

`destinations` lists, for one agent, exactly the handovers its role and the published graph allow,
sorted by destination name and named `handover_<n>`: Routing reaches the entry agent of every
enabled, routable community and Goodbye; an ordinary agent reaches its colleagues along
relationships, the entry agents of connected communities, Routing for a routable community that is
neither its own nor connected, and Goodbye; Welcome and Goodbye reach no one. The list grants
nothing on its own: `ConversationPolicy.handover` rechecks every choice against the same rules.
"""

from dataclasses import dataclass
from typing import Any, Literal

from caller_runtime.domain.configuration import RevisionSnapshot

HandoverPath = Literal["peer", "direct", "routing", "lifecycle"]
DESCRIPTION_CHARS = 1000
GOODBYE_DESCRIPTION = "Published-policy goodbye"


@dataclass(frozen=True, slots=True)
class Destination:
    """One offered handover: its tool name, path, destination agent and model-facing text."""

    name: str
    path: HandoverPath
    agent_id: str
    agent_name: str
    role: str
    community_id: str | None
    description: str
    community_choices: tuple[str, ...] = ()


def _key(name: str, identifier: str) -> tuple[str, str]:
    return name.casefold(), identifier


def _entry(graph: dict[str, Any], community: dict[str, Any]) -> dict[str, Any] | None:
    """The enabled entry agent of an enabled community, if both are usable."""
    if community["enabled"] is not True:
        return None
    for agent in graph["agents"]:
        if (
            agent["agentId"] == community["entryAgentId"]
            and agent["enabled"] is True
            and agent["role"] == "ordinary"
            and agent["communityId"] == community["communityId"]
        ):
            entry: dict[str, Any] = agent
            return entry
    return None


def destinations(snapshot: RevisionSnapshot, agent_id: str) -> list[Destination]:
    graph = snapshot.for_runtime()
    agents = {agent["agentId"]: agent for agent in graph["agents"]}
    source = agents.get(agent_id)
    if source is None or source["enabled"] is not True:
        return []
    by_role = {agent["role"]: agent for agent in graph["agents"] if agent["role"] != "ordinary"}
    communities = {community["communityId"]: community for community in graph["communities"]}
    routable = {
        identifier: (community, entry)
        for identifier, community in communities.items()
        if community.get("routable", True) is True
        and (entry := _entry(graph, community)) is not None
    }
    found: list[tuple[HandoverPath, dict[str, Any], str | None, str, tuple[str, ...]]] = []
    goodbye = by_role["goodbye"]
    if source["role"] == "routing":
        for identifier, (community, entry) in routable.items():
            found.append(("routing", entry, identifier, community["routingDescription"], ()))
        found.append(("lifecycle", goodbye, None, GOODBYE_DESCRIPTION, ()))
    elif source["role"] == "ordinary":
        own = source["communityId"]
        if own not in communities or communities[own]["enabled"] is not True:
            return []
        for edge in graph["relationships"]:
            target = agents.get(edge["targetAgentId"])
            if (
                edge["sourceAgentId"] == agent_id
                and target is not None
                and target["agentId"] != agent_id
                and target["enabled"] is True
                and target["role"] == "ordinary"
                and target["communityId"] == own
            ):
                found.append(("peer", target, own, target.get("description") or target["name"], ()))
        connected = set()
        for edge in graph["communityConnections"]:
            community = communities.get(edge["targetCommunityId"])
            if edge["sourceCommunityId"] != own or community is None:
                continue
            connected.add(community["communityId"])
            entry = _entry(graph, community)
            if entry is not None and community["communityId"] != own:
                found.append(
                    ("direct", entry, community["communityId"], community["routingDescription"], ())
                )
        others = sorted(
            (
                community
                for identifier, (community, _) in routable.items()
                if identifier != own and identifier not in connected
            ),
            key=lambda community: _key(community["name"], community["communityId"]),
        )
        if others:
            names = ", ".join(community["name"] for community in others)
            choices = tuple(community["communityId"] for community in others)
            reach = "Routing can reach: " + names
            found.append(("routing", by_role["routing"], None, reach, choices))
        found.append(("lifecycle", goodbye, None, GOODBYE_DESCRIPTION, ()))
    found.sort(key=lambda row: _key(row[1]["name"], row[1]["agentId"]))
    return [
        Destination(
            name=f"handover_{index}",
            path=path,
            agent_id=agent["agentId"],
            agent_name=agent["name"],
            role=agent["role"],
            community_id=community_id,
            description=description[:DESCRIPTION_CHARS],
            community_choices=choices,
        )
        for index, (path, agent, community_id, description, choices) in enumerate(found)
    ]


def enabled(destination: Destination, pending: str | None) -> bool:
    """Routing during a transfer may only hand over to the pending destination's entry agent."""
    return pending is None or (
        destination.path == "routing" and destination.community_id == pending
    )
