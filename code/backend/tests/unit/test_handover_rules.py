"""Handover destinations (feature 007): which agents each agent may hand over to, and how."""

import copy
import hashlib
import json

import pytest


def snapshot_of(document: dict):
    from caller_runtime.domain.configuration import (
        GRAPH_FIELDS,
        OPTIONAL_GRAPH_FIELDS,
        RevisionSnapshot,
        encoded,
    )

    document["schemaVersion"] = "2.3.0"
    policy = {key: document[key] for key in GRAPH_FIELDS} | {
        key: document[key] for key in OPTIONAL_GRAPH_FIELDS if key in document
    }
    document["policyHash"] = hashlib.sha256(encoded(policy)).hexdigest()
    return RevisionSnapshot.validate(json.loads(json.dumps(document)), etag='"rules"')


@pytest.fixture
def graph(runtime_revision_v2):
    """alpha (a-entry, a-source, a-peer) connects to beta (b-entry, b-peer); gamma is routable
    and unconnected; delta is not routable."""
    document = copy.deepcopy(runtime_revision_v2())
    for identifier, community in (("g-entry", "gamma"), ("d-entry", "delta")):
        document["agents"].append(
            {
                "agentId": identifier,
                "name": identifier,
                "prompt": "Synthetic prompt for " + identifier,
                "enabled": True,
                "role": "ordinary",
                "communityId": community,
                "capabilityAssignments": [],
            }
        )
    document["communities"] += [
        {
            "communityId": "gamma",
            "name": "Gamma",
            "routingDescription": "Synthetic gamma routing",
            "enabled": True,
            "entryAgentId": "g-entry",
            "routable": True,
        },
        {
            "communityId": "delta",
            "name": "Delta",
            "routingDescription": "Synthetic delta routing",
            "enabled": True,
            "entryAgentId": "d-entry",
            "routable": False,
        },
    ]
    return document


def rows(destinations):
    return [
        (item.name, item.path, item.agent_id, item.community_id, item.community_choices)
        for item in destinations
    ]


def test_routing_offers_entry_agents_of_enabled_routable_communities_and_goodbye(graph):
    from caller_runtime.services.handover_rules import destinations

    offered = destinations(snapshot_of(graph), "routing")
    assert rows(offered) == [
        ("handover_0", "routing", "a-entry", "alpha", ()),
        ("handover_1", "routing", "b-entry", "beta", ()),
        ("handover_2", "routing", "g-entry", "gamma", ()),
        ("handover_3", "lifecycle", "goodbye", None, ()),
    ]
    assert [item.description for item in offered[:3]] == [
        "Synthetic alpha routing",
        "Synthetic beta routing",
        "Synthetic gamma routing",
    ]


def test_ordinary_agents_get_colleagues_connected_entries_routing_and_goodbye(graph):
    from caller_runtime.services.handover_rules import destinations

    snapshot = snapshot_of(graph)
    assert rows(destinations(snapshot, "a-source")) == [
        ("handover_0", "peer", "a-entry", "alpha", ()),
        ("handover_1", "peer", "a-peer", "alpha", ()),
        ("handover_2", "direct", "b-entry", "beta", ()),
        ("handover_3", "lifecycle", "goodbye", None, ()),
        ("handover_4", "routing", "routing", None, ("gamma",)),
    ]
    routing = destinations(snapshot, "a-source")[4]
    assert routing.description == "Routing can reach: Gamma"
    # beta has no connection back to alpha, so Routing can reach both other routable ones.
    assert rows(destinations(snapshot, "b-entry")) == [
        ("handover_0", "peer", "b-peer", "beta", ()),
        ("handover_1", "lifecycle", "goodbye", None, ()),
        ("handover_2", "routing", "routing", None, ("alpha", "gamma")),
    ]
    assert destinations(snapshot, "welcome") == [] == destinations(snapshot, "goodbye")


