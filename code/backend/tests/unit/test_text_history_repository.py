"""Contracts of the owner-keyed Cosmos store for text conversation history."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from azure.core import MatchConditions

from caller_runtime.adapters.text_history_repository import (
    ClosedConversation,
    TextHistoryRepository,
)
from caller_runtime.domain.shared_history import HistoryEntry
from caller_runtime.domain.text_history import (
    MAX_HISTORY_BYTES,
    TextConversation,
    new_conversation_id,
)
from tests.doubles import ContainerDouble

TTL = 2592000
OWNER = "a" * 64
OTHER = "b" * 64
PRIVATE = "synthetic-private-conversation-text"
NOW = datetime(2026, 11, 3, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


pytestmark = pytest.mark.anyio


def entries(text: str = PRIVATE) -> list[HistoryEntry]:
    return [
        HistoryEntry(kind="user_text", content=text),
        HistoryEntry(
            kind="tool_call",
            content="{}",
            name="public_lookup",
            call_id="call_1",
            agent_id="agent-billing",
        ),
        HistoryEntry(
            kind="tool_result",
            content='{"status":"shipped"}',
            name="public_lookup",
            call_id="call_1",
            agent_id="agent-billing",
        ),
        HistoryEntry(kind="agent_answer", content="ok", agent_id="agent-billing"),
    ]


def conversation(owner: str = OWNER, text: str = PRIVATE, identifier: str | None = None):
    return TextConversation(
        id=identifier or new_conversation_id(),
        ownerKey=owner,
        historyVersion=2,
        entries=entries(text),
        activeAgentId="agent-welcome",
        activeCommunityId=None,
        pendingDestination=None,
        createdAt=NOW,
        updatedAt=NOW,
    )


@pytest.fixture
def container():
    return ContainerDouble(partition_field="ownerKey")


@pytest.fixture
def repository(container):
    return TextHistoryRepository(container=container, ttl_seconds=TTL, utc_now=lambda: NOW)


def deadline() -> float:
    return asyncio.get_running_loop().time() + 1


async def test_item_is_keyed_by_owner_and_carries_a_thirty_day_expiry(repository, container):
    item = conversation()

    created = await repository.create(item, deadline=deadline())
    loaded = await repository.load(OWNER, item.id, deadline=deadline())

    assert loaded is not None and loaded.conversation == item
    assert created.etag == loaded.etag
    body = container.records[item.id]
    assert body["ttl"] == TTL == 30 * 24 * 3600
    assert body["ownerKey"] == OWNER and body["id"] == item.id
    read = [call for call in container.calls if call[0] == "read"][0]
    assert read[2]["partition_key"] == OWNER


async def test_every_update_is_etag_conditioned_and_refreshes_the_expiry(repository, container):
    item = conversation()
    created = await repository.create(item, deadline=deadline())
    later = item.model_copy(update={"updated_at": NOW + timedelta(days=3)})

    replaced = await repository.replace(created, later, deadline=deadline())

    call = [call for call in container.calls if call[0] == "replace"][0]
    assert call[2]["etag"] == created.etag
    assert call[2]["match_condition"] == MatchConditions.IfNotModified
    assert call[2]["body"]["ttl"] == TTL
    assert replaced.etag != created.etag
    assert replaced.conversation.updated_at == NOW + timedelta(days=3)


async def test_stale_etag_and_duplicate_create_report_a_busy_conversation(repository):
    item = conversation()
    created = await repository.create(item, deadline=deadline())
    await repository.replace(created, item, deadline=deadline())

    with pytest.raises(PermissionError, match="work_busy"):
        await repository.replace(created, item, deadline=deadline())
    with pytest.raises(PermissionError, match="work_busy"):
        await repository.create(item, deadline=deadline())


async def test_foreign_unknown_and_expired_conversations_are_indistinguishable(
    repository, container
):
    item = conversation()
    await repository.create(item, deadline=deadline())

    foreign = await repository.load(OTHER, item.id, deadline=deadline())
    unknown = await repository.load(OWNER, new_conversation_id(), deadline=deadline())
    del container.records[item.id]
    expired = await repository.load(OWNER, item.id, deadline=deadline())

    assert foreign is None and unknown is None and expired is None


async def test_stored_size_is_bounded_by_the_context_limit():
    big = "x" * (60 * 1024)
    stored = [HistoryEntry(kind="user_text", content=big) for _ in range(5)]

    with pytest.raises(ValueError, match="conversation_context_limit"):
        TextConversation(
            id=new_conversation_id(),
            ownerKey=OWNER,
            historyVersion=2,
            entries=stored,
            activeAgentId="agent-welcome",
            activeCommunityId=None,
            createdAt=NOW,
            updatedAt=NOW,
        )
    assert MAX_HISTORY_BYTES == 256 * 1024


async def test_version_2_stores_all_entries_and_the_pending_destination(repository, container):
    item = conversation().model_copy(update={"pending_destination": "community-billing"})
    await repository.create(item, deadline=deadline())
    body = container.records[item.id]
    assert body["historyVersion"] == 2 and "turns" not in body
    assert [entry["kind"] for entry in body["entries"]] == [
        "user_text",
        "tool_call",
        "tool_result",
        "agent_answer",
    ]
    assert body["entries"][2]["callId"] == "call_1"
    assert body["pendingDestination"] == "community-billing"
    loaded = await repository.load(OWNER, item.id, deadline=deadline())
    assert not isinstance(loaded, ClosedConversation) and loaded is not None
    assert loaded.conversation == item


async def test_a_history_holds_at_most_2048_entries():
    too_many = [HistoryEntry(kind="user_text", content="x") for _ in range(2049)]
    with pytest.raises(ValueError):
        TextConversation(
            id=new_conversation_id(),
            ownerKey=OWNER,
            historyVersion=2,
            entries=too_many,
            activeAgentId="agent-welcome",
            activeCommunityId=None,
            createdAt=NOW,
            updatedAt=NOW,
        )


async def test_a_document_without_history_version_loads_as_closed(repository, container):
    identifier = new_conversation_id()
    container.records[identifier] = {
        "id": identifier,
        "ownerKey": OWNER,
        "turns": [{"role": "user", "content": PRIVATE}],
        "activeAgentId": "agent-welcome",
        "activeCommunityId": None,
        "createdAt": NOW.isoformat(),
        "updatedAt": NOW.isoformat(),
        "ttl": TTL,
        "_etag": '"cosmos-old"',
    }
    loaded = await repository.load(OWNER, identifier, deadline=deadline())
    # Closed at the upgrade (FR-025): never resumed, and left to its expiry.
    assert isinstance(loaded, ClosedConversation) and loaded.conversation_id == identifier
    assert PRIVATE not in repr(loaded)
    assert identifier in container.records
    assert await repository.load(OTHER, identifier, deadline=deadline()) is None


async def test_a_document_above_512_kib_is_refused(repository, container):
    identifier = new_conversation_id()
    container.records[identifier] = conversation(identifier=identifier).model_dump(
        mode="json", by_alias=True
    ) | {"_etag": '"cosmos-big"', "padding": "x" * (512 * 1024)}
    with pytest.raises(PermissionError, match="history_unavailable"):
        await repository.load(OWNER, identifier, deadline=deadline())


@pytest.mark.parametrize("operation", ["load", "create", "replace"])
async def test_store_failure_is_a_fixed_content_free_error(repository, container, operation):
    item = conversation()
    created = await repository.create(item, deadline=deadline())
    container.failure = 503
    calls = {
        "load": lambda: repository.load(OWNER, item.id, deadline=deadline()),
        "create": lambda: repository.create(conversation(), deadline=deadline()),
        "replace": lambda: repository.replace(created, item, deadline=deadline()),
    }

    with pytest.raises(PermissionError) as failure:
        await calls[operation]()

    assert str(failure.value) == "history_unavailable"
    assert failure.value.__cause__ is None and failure.value.__suppress_context__
    for private in (PRIVATE, "sensitive-store-details"):
        assert private not in repr(failure.value) + str(failure.value.args)


async def test_probe_reports_reachability_and_failure(repository, container):
    assert await repository.probe(deadline=deadline()) is True
    container.failure = 503
    assert await repository.probe(deadline=deadline()) is False


async def test_invalid_deadline_or_missing_store_is_rejected():
    with pytest.raises(ValueError, match="explicit_text_history_store_required"):
        TextHistoryRepository(container=None, ttl_seconds=TTL, utc_now=lambda: NOW)
    with pytest.raises(ValueError, match="explicit_text_history_store_required"):
        TextHistoryRepository(container=ContainerDouble(), ttl_seconds=0, utc_now=lambda: NOW)
    repository = TextHistoryRepository(
        container=ContainerDouble(partition_field="ownerKey"), ttl_seconds=TTL, utc_now=lambda: NOW
    )
    with pytest.raises(PermissionError, match="history_unavailable"):
        await repository.load(OWNER, new_conversation_id(), deadline=float("inf"))
