"""The Community and Agent graph as managers see it, and the rules that refuse an edit.

The stored configuration keeps the Welcome, Routing and Goodbye agents outside any community.
`project` shows them as three fixed default communities (`default-welcome`, `default-routing`,
`default-goodbye`), each holding its pre-defined agent as entry agent, with the fixed link
Welcome to Routing, a Routing link to every routable custom community, and Goodbye labeled as
reachable from every community. Default communities are a view only and are never stored.

`unreachable` lists the enabled custom communities that no caller can reach: not routable and
not reached through community connections from a reachable, enabled community. Each `check_*`
function returns `(rule, message)` naming the item when an edit is refused, or `None`; callers
refuse before they change anything, so a refused edit leaves the draft as it was.
"""

from collections.abc import Mapping
from typing import Any, Final

Document = dict[str, Any]
Refusal = tuple[str, str]

DEFAULT_COMMUNITIES: Final = (
    ("default-welcome", "Welcome", "welcome"),
    ("default-routing", "Routing", "routing"),
    ("default-goodbye", "Goodbye", "goodbye"),
)
DEFAULT_IDS: Final = frozenset(identifier for identifier, _name, _role in DEFAULT_COMMUNITIES)
GOODBYE_NOTE: Final = "Reachable from every community"


def _agents(graph: Mapping[str, Any]) -> dict[str, Document]:
    return {agent["agentId"]: agent for agent in graph["agents"]}


def _communities(graph: Mapping[str, Any]) -> dict[str, Document]:
    return {community["communityId"]: community for community in graph["communities"]}


def _name(item: Mapping[str, Any], fallback: str) -> str:
    value = item.get("name")
    return value if isinstance(value, str) and value.strip() else fallback


def default_community_of(agent: Mapping[str, Any]) -> str | None:
    for identifier, _name_, role in DEFAULT_COMMUNITIES:
        if agent.get("role") == role:
            return identifier
    return None


def community_of(agent: Mapping[str, Any]) -> str | None:
    """The community an agent sits in on the graph, default communities included."""
    return default_community_of(agent) or agent.get("communityId")


def reachable(graph: Mapping[str, Any]) -> set[str]:
    agents = _agents(graph)
    communities = _communities(graph)

    def open_(identifier: str) -> bool:
        community = communities.get(identifier)
        if community is None or community["enabled"] is not True:
            return False
        entry = agents.get(community["entryAgentId"])
        return entry is not None and entry["enabled"] is True

    found = {
        identifier
        for identifier, community in communities.items()
        if open_(identifier) and community.get("routable", True) is True
    }
    pending = list(found)
    while pending:
        source = pending.pop()
        for edge in graph["communityConnections"]:
            target = edge["targetCommunityId"]
            if edge["sourceCommunityId"] == source and target not in found and open_(target):
                found.add(target)
                pending.append(target)
    return found


def unreachable(graph: Mapping[str, Any]) -> list[str]:
    """Enabled custom communities no caller can reach; disabled ones are never reported."""
    found = reachable(graph)
    return [
        community["communityId"]
        for community in graph["communities"]
        if community["enabled"] is True and community["communityId"] not in found
    ]


def invalid(graph: Mapping[str, Any]) -> list[str]:
    """Enabled custom communities whose entry agent is missing, foreign or disabled."""
    agents = _agents(graph)
    result = []
    for community in graph["communities"]:
        entry = agents.get(community["entryAgentId"])
        if community["enabled"] is True and (
            entry is None
            or entry["role"] != "ordinary"
            or entry["communityId"] != community["communityId"]
            or entry["enabled"] is not True
        ):
            result.append(community["communityId"])
    return result


