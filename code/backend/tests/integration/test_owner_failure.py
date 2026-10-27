"""US6 owner fences; missing control services are intentional red contracts."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from caller_runtime.adapters.session_repository import LocalSessionRepository, SessionConflict
from caller_runtime.domain.session import CallSession

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def owner_metadata():
    now = datetime.now(UTC)
    return CallSession(
        sessionId="session-owner-fixture",
        providerCallRef="provider-owner-fixture",
        configurationRevisionId="revision-owner-fixture",
        physicalOwnerId="owner-one",
        connectionEpoch=1,
        agentEpoch=2,
        activeAgentId="agent-one",
        activeCommunityId="community-one",
        state="Active",
        startedAt=now,
        leaseUntil=now + timedelta(seconds=60),
    ).to_document()


async def test_duplicate_admissions_create_exactly_one_physical_owner(owner_metadata):
    repository = LocalSessionRepository()
    candidates = [
        owner_metadata | {"physicalOwnerId": "owner-" + str(index)} for index in range(32)
    ]
    outcomes = await asyncio.gather(*(repository.create(candidate) for candidate in candidates))
    assert outcomes.count(True) == 1
    stored = await repository.read(owner_metadata["sessionId"])
    assert stored["physicalOwnerId"] == candidates[outcomes.index(True)]["physicalOwnerId"]
    assert stored["connectionEpoch"] == 1 and stored["agentEpoch"] == 2
    assert stored["configurationRevisionId"] == owner_metadata["configurationRevisionId"]


async def test_expired_lease_is_not_permission_to_migrate_a_live_socket(owner_metadata):
    repository = LocalSessionRepository()
    owner_metadata["leaseUntil"] = owner_metadata["startedAt"]
    assert await repository.create(owner_metadata)
    stored = await repository.read(owner_metadata["sessionId"])
    assert not await repository.create(owner_metadata | {"physicalOwnerId": "replacement-owner"})
    with pytest.raises(SessionConflict):
        await repository.replace(
            owner_metadata["sessionId"],
            stored | {"physicalOwnerId": "replacement-owner", "connectionEpoch": 2},
        )
    assert await repository.read(owner_metadata["sessionId"]) == stored


async def test_obsolete_agent_epoch_cannot_rewrite_current_metadata(owner_metadata):
    repository = LocalSessionRepository()
    assert await repository.create(owner_metadata)
    current = await repository.read(owner_metadata["sessionId"])
    with pytest.raises(SessionConflict):
        await repository.replace(owner_metadata["sessionId"], current | {"agentEpoch": 1})
    assert await repository.read(owner_metadata["sessionId"]) == current


@pytest.fixture
def owned_connection():
    class Connection:
        def __init__(self):
            self.output = []
            self.explanations = []
            self.hangups = 0

        async def send_audio(self, payload):
            self.output.append(payload)

        async def explain_unavailable(self):
            self.explanations.append("owner_unavailable")

        async def hangup(self):
            self.hangups += 1
            return True

    return Connection()


async def owner_router(repository):
    from caller_runtime.services.owner_routing import OwnerRouting

    return OwnerRouting(repository=repository, operation_budget=0.1)


@pytest.mark.parametrize("failure", ["lease-lost", "processing-failed"])
async def test_lost_owner_becomes_terminal_within_original_budget(
    owner_metadata, owned_connection, failure
):
    repository = LocalSessionRepository()
    assert await repository.create(owner_metadata)
    router = await owner_router(repository)
    await router.attach(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=1,
        connection=owned_connection,
    )
    if failure == "lease-lost":
        current = await repository.read(owner_metadata["sessionId"])
        await repository.replace(
            owner_metadata["sessionId"], current | {"leaseUntil": owner_metadata["startedAt"]}
        )
    started = asyncio.get_running_loop().time()
    await router.owner_lost(
        owner_metadata["sessionId"], physical_owner_id="owner-one", connection_epoch=1
    )
    assert asyncio.get_running_loop().time() - started < 0.5
    stored = await repository.read(owner_metadata["sessionId"])
    assert stored["state"] == "Ended" and stored["terminalReasonCode"] == "owner_lost"
    assert stored["physicalOwnerId"] == "owner-one" and stored["connectionEpoch"] == 1
    assert owned_connection.explanations == ["owner_unavailable"] and owned_connection.hangups == 1
    await router.owner_lost(
        owner_metadata["sessionId"], physical_owner_id="owner-one", connection_epoch=1
    )
    assert owned_connection.hangups == 1


async def test_replacement_worker_cannot_attach_or_send_for_old_socket(
    owner_metadata, owned_connection
):
    repository = LocalSessionRepository()
    assert await repository.create(owner_metadata)
    router = await owner_router(repository)
    with pytest.raises(PermissionError):
        await router.attach(
            owner_metadata["sessionId"],
            physical_owner_id="replacement-owner",
            connection_epoch=2,
            connection=owned_connection,
        )
    assert not await router.send_audio(
        owner_metadata["sessionId"],
        physical_owner_id="replacement-owner",
        connection_epoch=2,
        agent_epoch=2,
        payload=b"synthetic-obsolete-audio",
    )
    assert owned_connection.output == []


async def test_current_owner_drops_old_agent_and_connection_epoch_output(
    owner_metadata, owned_connection
):
    repository = LocalSessionRepository()
    assert await repository.create(owner_metadata)
    router = await owner_router(repository)
    await router.attach(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=1,
        connection=owned_connection,
    )
    assert not await router.send_audio(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=1,
        agent_epoch=1,
        payload=b"synthetic-old-agent",
    )
    assert not await router.send_audio(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=2,
        agent_epoch=2,
        payload=b"synthetic-wrong-connection",
    )
    assert await router.send_audio(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=1,
        agent_epoch=2,
        payload=b"synthetic-current-audio",
    )
    assert owned_connection.output == [b"synthetic-current-audio"]


@pytest.mark.parametrize("blocked", ["explanation", "hangup"])
async def test_blocked_phone_cleanup_cannot_extend_owner_loss_deadline(owner_metadata, blocked):
    repository = LocalSessionRepository()
    assert await repository.create(owner_metadata)
    router = await owner_router(repository)

    class BlockedConnection:
        async def explain_unavailable(self):
            if blocked == "explanation":
                await asyncio.Event().wait()
            return None

        async def hangup(self):
            if blocked == "hangup":
                await asyncio.Event().wait()
            return True

    await router.attach(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=1,
        connection=BlockedConnection(),
    )
    started = asyncio.get_running_loop().time()
    result = await asyncio.wait_for(
        router.owner_lost(
            owner_metadata["sessionId"], physical_owner_id="owner-one", connection_epoch=1
        ),
        0.5,
    )
    assert asyncio.get_running_loop().time() - started < 0.5
    stored = await repository.read(owner_metadata["sessionId"])
    assert stored["state"] == "Ended" and stored["terminalReasonCode"] == "owner_lost"
    assert result.terminal_confirmed is True
    assert result.hangup_acknowledged is (blocked == "explanation")


async def test_expired_lease_fences_audio_and_callback_routing(owner_metadata, owned_connection):
    repository = LocalSessionRepository()
    assert await repository.create(owner_metadata)
    router = await owner_router(repository)
    await router.attach(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=1,
        connection=owned_connection,
    )
    current = await repository.read(owner_metadata["sessionId"])
    await repository.replace(
        owner_metadata["sessionId"], current | {"leaseUntil": owner_metadata["startedAt"]}
    )
    assert not await router.send_audio(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=1,
        agent_epoch=2,
        payload=b"synthetic-expired-owner-output",
    )
    with pytest.raises(PermissionError):
        await router.owner_for_callback(owner_metadata["sessionId"])
    assert owned_connection.output == []


async def test_owner_loss_reports_unknown_terminal_confirmation_without_unfencing_output(
    owner_metadata, owned_connection, monkeypatch
):
    repository = LocalSessionRepository()
    assert await repository.create(owner_metadata)
    router = await owner_router(repository)
    await router.attach(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=1,
        connection=owned_connection,
    )

    async def unavailable(session_id):
        raise RuntimeError("synthetic-private-store-diagnostic")

    monkeypatch.setattr(repository, "read", unavailable)
    result = await router.owner_lost(
        owner_metadata["sessionId"], physical_owner_id="owner-one", connection_epoch=1
    )
    assert result.terminal_confirmed is False and result.hangup_acknowledged is True
    assert not await router.send_audio(
        owner_metadata["sessionId"],
        physical_owner_id="owner-one",
        connection_epoch=1,
        agent_epoch=2,
        payload=b"synthetic-untrusted-output",
    )
    assert owned_connection.hangups == 1 and owned_connection.output == []
