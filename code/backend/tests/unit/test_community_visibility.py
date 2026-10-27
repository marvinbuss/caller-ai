"""Trusted revision projections; prompts and model guesses never authorize discovery."""

import copy
import json

import pytest


@pytest.fixture
def visibility(community_configuration):
    from caller_runtime.domain.visibility import CommunityVisibility

    return CommunityVisibility(community_configuration)


def test_ordinary_discovery_is_complete_enabled_and_community_local(visibility) -> None:
    view = visibility.view_for("a-source")
    assert {peer["agentId"] for peer in view["peers"]} == {"a-entry", "a-peer"}
    assert all(set(peer) == {"agentId", "name", "communityId"} for peer in view["peers"])
    assert view["communities"] == [] and view["agent"]["agentId"] == "a-source"
    text = json.dumps(view)
    assert all(
        value not in text
        for value in ("b-entry", "b-peer", "beta-tool", "Foreign tool marker", "a-disabled")
    )
    assert "Synthetic prompt for a-peer" not in text


def test_routing_sees_only_enabled_community_routing_metadata(visibility) -> None:
    view = visibility.view_for("routing")
    assert view["peers"] == []
    assert {item["communityId"] for item in view["communities"]} == {"alpha", "beta"}
    assert all(
        set(item) == {"communityId", "name", "routingDescription", "entryAgentId"}
        for item in view["communities"]
    )
    text = json.dumps(view)
    assert view["agent"]["capabilityAssignments"] == []
    assert all(
        value not in text
        for value in ("Synthetic prompt for b-entry", "b-peer", "beta-tool", "Foreign tool marker")
    )


@pytest.mark.parametrize("role", ["welcome", "goodbye"])
def test_protected_lifecycle_roles_are_not_foreign_directories(visibility, role) -> None:
    view = visibility.view_for(role)
    assert view["peers"] == view["communities"] == []
    assert all(value not in json.dumps(view) for value in ("a-entry", "b-entry", "beta-tool"))


@pytest.mark.parametrize(
    "source,target",
    [
        ("a-source", "b-entry"),
        ("a-source", "b-peer"),
        ("a-source", "a-disabled"),
        ("routing", "b-peer"),
        ("welcome", "a-entry"),
        ("goodbye", "routing"),
        ("a-source", "guessed-id"),
    ],
)
def test_guessed_or_forbidden_destination_is_rejected_without_content(
    visibility, source, target
) -> None:
    with pytest.raises(PermissionError, match="^handover_not_allowed$"):
        visibility.resolve_handover(source, target)


def test_relationships_restrict_handover_not_same_community_discovery(
    community_configuration,
) -> None:
    from caller_runtime.domain.visibility import CommunityVisibility

    community_configuration["relationships"] = [
        item
        for item in community_configuration["relationships"]
        if (item["sourceAgentId"], item["targetAgentId"]) != ("a-source", "a-peer")
    ]
    visibility = CommunityVisibility(community_configuration)
    assert "a-peer" in {item["agentId"] for item in visibility.view_for("a-source")["peers"]}
    with pytest.raises(PermissionError):
        visibility.resolve_handover("a-source", "a-peer")


def test_valid_local_and_routing_mediated_destinations_are_resolved(visibility) -> None:
    for source, target in (
        ("a-source", "a-peer"),
        ("a-source", "routing"),
        ("routing", "b-entry"),
        ("a-source", "goodbye"),
    ):
        projection = visibility.resolve_handover(source, target)
        assert projection["agentId"] == target
        assert projection["capabilityAssignments"] == []
        assert set(projection) == {
            "agentId",
            "name",
            "prompt",
            "enabled",
            "role",
            "communityId",
            "capabilityAssignments",
        }


def test_views_are_detached_and_pinned_against_input_or_return_mutation(
    community_configuration,
) -> None:
    from caller_runtime.domain.visibility import CommunityVisibility

    original = copy.deepcopy(community_configuration)
    visibility = CommunityVisibility(community_configuration)
    community_configuration["agents"][3]["prompt"] = "Unpublished changed prompt"
    view = visibility.view_for("a-source")
    view["agent"]["prompt"] = "Caller-mutated projection"
    view["peers"].clear()
    fresh = visibility.view_for("a-source")
    assert visibility.revision_id == original["revisionId"]
    assert fresh["agent"]["prompt"] == "Synthetic prompt for a-source" and len(fresh["peers"]) == 2


def test_disabled_community_and_entry_cannot_be_materialized(community_configuration) -> None:
    from caller_runtime.domain.visibility import CommunityVisibility

    community_configuration["communities"][1]["enabled"] = False
    visibility = CommunityVisibility(community_configuration)
    assert [item["communityId"] for item in visibility.view_for("routing")["communities"]] == [
        "alpha"
    ]
    with pytest.raises(PermissionError):
        visibility.resolve_handover("routing", "b-entry")
