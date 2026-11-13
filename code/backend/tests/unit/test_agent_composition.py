"""Per-modality composition of agent instructions from the pinned projection (FR-026-FR-032)."""

import pytest

from tests.unit.test_configuration_cache import cache as cache
from tests.unit.test_configuration_cache import rehashed, schema_two_one

SETTINGS = {
    "voiceInstructions": "Synthetic voice instructions",
    "voicePlatformRules": "Synthetic voice rules",
    "voiceGlobalPrompt": "Synthetic voice global",
    "textPlatformRules": "Synthetic text rules",
    "textGlobalPrompt": "",
    "quietHandover": True,
}


def layered(document, **settings):
    document = schema_two_one(document, **(SETTINGS | settings))
    document["schemaVersion"] = "2.2.0"
    for agent in document["agents"]:
        agent["voiceAddition"] = "Synthetic voice addition for " + agent["agentId"]
        agent["textAddition"] = ""
    return rehashed(document)


def agents_for(snapshot, mode):
    from unittest.mock import Mock

    from agents.models.interface import Model

    from caller_runtime.services.conversation_agents import build_agents

    return build_agents(
        snapshot,
        model=Mock(spec=Model),
        mode=mode,
        capabilities=lambda _agent: [],
        policy_tools=lambda _agent: [],
    )


def test_layers_compose_in_order_and_empty_parts_are_skipped(runtime_revision_v2):
    from caller_runtime.domain.configuration import RevisionSnapshot

    snapshot = RevisionSnapshot.validate(layered(runtime_revision_v2()), etag='"layered"')
    graph = snapshot.for_runtime()
    speech, text = agents_for(snapshot, "speech"), agents_for(snapshot, "text")
    for agent in graph["agents"]:
        assert speech[agent["agentId"]].instructions == "\n\n".join(
            [
                "Synthetic voice rules",
                "Synthetic voice global",
                agent["prompt"],
                "Synthetic voice addition for " + agent["agentId"],
            ]
        )
        # The empty text global prompt and text addition add nothing, not even a separator.
        assert text[agent["agentId"]].instructions == "Synthetic text rules\n\n" + agent["prompt"]


def test_a_projection_without_layers_falls_back_to_the_shipped_rules(runtime_revision_v2):
    from caller_runtime.domain.configuration import RevisionSnapshot
    from caller_runtime.services.modality_defaults import (
        TEXT_PLATFORM_RULES,
        VOICE_PLATFORM_RULES,
    )

    snapshot = RevisionSnapshot.validate(runtime_revision_v2(), etag='"slice-one"')
    for mode, rules in (("speech", VOICE_PLATFORM_RULES), ("text", TEXT_PLATFORM_RULES)):
        for agent in snapshot.for_runtime()["agents"]:
            assert agents_for(snapshot, mode)[agent["agentId"]].instructions == (
                rules + "\n\n" + agent["prompt"]
            )


def test_composed_instructions_are_bounded_on_load(runtime_revision_v2):
    from caller_runtime.domain.configuration import COMPOSED_LIMIT, RevisionSnapshot

    document = layered(
        runtime_revision_v2(), voicePlatformRules="r" * 8192, voiceGlobalPrompt="g" * 8192
    )
    document["agents"][0]["prompt"] = "p" * 16384
    document["agents"][0]["voiceAddition"] = "a" * 4096
    assert COMPOSED_LIMIT == 36 * 1024
    with pytest.raises(ValueError, match="invalid_configuration_snapshot"):
        RevisionSnapshot.validate(rehashed(document), etag='"too-large"')


def test_live_instructions_come_from_voice_instructions_without_layers_or_additions(
    runtime_revision_v2,
):
    from caller_runtime.composition import LIVE_DELEGATION_SUFFIX, live_instructions
    from caller_runtime.domain.configuration import RevisionSnapshot
    from caller_runtime.services.modality_defaults import VOICE_INSTRUCTIONS

    snapshot = RevisionSnapshot.validate(layered(runtime_revision_v2()), etag='"layered"')
    instructions = live_instructions(snapshot)
    assert instructions == "Synthetic voice instructions" + LIVE_DELEGATION_SUFFIX
    for layer in ("Synthetic voice rules", "Synthetic voice global", "Synthetic voice addition"):
        assert layer not in instructions
    assert all(agent["prompt"] not in instructions for agent in snapshot.for_runtime()["agents"])
    slice_one = RevisionSnapshot.validate(runtime_revision_v2(), etag='"slice-one"')
    assert live_instructions(slice_one) == VOICE_INSTRUCTIONS + LIVE_DELEGATION_SUFFIX


def test_a_running_session_keeps_its_pinned_layers_after_a_new_publication(
    cache, runtime_revision_v2
):
    first = cache.install(layered(runtime_revision_v2(1)), etag='"first"')
    pinned = cache.pin()
    cache.install(
        layered(runtime_revision_v2(2), voicePlatformRules="Synthetic newer voice rules"),
        etag='"second"',
    )
    assert pinned is first
    agent_id = pinned.for_runtime()["agents"][0]["agentId"]
    assert agents_for(pinned, "speech")[agent_id].instructions.startswith("Synthetic voice rules")
    newer = cache.pin()
    assert agents_for(newer, "speech")[agent_id].instructions.startswith(
        "Synthetic newer voice rules"
    )
