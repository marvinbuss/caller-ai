"""Scripted US3 journeys only; no telephony, model network or live latency claim."""

import json
import math
import time

import pytest

from caller_runtime.domain.visibility import CommunityVisibility
from caller_runtime.services.routing import RoutingService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Output:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.clarifications = []
        self.goodbyes = []

    async def clarify(self, session_id):
        self.clarifications.append(session_id)

    async def goodbye(self, session_id):
        assert self.coordinator.metadata_for(session_id).active_agent_id == "goodbye"
        self.goodbyes.append(session_id)


def routing(coordinator, configuration):
    output = Output(coordinator)
    return RoutingService(
        coordinator=coordinator, visibility=CommunityVisibility(configuration), output=output
    ), output


async def test_foreign_community_selection_always_commits_routing_then_entry(
    handover_host, community_configuration
):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        service, output = routing(coordinator, community_configuration)
        result = await service.select_community(
            "session-us3-fixture", "beta", context={"request_summary": "Synthetic beta request"}
        )
        assert result.outcome == "committed"
        records = await repository.handovers("session-us3-fixture")
        assert [record["destinationAgentId"] for record in records] == ["routing", "b-entry"]
        saved = await repository.read("session-us3-fixture")
        assert saved["activeCommunityId"] == "beta" and saved["agentEpoch"] == 3
        assert coordinator.phone_for("session-us3-fixture") is phone
        assert phone.hangups == 0 and output.clarifications == []


async def test_unknown_request_clarifies_once_then_uses_protected_goodbye(
    handover_host, community_configuration
):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        service, output = routing(coordinator, community_configuration)
        first = await service.select_community("session-us3-fixture", None, context={})
        assert first.outcome == "failed" and len(output.clarifications) == 1
        assert len(models.models) == 1 and phone.hangups == 0
        await service.select_community("session-us3-fixture", "guessed-community", context={})
        saved = await repository.read("session-us3-fixture")
        assert len(output.clarifications) == len(output.goodbyes) == 1
        assert saved["state"] == "Ended" and saved["terminalReasonCode"] == "completed"
        assert saved["activeAgentId"] == "goodbye" and phone.hangups == 1
        assert all(model.closed == 1 for model in models.models)


async def test_ten_commits_stop_ordinary_transfers_then_clarify_and_close(
    handover_host, community_configuration
):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        service, output = routing(coordinator, community_configuration)
        assert (
            await service.transfer("session-us3-fixture", "b-entry", context={})
        ).outcome == "failed"
        models.next_behavior = "fail"
        assert (
            await service.transfer("session-us3-fixture", "a-peer", context={})
        ).outcome == "failed"
        for index in range(10):
            target = "a-peer" if index % 2 == 0 else "a-source"
            assert (
                await service.transfer("session-us3-fixture", target, context={})
            ).outcome == "committed"
        prepared = len(models.models)
        await service.transfer("session-us3-fixture", "a-peer", context={})
        assert len(output.clarifications) == 1 and len(models.models) == prepared
        await service.transfer("session-us3-fixture", "a-peer", context={})
        saved = await repository.read("session-us3-fixture")
        assert saved["state"] == "Ended" and saved["activeAgentId"] == "goodbye"
        assert len(output.goodbyes) == 1 and phone.hangups == 1
        commits = [
            item
            for item in await repository.handovers("session-us3-fixture")
            if item["outcome"] == "committed"
        ]
        assert len([item for item in commits if item["destinationAgentId"] != "goodbye"]) == 10


async def test_scripted_decision_to_fenced_speech_timing_is_not_live_p95(
    handover_host, record_property
):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        timings = []
        for index in range(8):
            started = time.perf_counter()
            target = "a-peer" if index % 2 == 0 else "a-source"
            assert (
                await coordinator.transfer("session-us3-fixture", target, context={})
            ).outcome == "committed"
            epoch = (await repository.read("session-us3-fixture"))["agentEpoch"]
            assert await coordinator.emit(
                "session-us3-fixture", epoch, b"synthetic-relevant-speech"
            )
            timings.append(time.perf_counter() - started)
        synthetic_p95 = sorted(timings)[math.ceil(0.95 * len(timings)) - 1]
        record_property("scripted_decision_to_fenced_speech_seconds", json.dumps(timings))
        assert synthetic_p95 < 2
        assert len(phone.output) == 8 and phone.hangups == 0
