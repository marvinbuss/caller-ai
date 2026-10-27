"""Conditional metadata repository contracts with deterministic local/Cosmos doubles."""

import asyncio
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError

from caller_runtime.domain.session import CallSession


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def metadata() -> dict:
    started = datetime(2026, 10, 23, tzinfo=UTC)
    return CallSession.model_validate(
        {
            "sessionId": "session-repository-fixture",
            "providerCallRef": "provider-repository-fixture",
            "configurationRevisionId": "revision-repository-fixture",
            "physicalOwnerId": "owner-fixture",
            "connectionEpoch": 1,
            "agentEpoch": 0,
            "state": "Admitted",
            "startedAt": started,
            "leaseUntil": started + timedelta(seconds=30),
        }
    ).to_document()


@dataclass
class ContainerDouble:
    records: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    version: int = 0
    failure: int | None = None
    conflict_on_replace: bool = False

    def fail_if_requested(self) -> None:
        if self.failure:
            raise CosmosHttpResponseError(
                message="sensitive-store-details", status_code=self.failure
            )

    async def create_item(self, body, **keywords):
        self.calls.append(("create", copy.deepcopy(body), keywords))
        self.fail_if_requested()
        if body["id"] in self.records:
            raise CosmosHttpResponseError(status_code=409)
        self.version += 1
        self.records[body["id"]] = {**copy.deepcopy(body), "_etag": f'"cosmos-{self.version}"'}
        return copy.deepcopy(self.records[body["id"]])

    async def read_item(self, item, partition_key, **keywords):
        self.calls.append(("read", item, {"partition_key": partition_key, **keywords}))
        self.fail_if_requested()
        assert item == partition_key
        if item not in self.records:
            raise CosmosHttpResponseError(status_code=404)
        return copy.deepcopy(self.records[item])

    async def replace_item(self, item, body, *, etag, match_condition, **keywords):
        self.calls.append(
            (
                "replace",
                item,
                {
                    "etag": etag,
                    "match_condition": match_condition,
                    "body": copy.deepcopy(body),
                    **keywords,
                },
            )
        )
        self.fail_if_requested()
        assert match_condition == MatchConditions.IfNotModified
        assert "_etag" not in body
        if self.conflict_on_replace or self.records[item]["_etag"] != etag:
            raise CosmosHttpResponseError(status_code=412)
        self.version += 1
        self.records[item] = {**copy.deepcopy(body), "_etag": f'"cosmos-{self.version}"'}
        return copy.deepcopy(self.records[item])

    async def execute_item_batch(self, *, batch_operations, partition_key, **keywords):
        from azure.cosmos.exceptions import CosmosBatchOperationError

        self.calls.append(
            ("batch", copy.deepcopy(batch_operations), {"partition_key": partition_key, **keywords})
        )
        self.fail_if_requested()
        assert keywords == {"retry_write": 0}
        assert len(batch_operations) == 2
        replacement, creation = batch_operations
        assert replacement[0] == "replace" and creation[0] == "create"
        item, body = replacement[1]
        outcome = creation[1][0]
        assert item == partition_key == body["sessionId"] == outcome["sessionId"]
        if (
            self.conflict_on_replace
            or self.records[item]["_etag"] != replacement[2]["if_match_etag"]
        ):
            raise CosmosBatchOperationError(status_code=412, error_index=0, headers={})
        if outcome["id"] in self.records:
            raise CosmosBatchOperationError(status_code=409, error_index=1, headers={})
        self.version += 1
        self.records[item] = {**copy.deepcopy(body), "_etag": f'"cosmos-{self.version}"'}
        self.records[outcome["id"]] = copy.deepcopy(outcome)
        return [{"statusCode": 200, "eTag": self.records[item]["_etag"]}, {"statusCode": 201}]


@pytest.fixture(params=["local", "cosmos"])
def repository(request):
    from caller_runtime.adapters.session_repository import (
        CosmosSessionRepository,
        LocalSessionRepository,
    )

    container = ContainerDouble()
    instance = (
        LocalSessionRepository() if request.param == "local" else CosmosSessionRepository(container)
    )
    return instance, container


