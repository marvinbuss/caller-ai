"""Default communities, reachability and every refused edit with its rule and named item."""

import copy

import pytest


def test_default_communities_are_projected_from_the_protected_roles(configuration_graph):
    from management_app.services.graph_view import GOODBYE_NOTE, project

    view = project(configuration_graph)
    defaults = [row for row in view["communities"] if row["fixed"]]
    assert [(row["communityId"], row["name"]) for row in defaults] == [
        ("default-welcome", "Welcome"),
        ("default-routing", "Routing"),
        ("default-goodbye", "Goodbye"),
    ]
    for row, agent in zip(
        defaults, ("agent-welcome", "agent-routing", "agent-goodbye"), strict=True
    ):
        assert row["entryAgentId"] == agent and row["agentIds"] == [agent]
    assert defaults[2]["note"] == GOODBYE_NOTE == "Reachable from every community"
    assert view["links"][0] == {
        "id": "fixed-welcome-routing",
        "source": "default-welcome",
        "target": "default-routing",
        "kind": "fixed",
    }
    routed = {link["target"] for link in view["links"] if link["kind"] == "routing"}
    assert routed == {"community-alpha", "community-beta"}
    assert view["errors"] == []
    assert "default-welcome" not in {
        row["communityId"] for row in configuration_graph["communities"]
    }


@pytest.mark.parametrize("role", ["welcome", "routing", "goodbye"])
def test_a_missing_protected_agent_is_a_blocking_error(configuration_graph, role):
    from management_app.services.graph_view import project

    configuration_graph["agents"] = [
        agent for agent in configuration_graph["agents"] if agent["role"] != role
    ]
    view = project(configuration_graph)
    assert view["errors"] == [f"The pre-defined {role.title()} agent is missing."]
    assert not any(link["kind"] == "fixed" for link in view["links"])


def test_reachability_follows_routable_and_connection_chains(configuration_graph):
    from management_app.services.graph_view import project, unreachable

    graph = configuration_graph
    assert unreachable(graph) == []
    graph["communities"][1]["routable"] = False
    assert unreachable(graph) == ["community-beta"]
    graph["communityConnections"] = [
        {
            "connectionId": "alpha-to-beta",
            "sourceCommunityId": "community-alpha",
            "targetCommunityId": "community-beta",
            "allowedContextFields": [],
        }
    ]
    assert unreachable(graph) == []
    graph["communities"][0]["routable"] = False
    assert unreachable(graph) == ["community-alpha", "community-beta"]
    flags = {row["communityId"]: row.get("unreachable") for row in project(graph)["communities"]}
    assert flags["community-alpha"] and flags["community-beta"]


def test_disabled_communities_are_never_reported_unreachable(configuration_graph):
    from management_app.services.graph_view import unreachable

    configuration_graph["communities"][1].update(routable=False, enabled=False)
    assert unreachable(configuration_graph) == []


def test_a_disabled_entry_agent_breaks_reachability_and_marks_invalid(configuration_graph):
    from management_app.services.graph_view import invalid, unreachable

    configuration_graph["agents"][3]["enabled"] = False
    assert invalid(configuration_graph) == ["community-alpha"]
    assert unreachable(configuration_graph) == ["community-alpha"]


@pytest.mark.parametrize(
    "check,arguments,rule,named",
    [
        ("check_community_change", ("default-welcome",), "default_community", "Welcome"),
        ("check_community_change", ("default-routing",), "default_community", "Routing"),
        ("check_community_change", ("default-goodbye",), "default_community", "Goodbye"),
        ("check_link", ("default-welcome", "default-routing"), "default_connection", "Welcome"),
        ("check_link", ("default-routing", "default-goodbye"), "default_connection", "Routing"),
        ("check_link", ("community-alpha", "default-goodbye"), "default_connection", "Goodbye"),
        ("check_agent_change", ("agent-welcome", "delete"), "predefined_agent", "agent-welcome"),
        ("check_agent_change", ("agent-routing", "move"), "predefined_agent", "agent-routing"),
        ("check_agent_change", ("agent-goodbye", "disable"), "predefined_agent", "agent-goodbye"),
        ("check_agent_change", ("agent-goodbye", "tools"), "predefined_agent", "agent-goodbye"),
        ("check_agent_change", ("agent-alpha", "delete"), "entry_agent", "agent-alpha"),
        ("check_agent_change", ("agent-alpha", "disable"), "entry_agent", "alpha"),
        ("check_agent_change", ("agent-alpha", "move"), "entry_agent", "agent-alpha"),
        ("check_handover", ("agent-alpha", "agent-beta"), "handover_scope", "agent-beta"),
        ("check_handover", ("agent-welcome", "agent-routing"), "handover_scope", "Default"),
    ],
)
def test_each_refusal_names_its_rule_and_item(configuration_graph, check, arguments, rule, named):
    from management_app.services import graph_view

    before = copy.deepcopy(configuration_graph)
    refusal = getattr(graph_view, check)(configuration_graph, *arguments)
    assert refusal is not None
    assert refusal[0] == rule and named in refusal[1]
    assert configuration_graph == before


@pytest.mark.parametrize(
    "check,arguments",
    [
        ("check_community_change", ("community-alpha",)),
        ("check_link", ("default-routing", "community-alpha")),
        ("check_link", ("community-alpha", "community-beta")),
        ("check_agent_change", ("agent-alpha", "enable")),
        ("check_agent_change", ("agent-alpha", "tools")),
        ("check_handover", ("agent-alpha", "agent-alpha-peer")),
    ],
)
def test_allowed_edits_are_not_refused(graph_v2, check, arguments):
    from management_app.services import graph_view

    assert getattr(graph_view, check)(graph_v2, *arguments) is None


def test_deleting_a_connected_community_names_the_connections(graph_v2):
    from management_app.services.graph_view import connections_touching

    assert connections_touching(graph_v2, "community-beta") == ["alpha to beta"]
    assert connections_touching(graph_v2, "missing") == []
