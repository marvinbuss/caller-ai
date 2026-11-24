"""Delegation updates and the static delegation block (feature 007, US3: FR-011, FR-023, R5)."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from caller_runtime.services import modality_defaults

REPLACES = "Delegation update. This replaces all earlier delegation updates."
FITS = "Generate <|SpawnThinking|> when the request needs one of these capabilities."
NO_FIT = (
    "If no listed capability fits, say briefly that this service cannot help with it and "
    "offer what it can do."
)
CUT = "For other requests, Generate <|SpawnThinking|> too; the backend decides whether it can help."


def snapshot_of(document: dict):
    from caller_runtime.domain.configuration import (
        GRAPH_FIELDS,
        OPTIONAL_GRAPH_FIELDS,
        RevisionSnapshot,
        encoded,
    )

    policy = {key: document[key] for key in GRAPH_FIELDS} | {
        key: document[key] for key in OPTIONAL_GRAPH_FIELDS if key in document
    }
    document["policyHash"] = hashlib.sha256(encoded(policy)).hexdigest()
    return RevisionSnapshot.validate(json.loads(json.dumps(document)), etag='"update"')


@pytest.fixture
def graph(runtime_revision_v2):
    """alpha → beta connected; gamma routable and unconnected; delta not routable."""
    document = copy.deepcopy(runtime_revision_v2())
    for identifier, community, description, routable in (
        ("g-entry", "gamma", "", True),
        ("d-entry", "delta", "Synthetic delta routing", False),
    ):
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
        document["communities"].append(
            {
                "communityId": community,
                "name": community.title(),
                "routingDescription": description,
                "enabled": True,
                "entryAgentId": identifier,
                "routable": routable,
            }
        )
    return document


def slot(agent_id, community_id):
    return SimpleNamespace(active_agent_id=agent_id, active_community_id=community_id)


def test_the_update_lists_capabilities_and_replaces_earlier_updates(graph):
    from caller_runtime.services.delegation_update import delegation_update

    parts = delegation_update(snapshot_of(graph), slot("routing", None))
    text = "".join(parts)
    assert parts[0].startswith(REPLACES)
    # Before a community is active: all routable communities; a name only without a description.
    assert text == "\n".join(
        [
            REPLACES,
            "Backend tools:",
            "- Synthetic alpha routing",
            "- Synthetic beta routing",
            "- Gamma",
            FITS,
            NO_FIT,
        ]
    )
    assert "Synthetic delta routing" not in text
    # The same delegation phrase as the shipped voice conversation instructions.
    assert "Generate <|SpawnThinking|>" in modality_defaults.VOICE_INSTRUCTIONS


def test_the_reachable_set_follows_the_position(graph):
    from caller_runtime.services.delegation_update import reachable

    snapshot = snapshot_of(graph)
    assert reachable(snapshot, slot("welcome", None)) == ("alpha", "beta", "gamma")
    # Own community, connected ones, and routable ones through Routing.
    assert reachable(snapshot, slot("a-entry", "alpha")) == ("alpha", "beta", "gamma")
    graph["communities"][2]["routable"] = False
    snapshot = snapshot_of(graph)
    assert reachable(snapshot, slot("b-entry", "beta")) == ("alpha", "beta")
    assert reachable(snapshot, slot("a-entry", "alpha")) == ("alpha", "beta")
    assert reachable(snapshot, slot("g-entry", "gamma")) == ("alpha", "beta", "gamma")


def test_lines_are_cut_to_160_characters_and_the_update_to_2000_bytes(graph):
    from caller_runtime.services.delegation_update import delegation_update

    for index in range(30):
        identifier = f"x{index:02d}"
        graph["agents"].append(
            {
                "agentId": identifier + "-entry",
                "name": identifier + "-entry",
                "prompt": "Synthetic prompt",
                "enabled": True,
                "role": "ordinary",
                "communityId": identifier,
                "capabilityAssignments": [],
            }
        )
        graph["communities"].append(
            {
                "communityId": identifier,
                "name": "Extra " + identifier,
                "routingDescription": "é" * 200,
                "enabled": True,
                "entryAgentId": identifier + "-entry",
                "routable": True,
            }
        )
    parts = delegation_update(snapshot_of(graph), slot("routing", None))
    text = "".join(parts)
    assert len(text.encode("utf-8")) <= 2000
    lines = text.split("\n")
    tools = [line for line in lines if line.startswith("- ")]
    assert all(len(line) <= 162 for line in tools)
    assert "- " + "é" * 160 in lines
    assert tools[-1] == "- …and more capabilities"
    assert lines[-1] == CUT and NO_FIT not in text
    assert all(len(part.encode("utf-8")) <= 500 for part in parts)
    assert all(part.endswith("\n") or part is parts[-1] for part in parts)
    assert parts[0].startswith(REPLACES)


def test_an_update_is_needed_only_when_the_reachable_set_changes(graph):
    from caller_runtime.services.delegation_update import changed

    snapshot = snapshot_of(graph)
    first = changed(snapshot, slot("routing", None), None)
    assert first is not None
    assert changed(snapshot, slot("a-entry", "alpha"), first) is None
    graph["communityConnections"] = []
    snapshot = snapshot_of(graph)
    assert changed(snapshot, slot("b-entry", "beta"), first) is None
    graph["communities"][2]["routable"] = False
    snapshot = snapshot_of(graph)
    assert changed(snapshot, slot("b-entry", "beta"), first) == ("alpha", "beta")


def test_the_static_block_has_the_same_lines_and_fits_the_start_instructions(graph):
    from caller_runtime.composition import live_instructions
    from caller_runtime.services.delegation_update import static_delegation_block

    snapshot = snapshot_of(graph)
    block = static_delegation_block(snapshot)
    assert block == "\n".join(
        [
            "Delegation scope for this call:",
            "Backend tools:",
            "- Synthetic alpha routing",
            "- Synthetic beta routing",
            "- Gamma",
            FITS,
            NO_FIT,
        ]
    )
    assert len(block.encode("utf-8")) <= 4096
    instructions = live_instructions(snapshot, delegation_updates=False)
    assert instructions.endswith("\n\n" + block)
    assert len(instructions.encode("utf-8")) <= 16 * 1024
    assert block not in live_instructions(snapshot, delegation_updates=True)
