"""US6 profile/gate contracts; virtual scheduling never proves live speech latency."""

import asyncio
import math

import pytest

pytestmark = pytest.mark.anyio

US6_SYNTHETIC_PROFILE = {
    "profileId": "us6-synthetic-v1",
    "defaultEnabled": True,
    "clock": "virtual",
    "initialCalls": 100,
    "initialSustainSeconds": 1800,
    "targetCalls": 200,
    "scaleDeadlineSeconds": 300,
    "targetSustainSeconds": 1800,
    "evidenceClass": "synthetic_control_contracts_not_live_latency",
}
US6_LIVE_PROFILE = {
    "profileId": "us6-live-v1",
    "status": "defined_not_executed",
    "defaultEnabled": False,
    "explicitOptIn": "us6-live",
    "requiredTasks": ["T099", "T112", "T117", "T118"],
    "environmentEvidence": "code/docs/acceptance/live-environment.md",
    "clock": "wall",
    "initialCalls": 100,
    "initialSustainSeconds": 1800,
    "targetCalls": 200,
    "scaleDeadlineSeconds": 300,
    "targetSustainSeconds": 1800,
    "minimumEligibleAdmissionRatio": 0.99,
    "minimumGreetingRatioWithinThreeSeconds": 0.95,
    "maximumSpeechP95SecondsExcludingExternalActionWait": 2.0,
    "maximumHandoverP95Seconds": 2.0,
    "existingCallDisconnectsDuringScaleOut": 0,
    "automaticProvisioning": False,
    "automaticCleanup": False,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def assert_service_levels(report):
    assert type(report["eligibleAttempts"]) is int and report["eligibleAttempts"] >= 100
    assert type(report["admitted"]) is int and 0 <= report["admitted"] <= report["eligibleAttempts"]
    assert report["admitted"] / report["eligibleAttempts"] >= 0.99
    greetings = report["greetingSeconds"]
    assert len(greetings) == report["admitted"]
    for samples in (
        greetings,
        report["speechSecondsExcludingActionWait"],
        report["handoverSeconds"],
    ):
        assert len(samples) >= report["admitted"] > 0
        assert all(
            type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in samples
        )
    assert sum(value <= 3 for value in greetings) / len(greetings) >= 0.95
    for samples in (report["speechSecondsExcludingActionWait"], report["handoverSeconds"]):
        assert sorted(samples)[math.ceil(0.95 * len(samples)) - 1] <= 2
    assert type(report["existingCallDisconnectsDuringScaleOut"]) is int
    assert report["existingCallDisconnectsDuringScaleOut"] == 0


def sample_report():
    return {
        "eligibleAttempts": 100,
        "admitted": 100,
        "greetingSeconds": [1.0] * 100,
        "speechSecondsExcludingActionWait": [0.5] * 100,
        "handoverSeconds": [0.5] * 100,
        "existingCallDisconnectsDuringScaleOut": 0,
    }


def test_live_profile_is_explicit_separate_and_not_authorized_by_synthetic_profile():
    assert US6_SYNTHETIC_PROFILE["clock"] == "virtual"
    assert US6_LIVE_PROFILE["clock"] == "wall" and US6_LIVE_PROFILE["defaultEnabled"] is False
    assert US6_LIVE_PROFILE["status"] == "defined_not_executed"
    assert US6_LIVE_PROFILE["requiredTasks"] == ["T099", "T112", "T117", "T118"]
    assert (
        US6_LIVE_PROFILE["automaticProvisioning"] is US6_LIVE_PROFILE["automaticCleanup"] is False
    )
    for field in (
        "initialCalls",
        "initialSustainSeconds",
        "targetCalls",
        "scaleDeadlineSeconds",
        "targetSustainSeconds",
    ):
        assert US6_SYNTHETIC_PROFILE[field] == US6_LIVE_PROFILE[field]


def test_measurement_gate_accepts_exact_requirements_boundary():
    report = sample_report()
    report.update(
        admitted=99,
        greetingSeconds=[3.0] * 95 + [3.01] * 4,
        speechSecondsExcludingActionWait=[2.0] * 95 + [2.01] * 5,
        handoverSeconds=[2.0] * 95 + [2.01] * 5,
    )
    assert_service_levels(report)


@pytest.mark.parametrize("value", [False, 0.0, "0"])
def test_measurement_gate_rejects_non_integer_disconnect_counts(value):
    report = sample_report()
    report["existingCallDisconnectsDuringScaleOut"] = value
    with pytest.raises(AssertionError):
        assert_service_levels(report)


@pytest.mark.parametrize(
    "violation", ["admission", "greeting", "speech", "handover", "dropped", "empty", "non-finite"]
)
def test_measurement_gate_rejects_failed_or_missing_evidence(violation):
    report = sample_report()
    if violation == "admission":
        report["admitted"] = 98
    elif violation == "greeting":
        report["greetingSeconds"][:6] = [3.01] * 6
    elif violation == "speech":
        report["speechSecondsExcludingActionWait"][:6] = [2.01] * 6
    elif violation == "handover":
        report["handoverSeconds"][:6] = [2.01] * 6
    elif violation == "dropped":
        report["existingCallDisconnectsDuringScaleOut"] = 1
    elif violation == "empty":
        report["handoverSeconds"] = []
    else:
        report["speechSecondsExcludingActionWait"][0] = math.nan
    with pytest.raises(AssertionError):
        assert_service_levels(report)


async def admit(service, index):
    return await service.admit(
        session_id=f"session-capacity-{index}", provider_call_ref=f"provider-capacity-{index}"
    )


async def test_virtual_100_to_200_schedule_preserves_all_existing_owners(capacity_case):
    service, repository, clock = capacity_case
    await service.register_owner("owner-one")
    first = await asyncio.gather(*(admit(service, index) for index in range(100)))
    assert all(result.outcome == "admitted" for result in first)
    original = {
        f"session-capacity-{index}": await repository.read(f"session-capacity-{index}")
        for index in range(100)
    }
    for _interval in range(60):
        clock.advance(30)
        await service.heartbeat("owner-one")
        assert await service.active_count() == 100
    assert clock.seconds == US6_SYNTHETIC_PROFILE["initialSustainSeconds"]
    scale_started = clock.monotonic()
    await service.register_owner("owner-two")
    second = await asyncio.gather(*(admit(service, index) for index in range(100, 200)))
    assert all(result.outcome == "admitted" for result in second)
    assert clock.monotonic() - scale_started <= US6_SYNTHETIC_PROFILE["scaleDeadlineSeconds"]
    for _interval in range(60):
        clock.advance(30)
        await service.heartbeat("owner-one")
        await service.heartbeat("owner-two")
        assert await service.active_count() == 200
    for session_id, before in original.items():
        current = await repository.read(session_id)
        assert current["state"] != "Ended"
        assert current["physicalOwnerId"] == before["physicalOwnerId"]
        assert current["connectionEpoch"] == before["connectionEpoch"]
        assert current["configurationRevisionId"] == before["configurationRevisionId"]
    assert clock.seconds == 3600


async def test_over_capacity_is_bounded_unavailable_not_a_waiting_queue(capacity_case):
    service, repository, clock = capacity_case
    await service.register_owner("owner-one")
    assert all(
        result.outcome == "admitted"
        for result in await asyncio.gather(*(admit(service, index) for index in range(100)))
    )
    started = asyncio.get_running_loop().time()
    result = await asyncio.wait_for(admit(service, 101), timeout=1)
    assert result.outcome == "unavailable" and asyncio.get_running_loop().time() - started < 1
    assert await repository.read("session-capacity-101") is None
    assert await service.active_count() == 100


async def test_quota_and_duplicate_admission_are_atomic(capacity_case):
    service, repository, clock = capacity_case
    await service.register_owner("owner-one")
    outcomes = await asyncio.gather(*(admit(service, 1) for _index in range(32)))
    assert sum(result.outcome == "admitted" for result in outcomes) == 1
    assert await service.active_count() == 1
    await service.set_model_session_quota(1)
    assert (await admit(service, 2)).outcome == "unavailable"
    assert await repository.read("session-capacity-2") is None


@pytest.mark.parametrize("failure", ["read", "create-response-lost"])
async def test_unknown_store_state_denies_admission_without_freeing_uncertain_reservation(
    capacity_case, monkeypatch, failure
):
    service, repository, clock = capacity_case
    await service.register_owner("owner-one")
    await service.set_model_session_quota(1)
    create, read = repository.create, repository.read

    async def unavailable_read(session_id):
        raise RuntimeError("synthetic-private-store-diagnostic")

    async def ambiguous_create(document):
        await create(document)
        raise RuntimeError("synthetic-private-store-diagnostic")

    monkeypatch.setattr(
        repository,
        "read" if failure == "read" else "create",
        unavailable_read if failure == "read" else ambiguous_create,
    )
    result = await admit(service, 1)
    assert result.outcome == "unavailable" and "synthetic-private-store-diagnostic" not in str(
        result
    )
    assert await service.active_count() == (0 if failure == "read" else 1)
    if failure == "create-response-lost":
        assert (await admit(service, 2)).outcome == "unavailable"
        assert await read("session-capacity-2") is None


async def test_pending_admissions_are_bounded_before_the_repository_lock(
    capacity_case, monkeypatch
):
    service, repository, clock = capacity_case
    await service.register_owner("owner-one")
    entered, release = asyncio.Event(), asyncio.Event()
    read = repository.read

    async def blocked_read(session_id):
        entered.set()
        await release.wait()
        return await read(session_id)

    monkeypatch.setattr(repository, "read", blocked_read)
    tasks = [asyncio.create_task(admit(service, index)) for index in range(256)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(admit(service, 999), 0.5)
        assert result.outcome == "unavailable"
    finally:
        release.set()
        await asyncio.gather(*tasks)


async def test_transient_handover_model_sessions_share_the_current_quota(capacity_case):
    service, repository, clock = capacity_case
    await service.register_owner("owner-one")
    assert (await admit(service, 1)).outcome == "admitted"
    await service.set_model_session_quota(2)
    reservation = await service.reserve_model_session("session-capacity-1")
    assert reservation is not None
    assert await service.reserve_model_session("session-capacity-1") is None
    assert (await admit(service, 2)).outcome == "unavailable"
    await service.release_model_session(reservation)
    await service.release_model_session(reservation)
    assert (await admit(service, 2)).outcome == "admitted"
    with pytest.raises(PermissionError):
        await service.reserve_model_session("guessed-session")


async def test_owner_heartbeat_cannot_revive_an_expired_session_lease(capacity_case):
    service, repository, clock = capacity_case
    await service.register_owner("owner-one")
    assert (await admit(service, 1)).outcome == "admitted"
    current = await repository.read("session-capacity-1")
    await repository.replace("session-capacity-1", current | {"leaseUntil": current["startedAt"]})
    expired = await repository.read("session-capacity-1")
    with pytest.raises(PermissionError, match="session_lease_lost"):
        await service.heartbeat("owner-one")
    assert await repository.read("session-capacity-1") == expired
