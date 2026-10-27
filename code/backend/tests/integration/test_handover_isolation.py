"""Real scripted SDK transfer isolation; not provider or live latency acceptance."""

import asyncio
import copy
import json
from dataclasses import asdict

import pytest

pytestmark = pytest.mark.anyio


def test_transfer_filter_copies_only_edge_approved_fields(community_configuration, transfer_policy):
    from caller_runtime.domain.visibility import CommunityVisibility

    source = {
        "request_summary": "Synthetic current request",
        "transfer_reason": "Synthetic reason",
        "conversation": [{"content": "Synthetic full transcript"}],
        "audio": bytearray(b"synthetic-audio"),
        "tool_results": {"payload": "Synthetic unrestricted result"},
        "prompt": "Synthetic source prompt",
    }
    before = copy.deepcopy(source)
    selected = transfer_policy.filter(
        CommunityVisibility(community_configuration), "a-source", "a-peer", source
    )
    assert selected == {
        "request_summary": "Synthetic current request",
        "transfer_reason": "Synthetic reason",
    }
    assert source == before and selected is not source
    assert "Synthetic full transcript" not in json.dumps(selected)


def test_transfer_filter_obeys_pinned_edge_narrowing(community_configuration, transfer_policy):
    from caller_runtime.domain.visibility import CommunityVisibility

    edge = next(
        item
        for item in community_configuration["relationships"]
        if item["sourceAgentId"] == "a-source" and item["targetAgentId"] == "a-peer"
    )
    edge["allowedContextFields"] = ["request_summary"]
    assert transfer_policy.filter(
        CommunityVisibility(community_configuration),
        "a-source",
        "a-peer",
        {
            "request_summary": "Synthetic current request",
            "transfer_reason": "Synthetic excluded reason",
        },
    ) == {"request_summary": "Synthetic current request"}


@pytest.mark.parametrize(
    "value",
    ["x" * 4097, "\u00e9" * 2049, {"unrestricted": "object"}],
    ids=["ascii-oversize", "utf8-oversize", "non-text"],
)
def test_transfer_filter_rejects_oversize_or_non_text_without_value_reflection(
    community_configuration, transfer_policy, value
):
    from caller_runtime.domain.visibility import CommunityVisibility

    with pytest.raises(ValueError, match="^invalid_transfer_context$"):
        transfer_policy.filter(
            CommunityVisibility(community_configuration),
            "a-source",
            "a-peer",
            {"request_summary": value},
        )


@pytest.mark.parametrize(
    "kind", ["connection", "userinfo", "signature", "bearer", "private-key", "secret-reference"]
)
def test_transfer_filter_rejects_recognized_credentials_without_echo(
    community_configuration, transfer_policy, kind
):
    from uuid import uuid4

    from caller_runtime.domain.visibility import CommunityVisibility

    candidate = str(uuid4())
    values = {
        "connection": "=".join(("Password", candidate)),
        "userinfo": "https://fixture-user:" + candidate + "@example.invalid/path",
        "signature": "https://example.invalid/path?sig=" + candidate,
        "bearer": " ".join(("Bearer", candidate)),
        "private-key": "-----BEGIN " + "PRIVATE KEY-----\n" + candidate,
        "secret-reference": "secret://fixture-alias",
    }
    with pytest.raises(PermissionError, match="^transfer_content_prohibited$") as failure:
        transfer_policy.filter(
            CommunityVisibility(community_configuration),
            "a-source",
            "a-peer",
            {"request_summary": values[kind]},
        )
    assert candidate not in str(failure.value)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_fresh_sdk_transfer_replays_only_approved_fields_and_retains_phone(handover_host):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        source = coordinator.current("session-us3-fixture")
        sensitive = "synthetic-history-audio-tool-content"
        await source.send_message(sensitive)
        result = await coordinator.transfer(
            "session-us3-fixture",
            "a-peer",
            context={
                "request_summary": "Synthetic current request",
                "transfer_reason": "Synthetic transfer reason",
                "conversation": sensitive,
                "audio": sensitive,
                "tool_results": sensitive,
            },
        )
        assert result.outcome == "committed"
        destination = coordinator.current("session-us3-fixture")
        assert destination is not source and destination.model is models.models[1]
        event = await asyncio.wait_for(anext(aiter(destination)), 1)
        assert event.type == "history_updated" and event.history == []
        assert event.info.context.context == {
            "session_id": "session-us3-fixture",
            "revision_id": "revision-us3-fixture",
        }
        sent = json.dumps([asdict(item) for item in models.models[1].sent], default=str)
        assert "Synthetic current request" in sent and "Synthetic transfer reason" in sent
        assert sensitive not in sent and sensitive not in json.dumps(
            models.models[1].options, default=str
        )
        metadata = await repository.read("session-us3-fixture")
        assert metadata["configurationRevisionId"] == "revision-us3-fixture"
        assert (
            metadata["physicalOwnerId"] == "physical-us3-owner" and metadata["connectionEpoch"] == 1
        )
        assert metadata["activeAgentId"] == "a-peer" and metadata["agentEpoch"] == 2
        assert coordinator.phone_for("session-us3-fixture") is phone and phone.hangups == 0
        assert models.models[0].closed == 1 and models.models[0].listeners == []
        records = await repository.handovers("session-us3-fixture")
        assert records[-1]["outcome"] == "committed"
        assert sensitive not in json.dumps(
            records
        ) and "Synthetic current request" not in json.dumps(records)
        assert sensitive not in json.dumps(
            [dict(span.attributes or {}) for span in telemetry.spans]
        )