@pytest.mark.anyio
async def test_conditional_creation_and_detached_reads(repository) -> None:
    store, container = repository
    document = metadata()
    results = await asyncio.gather(store.create(document), store.create(document))
    assert results.count(True) == 1
    snapshot = await store.read(document["sessionId"])
    assert snapshot["_etag"]
    snapshot["physicalOwnerId"] = "tampered-owner"
    assert (await store.read(document["sessionId"]))["physicalOwnerId"] == "owner-fixture"


@pytest.mark.anyio
async def test_unknown_session_is_not_created_by_read(repository) -> None:
    store, container = repository
    assert await store.read("unknown-session") is None


@pytest.mark.anyio
async def test_stale_or_missing_etag_cannot_overwrite(repository) -> None:
    from caller_runtime.adapters.session_repository import SessionConflict

    store, container = repository
    document = metadata()
    assert await store.create(document)
    first = await store.read(document["sessionId"])
    updated = {**first, "leaseUntil": "2026-10-23T00:01:00Z"}
    await store.replace(document["sessionId"], updated)
    saved = await store.read(document["sessionId"])
    assert saved["_etag"] != first["_etag"]
    with pytest.raises(SessionConflict):
        await store.replace(document["sessionId"], first)
    with pytest.raises(SessionConflict):
        await store.replace(document["sessionId"], document)
    assert await store.read(document["sessionId"]) == saved


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes", [{"physicalOwnerId": "competing-owner"}, {"connectionEpoch": 2}]
)
async def test_physical_owner_and_connection_epoch_are_fenced(repository, changes) -> None:
    from caller_runtime.adapters.session_repository import SessionConflict

    store, container = repository
    document = metadata()
    assert await store.create(document)
    current = await store.read(document["sessionId"])
    with pytest.raises(SessionConflict):
        await store.replace(document["sessionId"], {**current, **changes})
    assert await store.read(document["sessionId"]) == current


@pytest.mark.anyio
async def test_terminal_record_is_immutable(repository) -> None:
    from caller_runtime.adapters.session_repository import SessionConflict

    store, container = repository
    document = metadata()
    assert await store.create(document)
    current = await store.read(document["sessionId"])
    ended = {
        **current,
        "state": "Ended",
        "terminatedAt": "2026-10-23T00:00:01Z",
        "terminalReasonCode": "caller_disconnected",
    }
    await store.replace(document["sessionId"], ended)
    saved = await store.read(document["sessionId"])
    with pytest.raises(SessionConflict):
        await store.replace(document["sessionId"], saved)
    assert await store.read(document["sessionId"]) == saved


@pytest.mark.anyio
async def test_content_and_cross_session_identity_are_rejected_before_persistence(
    repository,
) -> None:
    store, container = repository
    document = metadata()
    with pytest.raises(ValueError):
        await store.create({**document, "transcript": "synthetic-content-not-for-persistence"})
    with pytest.raises(ValueError):
        await store.create({**document, "id": "other-session"})
    assert await store.read(document["sessionId"]) is None


@pytest.mark.anyio
async def test_cosmos_reads_are_partition_qualified_and_writes_are_conditional() -> None:
    from caller_runtime.adapters.session_repository import CosmosSessionRepository

    container = ContainerDouble()
    store = CosmosSessionRepository(container)
    document = metadata()
    assert await store.create(document)
    current = await store.read(document["sessionId"])
    await store.replace(document["sessionId"], {**current, "leaseUntil": "2026-10-23T00:01:00Z"})
    assert all(
        call[2]["partition_key"] == document["sessionId"]
        for call in container.calls
        if call[0] == "read"
    )
    writes = [call for call in container.calls if call[0] == "replace"]
    assert writes and writes[0][2]["etag"] == current["_etag"]
    assert writes[0][2]["match_condition"] == MatchConditions.IfNotModified


@pytest.mark.anyio
async def test_cosmos_failure_is_sanitized_without_local_fallback() -> None:
    from caller_runtime.adapters.session_repository import CosmosSessionRepository

    store = CosmosSessionRepository(ContainerDouble(failure=503))
    with pytest.raises(RuntimeError, match="session_store_unavailable") as failure:
        await store.create(metadata())
    assert "sensitive-store-details" not in str(failure.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"providerCallRef": "different-provider-call"},
        {"configurationRevisionId": "different-revision"},
        {"state": "Active"},
    ],
)
async def test_identity_revision_and_lifecycle_cannot_be_silently_replaced(
    repository, changes
) -> None:
    from caller_runtime.adapters.session_repository import SessionConflict

    store, container = repository
    document = metadata()
    assert await store.create(document)
    current = await store.read(document["sessionId"])
    with pytest.raises(SessionConflict):
        await store.replace(document["sessionId"], {**current, **changes})
    assert await store.read(document["sessionId"]) == current