def project(graph: Mapping[str, Any]) -> Document:
    """Default and custom communities with their agents, links and blocking errors."""
    agents = _agents(graph)
    errors: list[str] = []
    communities: list[Document] = []
    for identifier, name, role in DEFAULT_COMMUNITIES:
        members = [agent for agent in graph["agents"] if agent["role"] == role]
        if len(members) != 1:
            errors.append(f"The pre-defined {name} agent is missing.")
            continue
        communities.append(
            {
                "communityId": identifier,
                "name": name,
                "fixed": True,
                "enabled": True,
                "routable": False,
                "entryAgentId": members[0]["agentId"],
                "agentIds": [members[0]["agentId"]],
                "note": GOODBYE_NOTE if role == "goodbye" else "",
            }
        )
    blocked = set(unreachable(graph))
    broken = set(invalid(graph))
    for community in graph["communities"]:
        identifier = community["communityId"]
        communities.append(
            {
                "communityId": identifier,
                "name": community["name"],
                "fixed": False,
                "enabled": community["enabled"],
                "routable": community.get("routable", True),
                "entryAgentId": community["entryAgentId"],
                "agentIds": [
                    agent["agentId"]
                    for agent in graph["agents"]
                    if agent["role"] == "ordinary" and agent["communityId"] == identifier
                ],
                "note": "",
                "unreachable": identifier in blocked,
                "invalid": identifier in broken,
            }
        )
    links: list[Document] = []
    if not errors:
        links.append(
            {
                "id": "fixed-welcome-routing",
                "source": "default-welcome",
                "target": "default-routing",
                "kind": "fixed",
            }
        )
        for community in graph["communities"]:
            if community.get("routable", True) is True:
                links.append(
                    {
                        "id": "routing-" + community["communityId"],
                        "source": "default-routing",
                        "target": community["communityId"],
                        "kind": "routing",
                    }
                )
    for edge in graph["communityConnections"]:
        links.append(
            {
                "id": edge["connectionId"],
                "source": edge["sourceCommunityId"],
                "target": edge["targetCommunityId"],
                "kind": "connection",
            }
        )
    handovers = [
        {
            "id": edge["relationshipId"],
            "source": edge["sourceAgentId"],
            "target": edge["targetAgentId"],
            "kind": "handover",
        }
        for edge in graph["relationships"]
        if edge["sourceAgentId"] in agents and edge["targetAgentId"] in agents
    ]
    return {"communities": communities, "links": links, "handovers": handovers, "errors": errors}


def check_community_change(graph: Mapping[str, Any], community_id: str) -> Refusal | None:
    """Delete, rename, modify or add an agent to a community."""
    for identifier, name, _role in DEFAULT_COMMUNITIES:
        if community_id == identifier:
            return (
                "default_community",
                f"{name} is a default community; it cannot be changed or receive agents.",
            )
    return None


def check_link(graph: Mapping[str, Any], source: str, target: str) -> Refusal | None:
    """A link between two communities; only Routing to a custom community may change."""
    names = {identifier: name for identifier, name, _role in DEFAULT_COMMUNITIES}
    if source == "default-routing" and target not in DEFAULT_IDS:
        return None
    if source in DEFAULT_IDS or target in DEFAULT_IDS:
        label = names.get(source, names.get(target, "default"))
        return (
            "default_connection",
            f"The links of the default community {label} are fixed; only Routing to a custom "
            "community can change.",
        )
    return None


def check_agent_change(graph: Mapping[str, Any], agent_id: str, change: str) -> Refusal | None:
    """`change` is one of delete, move, disable, enable or tools."""
    agent = _agents(graph).get(agent_id)
    if agent is None:
        return None
    name = _name(agent, "This agent")
    if agent["role"] != "ordinary" and change in {"delete", "move", "disable", "enable", "tools"}:
        return (
            "predefined_agent",
            f"{name} is a pre-defined agent; it cannot be deleted, moved, disabled or given tools.",
        )
    if change in {"delete", "disable", "move"}:
        for community in graph["communities"]:
            if community["entryAgentId"] == agent_id:
                return (
                    "entry_agent",
                    f"{name} is the entry agent of {_name(community, 'its community')}. "
                    "Choose another entry agent first.",
                )
    return None


def check_handover(graph: Mapping[str, Any], source_id: str, target_id: str) -> Refusal | None:
    agents = _agents(graph)
    source, target = agents.get(source_id), agents.get(target_id)
    if source is None or target is None:
        return ("handover_scope", "Both agents must exist to draw a handover.")
    if source["role"] != "ordinary" or target["role"] != "ordinary":
        return ("handover_scope", "Default communities have no handovers between agents.")
    if source["communityId"] != target["communityId"]:
        return (
            "handover_scope",
            f"Handovers connect agents of the same community; {_name(source, 'the source')} "
            f"and {_name(target, 'the target')} are in different communities.",
        )
    return None


def connections_touching(graph: Mapping[str, Any], community_id: str) -> list[str]:
    """Names of the community links removed together with a community, for the confirmation."""
    names = {community["communityId"]: community["name"] for community in graph["communities"]}
    return [
        f"{names.get(edge['sourceCommunityId'], '?')} to {names.get(edge['targetCommunityId'], '?')}"
        for edge in graph["communityConnections"]
        if community_id in {edge["sourceCommunityId"], edge["targetCommunityId"]}
    ]
