"""Deterministic owner/epoch/deadline/loop contracts over scripted SDK sessions."""

import asyncio
import json
import time
from datetime import UTC, datetime

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_competing_transfers_commit_one_owner_and_fence_obsolete_output(handover_host):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        models.next_behavior = "block"
        models.preparing.clear()
        first = asyncio.create_task(
            coordinator.transfer("session-us3-fixture", "a-peer", context={})
        )
        await asyncio.wait_for(models.preparing.wait(), 1)
        competing = await coordinator.transfer("session-us3-fixture", "a-entry", context={})
        assert competing.outcome == "failed" and competing.reason_code == "transfer_in_progress"
        assert await coordinator.emit("session-us3-fixture", 2, b"premature-output") is False
        models.release.set()
        assert (await first).outcome == "committed"
        assert await coordinator.emit("session-us3-fixture", 1, b"obsolete-output") is False
        assert await coordinator.emit("session-us3-fixture", 2, b"current-output") is True
        assert phone.output == [b"current-output"]


@pytest.mark.anyio
async def test_hangup_during_preparation_cancels_destination_and_prevents_late_commit(
    handover_host,
):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        models.next_behavior = "block"
        models.preparing.clear()
        transfer = asyncio.create_task(
            coordinator.transfer("session-us3-fixture", "a-peer", context={})
        )
        await asyncio.wait_for(models.preparing.wait(), 1)
        await coordinator.disconnect("session-us3-fixture")
        models.release.set()
        result = await asyncio.wait_for(transfer, 1)
        assert result.outcome == "canceled"
        metadata = await repository.read("session-us3-fixture")
        assert (
            metadata["state"] == "Ended" and metadata["terminalReasonCode"] == "caller_disconnected"
        )
        assert metadata["agentEpoch"] == 1
        assert await coordinator.emit("session-us3-fixture", 2, b"late-output") is False
        assert all(model.closed == 1 for model in models.models)


@pytest.mark.anyio
async def test_absolute_transfer_deadline_bounds_preparation_and_cleanup(handover_host):
    async with handover_host(budget=0.05) as (coordinator, repository, phone, models, telemetry):
        models.next_behavior = "block"
        started = time.monotonic()
        result = await coordinator.transfer("session-us3-fixture", "a-peer", context={})
        assert time.monotonic() - started < 0.5
        assert result.outcome == "failed" and result.reason_code == "deadline_exceeded"
        assert coordinator.current("session-us3-fixture").model is models.models[0]
        assert models.models[0].closed == 0 and models.models[1].closed == 1


@pytest.mark.anyio
async def test_unavailable_entry_is_rejected_before_new_sdk_preparation(
    handover_host, community_configuration
):
    next(agent for agent in community_configuration["agents"] if agent["agentId"] == "b-entry")[
        "enabled"
    ] = False
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        assert (
            await coordinator.transfer("session-us3-fixture", "routing", context={})
        ).outcome == "committed"
        result = await coordinator.transfer("session-us3-fixture", "b-entry", context={})
        assert result.outcome == "failed" and len(models.models) == 2


@pytest.mark.anyio
async def test_disconnect_after_commit_does_not_rewrite_committed_outcome(handover_host):
    async with handover_host(budget=0.5) as (coordinator, repository, phone, models, telemetry):
        models.block_close_for = models.models[0]
        transfer = asyncio.create_task(
            coordinator.transfer("session-us3-fixture", "a-peer", context={})
        )
        await asyncio.wait_for(models.closing.wait(), 1)
        await coordinator.disconnect("session-us3-fixture")
        models.close_release.set()
        assert (await transfer).outcome == "committed"
        saved = await repository.read("session-us3-fixture")
        assert saved["state"] == "Ended" and saved["agentEpoch"] == 2
        assert [
            record["outcome"] for record in await repository.handovers("session-us3-fixture")
        ] == ["committed"]


@pytest.mark.anyio
async def test_transfer_deadline_also_bounds_failed_connection_cleanup(handover_host):
    async with handover_host(budget=0.1) as (coordinator, repository, phone, models, telemetry):
        models.next_behavior = "block"
        models.preparing.clear()
        started = time.monotonic()
        transfer = asyncio.create_task(
            coordinator.transfer("session-us3-fixture", "a-peer", context={})
        )
        await asyncio.wait_for(models.preparing.wait(), 1)
        models.block_close_for = models.models[1]
        try:
            result = await asyncio.wait_for(asyncio.shield(transfer), 0.5)
            assert result.reason_code == "deadline_exceeded"
            assert time.monotonic() - started < 0.3
            assert models.models[0].closed == 0
        finally:
            models.close_release.set()
            await transfer


