"""Trusted revision projections; prompts and model guesses never authorize discovery."""

import copy
import json

import pytest


@pytest.fixture
def visibility(community_configuration):
    from caller_runtime.domain.visibility import CommunityVisibility

    return CommunityVisibility(community_configuration)


@pytest.fixture
def visibility_v2(community_configuration_v2):
    from caller_runtime.domain.visibility import CommunityVisibility

    return CommunityVisibility(community_configuration_v2 | {"revisionId": "revision-v2-visible"})


def test_us2_eligible_peer_discovery_does_not_grant_missing_or_reverse_handover(visibility_v2):
    assert {peer["agentId"] for peer in visibility_v2.view_for("a-entry")["peers"]} == {
        "a-source",
        "a-peer",
    }
    assert visibility_v2.handover_target("a-source", "a-entry")["agentId"] == "a-entry"
    with pytest.raises(PermissionError, match="handover_not_allowed"):
        visibility_v2.handover_target("a-entry", "a-source")


@pytest.mark.parametrize(
    "source,target",
    [
        ("a-source", "b-entry"),
        ("a-source", "a-disabled"),
        ("routing", "a-entry"),
        ("welcome", "a-entry"),
        ("a-source", "guessed-id"),
        ("a-source", "a-source"),
    ],
)
def test_us2_browser_handover_rejects_foreign_disabled_protected_and_guessed_targets(
    visibility_v2, source, target
):
    with pytest.raises(PermissionError, match="handover_not_allowed"):
        visibility_v2.handover_target(source, target)


def test_us2_initial_routing_and_source_preserving_community_transfer_are_distinct(visibility_v2):
    assert visibility_v2.initial_entry("routing", "beta")["agentId"] == "b-entry"
    transfer = visibility_v2.community_transfer("a-source", "beta")
    assert transfer["sourceCommunityId"] == "alpha"
    assert transfer["targetCommunityId"] == "beta"
    assert transfer["routing"]["agentId"] == "routing"
    assert transfer["entry"]["agentId"] == "b-entry"
    assert transfer["allowedContextFields"] == ["request_summary", "transfer_reason"]
    with pytest.raises(PermissionError):
        visibility_v2.community_transfer("b-entry", "alpha")
    with pytest.raises(PermissionError):
        visibility_v2.community_transfer("routing", "beta")
    with pytest.raises(PermissionError):
        visibility_v2.initial_entry("a-source", "beta")


@pytest.mark.parametrize("disabled", ["source", "destination", "entry"])
def test_us2_transfer_denies_disabled_source_destination_or_entry(
    community_configuration_v2, disabled
):
    from caller_runtime.domain.visibility import CommunityVisibility

    graph = community_configuration_v2
    if disabled == "source":
        next(agent for agent in graph["agents"] if agent["agentId"] == "a-source")["enabled"] = (
            False
        )
    elif disabled == "destination":
        graph["communities"][1]["enabled"] = False
    else:
        next(agent for agent in graph["agents"] if agent["agentId"] == "b-entry")["enabled"] = False
    policy = CommunityVisibility(graph | {"revisionId": "revision-v2-visible"})
    with pytest.raises(PermissionError, match="handover_not_allowed"):
        policy.community_transfer("a-source", "beta")


def test_us2_browser_policy_has_no_legacy_schema_fallback(visibility):
    with pytest.raises(PermissionError, match="handover_not_allowed"):
        visibility.handover_target("a-source", "a-entry")
    with pytest.raises(PermissionError, match="handover_not_allowed"):
        visibility.initial_entry("routing", "beta")


@pytest.mark.parametrize("version", ["2.0.0", "2.1.0"])
def test_browser_policy_accepts_both_schema_two_versions(community_configuration_v2, version):
    from caller_runtime.domain.visibility import CommunityVisibility

    graph = community_configuration_v2 | {"schemaVersion": version, "revisionId": "revision-v"}
    policy = CommunityVisibility(graph)
    assert policy.initial_entry("routing", "beta")["agentId"] == "b-entry"
    assert policy.handover_target("a-source", "a-entry")["agentId"] == "a-entry"
    assert {row["communityId"] for row in policy.view_for("routing")["communities"]} == {
        "alpha",
        "beta",
    }


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