@pytest.mark.anyio
async def test_cosmos_conflict_after_read_does_not_fall_back_or_overwrite() -> None:
    from caller_runtime.adapters.session_repository import CosmosSessionRepository, SessionConflict

    container = ContainerDouble()
    store = CosmosSessionRepository(container)
    document = metadata()
    assert await store.create(document)
    current = await store.read(document["sessionId"])
    container.conflict_on_replace = True
    with pytest.raises(SessionConflict):
        await store.replace(
            document["sessionId"], {**current, "leaseUntil": "2026-10-23T00:01:00Z"}
        )
    assert await store.read(document["sessionId"]) == current


async def prepared_handover(store):
    from caller_runtime.domain.handover import HandoverRecord

    initial = metadata() | {
        "state": "Active",
        "activeAgentId": "a-source",
        "activeCommunityId": "alpha",
        "agentEpoch": 1,
    }
    assert await store.create(initial)
    source = await store.read(initial["sessionId"])
    record = HandoverRecord(
        handoverId="handover-repository-fixture",
        sessionId=source["sessionId"],
        configurationRevisionId=source["configurationRevisionId"],
        physicalOwnerId=source["physicalOwnerId"],
        connectionEpoch=1,
        sourceAgentId="a-source",
        destinationAgentId="routing",
        sourceAgentEpoch=1,
        destinationAgentEpoch=2,
        requestedAt="2026-10-23T00:00:00Z",
        completedAt="2026-10-23T00:00:01Z",
        outcome="committed",
        reasonCode="completed",
    )
    candidate = source | {
        "activeAgentId": "routing",
        "activeCommunityId": None,
        "agentEpoch": 2,
        "state": "Routing",
    }
    return source, candidate, record


@pytest.mark.anyio
async def test_handover_owner_and_outcome_commit_together(repository):
    store, container = repository
    source, candidate, record = await prepared_handover(store)
    saved = await store.commit_handover(source["sessionId"], candidate, record)
    assert saved["agentEpoch"] == 2 and saved["activeAgentId"] == "routing"
    assert saved["_etag"] != source["_etag"]
    if container.records:
        assert container.records[record.to_document()["id"]] == record.to_document()
        batch = [call for call in container.calls if call[0] == "batch"][-1]
        assert batch[2]["partition_key"] == source["sessionId"]
    else:
        assert (await store.handovers(source["sessionId"])) == [record.to_document()]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [{"agentEpoch": 3}, {"physicalOwnerId": "foreign-owner"}, {"activeAgentId": "guessed-agent"}],
)
async def test_handover_mismatch_cannot_partially_commit(repository, change):
    from caller_runtime.adapters.session_repository import SessionConflict

    store, container = repository
    source, candidate, record = await prepared_handover(store)
    with pytest.raises(SessionConflict):
        await store.commit_handover(source["sessionId"], candidate | change, record)
    assert await store.read(source["sessionId"]) == source
    assert record.to_document()["id"] not in container.records


@pytest.mark.anyio
async def test_handover_stale_retry_cannot_duplicate_outcome(repository):
    from caller_runtime.adapters.session_repository import SessionConflict

    store, container = repository
    source, candidate, record = await prepared_handover(store)
    saved = await store.commit_handover(source["sessionId"], candidate, record)
    with pytest.raises(SessionConflict):
        await store.commit_handover(source["sessionId"], candidate, record)
    assert await store.read(source["sessionId"]) == saved


@pytest.mark.anyio
async def test_cosmos_atomic_failure_does_not_change_owner_or_write_outcome():
    from caller_runtime.adapters.session_repository import CosmosSessionRepository, SessionConflict

    container = ContainerDouble()
    store = CosmosSessionRepository(container)
    source, candidate, record = await prepared_handover(store)
    container.conflict_on_replace = True
    with pytest.raises(SessionConflict):
        await store.commit_handover(source["sessionId"], candidate, record)
    assert await store.read(source["sessionId"]) == source
    assert record.to_document()["id"] not in container.records