@pytest.mark.anyio
@pytest.mark.parametrize(
    "ambiguous", [False, True], ids=["before-commit-outage", "commit-response-lost"]
)
async def test_uncertain_store_authority_closes_phone_and_terminates_when_store_recovers(
    handover_host, monkeypatch, ambiguous
):
    async with handover_host() as (coordinator, repository, phone, models, telemetry):
        commit = repository.commit_handover

        async def unavailable(session_id, metadata, record):
            if ambiguous and record.outcome == "committed":
                await commit(session_id, metadata, record)
            raise RuntimeError("synthetic-private-store-details")

        monkeypatch.setattr(repository, "commit_handover", unavailable)
        result = await coordinator.transfer("session-us3-fixture", "a-peer", context={})
        assert result.outcome == "failed" and "synthetic-private-store-details" not in str(result)
        assert phone.hangups == 1 and all(model.closed == 1 for model in models.models)
        assert (await repository.read("session-us3-fixture"))["state"] == "Ended"
        assert not await coordinator.emit("session-us3-fixture", 1, b"unsafe-source-output")
        assert not await coordinator.emit("session-us3-fixture", 2, b"unsafe-destination-output")
        records = await repository.handovers("session-us3-fixture")
        assert [record["outcome"] for record in records] == (["committed"] if ambiguous else [])


def test_committed_handover_window_counts_success_only_and_expires_at_sixty_seconds():
    from caller_runtime.services.routing import HandoverWindow

    now = [0.0]
    window = HandoverWindow(monotonic=lambda: now[0])
    for _ in range(10):
        assert window.allowed() is True
        window.committed()
    assert window.allowed() is False
    now[0] = 60.0
    assert window.allowed() is True


def test_handover_records_are_metadata_only_and_reject_transferred_text():
    from pydantic import ValidationError

    from caller_runtime.domain.handover import HandoverRecord

    now = datetime.now(UTC)
    record = HandoverRecord(
        handoverId="handover-fixture",
        sessionId="session-us3-fixture",
        configurationRevisionId="revision-us3-fixture",
        physicalOwnerId="physical-us3-owner",
        connectionEpoch=1,
        sourceAgentId="a-source",
        destinationAgentId="a-peer",
        sourceAgentEpoch=1,
        destinationAgentEpoch=2,
        requestedAt=now,
        completedAt=now,
        outcome="committed",
        reasonCode="completed",
    )
    assert record.to_document()["sessionId"] == "session-us3-fixture"
    assert "summary" not in json.dumps(record.to_document())
    with pytest.raises(ValidationError):
        HandoverRecord.model_validate(
            {
                **record.model_dump(by_alias=True),
                "request_summary": "Synthetic forbidden durable summary",
            }
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"sourceAgentEpoch": True},
        {"destinationAgentEpoch": 4},
        {"destinationAgentId": "a-source"},
        {"connectionEpoch": 0},
        {"requestedAt": "2026-01-01T00:00:00"},
        {"outcome": "requested", "reasonCode": "completed"},
        {"outcome": "failed", "reasonCode": "completed"},
        {"outcome": "committed", "reasonCode": "destination_unavailable"},
        {"outcome": "committed", "reasonCode": "completed", "completedAt": None},
        {"completedAt": "2025-01-01T00:00:00Z"},
    ],
    ids=[
        "boolean-epoch",
        "skipped-epoch",
        "same-agent",
        "zero-connection",
        "naive-time",
        "pending-completed",
        "failed-completed",
        "commit-failure",
        "missing-completion",
        "early-completion",
    ],
)
def test_handover_records_reject_inconsistent_metadata(changes):
    from pydantic import ValidationError

    from caller_runtime.domain.handover import HandoverRecord

    values = {
        "handoverId": "handover-fixture",
        "sessionId": "session-us3-fixture",
        "configurationRevisionId": "revision-us3-fixture",
        "physicalOwnerId": "physical-us3-owner",
        "connectionEpoch": 1,
        "sourceAgentId": "a-source",
        "destinationAgentId": "a-peer",
        "sourceAgentEpoch": 1,
        "destinationAgentEpoch": 2,
        "requestedAt": "2026-01-01T00:00:00Z",
    }
    with pytest.raises(ValidationError):
        HandoverRecord.model_validate(values | changes)


def test_handover_records_normalize_utc_and_are_immutable():
    from pydantic import ValidationError

    from caller_runtime.domain.handover import HandoverRecord

    record = HandoverRecord(
        handoverId="handover-fixture",
        sessionId="session-us3-fixture",
        configurationRevisionId="revision-us3-fixture",
        physicalOwnerId="physical-us3-owner",
        connectionEpoch=1,
        sourceAgentId="a-source",
        destinationAgentId="a-peer",
        sourceAgentEpoch=1,
        destinationAgentEpoch=2,
        requestedAt="2026-01-01T01:00:00+01:00",
    )
    assert record.requested_at.hour == 0 and record.requested_at.tzinfo == UTC
    with pytest.raises(ValidationError):
        record.outcome = "committed"