def test_routing_is_offered_only_when_a_routable_community_is_neither_own_nor_connected(graph):
    from caller_runtime.services.handover_rules import destinations

    graph["communityConnections"].append(
        {
            "connectionId": "alpha-to-gamma",
            "sourceCommunityId": "alpha",
            "targetCommunityId": "gamma",
            "allowedContextFields": [],
        }
    )
    offered = destinations(snapshot_of(graph), "a-peer")
    assert [item.path for item in offered] == ["peer", "direct", "direct", "lifecycle"]
    assert all(item.agent_id != "routing" for item in offered)


def test_descriptions_are_the_agent_description_or_name_cut_to_1000_characters(graph):
    from caller_runtime.services.handover_rules import destinations

    for agent in graph["agents"]:
        if agent["agentId"] == "a-entry":
            agent["description"] = "Handles synthetic alpha requests"
    graph["communities"][1]["routingDescription"] = "b" * 1500
    offered = {item.agent_id: item for item in destinations(snapshot_of(graph), "a-source")}
    assert offered["a-entry"].description == "Handles synthetic alpha requests"
    assert offered["a-peer"].description == "a-peer"
    assert offered["b-entry"].description == "b" * 1000


def test_disabled_agents_and_communities_are_never_offered(graph):
    from caller_runtime.services.handover_rules import destinations

    for agent in graph["agents"]:
        if agent["agentId"] == "a-peer":
            agent["enabled"] = False
    graph["communities"][1]["enabled"] = False
    offered = destinations(snapshot_of(graph), "a-source")
    assert [item.agent_id for item in offered] == ["a-entry", "goodbye", "routing"]
    assert offered[2].community_choices == ("gamma",)


def test_during_a_transfer_routing_enables_only_the_pending_destination(graph):
    from caller_runtime.services.handover_rules import destinations, enabled

    offered = destinations(snapshot_of(graph), "routing")
    assert [item.agent_id for item in offered if enabled(item, None)] == [
        "a-entry",
        "b-entry",
        "g-entry",
        "goodbye",
    ]
    assert [item.agent_id for item in offered if enabled(item, "gamma")] == ["g-entry"]


def test_names_follow_the_sorted_destination_names(graph):
    from caller_runtime.services.handover_rules import destinations

    for agent in graph["agents"]:
        if agent["agentId"] == "a-entry":
            agent["name"] = "Zeta entry"
    offered = destinations(snapshot_of(graph), "a-source")
    assert [(item.name, item.agent_id) for item in offered] == [
        ("handover_0", "a-peer"),
        ("handover_1", "b-entry"),
        ("handover_2", "goodbye"),
        ("handover_3", "routing"),
        ("handover_4", "a-entry"),
    ]


@pytest.mark.parametrize("mode", ["speech", "text"])
@pytest.mark.parametrize("announce", [True, False, None])
def test_the_announce_rule_follows_the_platform_rules_only_when_enabled(graph, mode, announce):
    # FR-026: quiet by default; with announceAgentChanges the receiving agent says who helps.
    from caller_runtime.domain.configuration import compose_instructions
    from caller_runtime.services.modality_defaults import (
        ANNOUNCE_RULE,
        TEXT_PLATFORM_RULES,
        VOICE_PLATFORM_RULES,
    )

    assert ANNOUNCE_RULE == (
        "- After a handover, the receiving agent says briefly who is now helping."
    )
    if announce is not None:
        graph["conversationSettings"] = {"announceAgentChanges": announce}
    projection = snapshot_of(graph).for_runtime()
    agent = next(item for item in projection["agents"] if item["agentId"] == "a-entry")
    composed = compose_instructions(projection, agent, mode)
    rules = VOICE_PLATFORM_RULES if mode == "speech" else TEXT_PLATFORM_RULES
    if announce:
        assert composed.startswith(rules + "\n" + ANNOUNCE_RULE + "\n\n")
    else:
        assert composed.startswith(rules + "\n\n") and "who is now helping" not in composed