async def test_sdk_prompt_contains_only_the_trusted_role_directory(handover_host):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        source = json.dumps(models.models[0].options, default=str)
        assert "a-entry" in source and "a-peer" in source
        assert all(
            marker not in source
            for marker in ("b-entry", "b-peer", "beta-tool", "Synthetic prompt for a-peer")
        )
        assert (
            await coordinator.transfer("session-us3-fixture", "routing", context={})
        ).outcome == "committed"
        routing = json.dumps(models.models[1].options, default=str)
        assert "b-entry" in routing and "Synthetic beta routing" in routing
        assert all(
            marker not in routing
            for marker in ("b-peer", "beta-tool", "Synthetic prompt for b-entry")
        )


async def test_failed_destination_preserves_source_epoch_and_live_sdk_session(handover_host):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        source = coordinator.current("session-us3-fixture")
        models.next_behavior = "fail"
        result = await coordinator.transfer(
            "session-us3-fixture",
            "a-peer",
            context={"request_summary": "Synthetic bounded request"},
        )
        assert result.outcome == "failed" and result.reason_code == "destination_unavailable"
        assert coordinator.current("session-us3-fixture") is source
        await source.send_message("synthetic-source-retained")
        assert models.models[0].closed == 0 and models.models[1].closed == 1
        assert models.models[1].listeners == [] and phone.hangups == 0
        metadata = await repository.read("session-us3-fixture")
        assert metadata["activeAgentId"] == "a-source" and metadata["agentEpoch"] == 1
        assert "synthetic-private-model-details" not in str(result)


async def test_cross_community_transfer_requires_routing_and_never_reuses_history(handover_host):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        direct = await coordinator.transfer(
            "session-us3-fixture", "b-entry", context={"request_summary": "Synthetic beta request"}
        )
        assert direct.outcome == "failed" and len(models.models) == 1
        assert (
            await coordinator.transfer(
                "session-us3-fixture",
                "routing",
                context={"request_summary": "Synthetic beta request"},
            )
        ).outcome == "committed"
        assert (
            await coordinator.transfer(
                "session-us3-fixture",
                "b-entry",
                context={"request_summary": "Synthetic beta request"},
            )
        ).outcome == "committed"
        assert coordinator.current("session-us3-fixture").model is models.models[2]
        metadata = await repository.read("session-us3-fixture")
        assert metadata["activeCommunityId"] == "beta" and metadata["agentEpoch"] == 3
        assert metadata["connectionEpoch"] == 1 and phone.hangups == 0
