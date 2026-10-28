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
async def test_cosmos_capacity_survives_adapter_reconstruction_and_concurrent_writers(
    monkeypatch,
) -> None:
    from caller_runtime.adapters.capacity_repository import CosmosCapacityRepository

    container = ContainerDouble()
    now = datetime(2026, 10, 23, tzinfo=UTC)
    first = CosmosCapacityRepository(container, utc_now=lambda: now)
    await first.initialize_capacity_authority(
        maximum_sessions=1, sessions_per_owner=1, model_session_quota=1
    )
    await first.register_capacity_owner("owner-fixture", lease_seconds=30)
    second = CosmosCapacityRepository(container, utc_now=lambda: now)
    read_item = container.read_item
    released = asyncio.Event()
    snapshots = []

    async def competing_read(item, partition_key, **keywords):
        snapshot = await read_item(item, partition_key, **keywords)
        if len(snapshots) < 2:
            snapshots.append(snapshot)
            if len(snapshots) == 2:
                released.set()
            await released.wait()
        return snapshot

    monkeypatch.setattr(container, "read_item", competing_read)
    document = metadata()
    other = {**document, "id": "second-session", "sessionId": "second-session"}
    limits = {"maximum_sessions": 1, "sessions_per_owner": 1, "model_session_quota": 1}
    results = await asyncio.wait_for(
        asyncio.gather(
            first.reserve_capacity(document, **limits), second.reserve_capacity(other, **limits)
        ),
        timeout=2,
    )
    assert results.count("reserved") == results.count("unavailable") == 1
    reconstructed = CosmosCapacityRepository(container, utc_now=lambda: now)
    assert (
        await reconstructed.reserve_capacity(
            {**document, "id": "third-session", "sessionId": "third-session"}, **limits
        )
        == "unavailable"
    )
    writes = [call for call in container.calls if call[0] == "replace"]
    assert writes and all(
        call[2]["match_condition"] == MatchConditions.IfNotModified for call in writes
    )


@pytest.mark.anyio
async def test_cosmos_capacity_composes_admission_heartbeat_and_model_drain(
    capacity_case, community_configuration
) -> None:
    from caller_runtime.adapters.capacity_repository import CosmosCapacityRepository
    from caller_runtime.adapters.session_repository import metadata_model
    from caller_runtime.services.capacity import CapacityService

    _service, _local_repository, clock = capacity_case
    container = ContainerDouble()
    stores = [CosmosCapacityRepository(container, utc_now=clock.utc_now) for _ in range(2)]
    await stores[0].initialize_capacity_authority(
        maximum_sessions=1, sessions_per_owner=1, model_session_quota=2
    )
    services = [
        CapacityService(
            repository=store,
            configuration=community_configuration,
            maximum_sessions=1,
            sessions_per_owner=1,
            model_session_quota=2,
            utc_now=clock.utc_now,
            monotonic=clock.monotonic,
        )
        for store in stores
    ]
    for service in services:
        await service.register_owner("owner-fixture")
    result = await services[0].admit(
        session_id="composed-session", provider_call_ref="composed-provider"
    )
    assert result.outcome == "admitted"
    assert (
        await services[1].admit(
            session_id="composed-session", provider_call_ref="composed-provider"
        )
    ).outcome == "duplicate"
    token = await services[0].reserve_model_session("composed-session")
    assert token is not None
    assert await services[1].begin_drain("owner-fixture") == "draining"
    assert await services[1].finish_drain("owner-fixture") == "postponed"
    clock.advance(20)
    await services[0].heartbeat("owner-fixture")
    reconstructed = CosmosCapacityRepository(container, utc_now=clock.utc_now)
    session = await reconstructed.read("composed-session")
    assert session is not None
    assert metadata_model(session).lease_until == clock.utc_now() + timedelta(seconds=60)
    await services[0].release("composed-session")
    assert await services[1].finish_drain("owner-fixture") == "postponed"
    await services[0].release_model_session(token)
    assert await services[1].finish_drain("owner-fixture") == "drained"
    assert (
        await reconstructed.register_capacity_owner("owner-fixture", lease_seconds=60) == "drained"
    )
    assert (
        await services[1].admit(
            session_id="post-drain-session", provider_call_ref="post-drain-provider"
        )
    ).outcome == "unavailable"


@pytest.fixture
async def cosmos_capacity(capacity_case):
    from caller_runtime.adapters.capacity_repository import CosmosCapacityRepository

    _service, _repository, clock = capacity_case
    container = ContainerDouble()
    store = CosmosCapacityRepository(container, utc_now=clock.utc_now)
    limits = {"maximum_sessions": 2, "sessions_per_owner": 2, "model_session_quota": 3}
    assert await store.initialize_capacity_authority(**limits)
    await store.register_capacity_owner("owner-fixture", lease_seconds=30)
    document = {
        **metadata(),
        "startedAt": clock.utc_now().isoformat(),
        "leaseUntil": (clock.utc_now() + timedelta(seconds=30)).isoformat(),
    }
    return store, container, clock, document, limits


async def reserve_stored_capacity(store, document, limits):
    assert await store.reserve_capacity(document, **limits) == "reserved"
    assert await store.create(document)


def recovery_service(store, clock, configuration, limits):
    from caller_runtime.services.capacity import CapacityService

    return CapacityService(
        repository=store,
        configuration=configuration,
        **limits,
        lease_seconds=30,
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("recovery_kind", ["call", "model"])
async def test_reconstructed_capacity_service_reconciles_verified_owned_cleanup(
    cosmos_capacity, community_configuration, recovery_kind
) -> None:
    from caller_runtime.adapters.capacity_repository import CosmosCapacityRepository
    from caller_runtime.adapters.session_repository import metadata_model
    from caller_runtime.domain.session import LifecycleState

    store, container, clock, document, limits = cosmos_capacity
    session_id = document["sessionId"]
    token = "a" * 32
    await reserve_stored_capacity(store, document, limits)
    if recovery_kind == "model":
        assert await store.reserve_model_capacity(
            session_id, physical_owner_id="owner-fixture", reservation=token
        )
    await store.begin_capacity_drain("owner-fixture")
    clock.advance(31)
    current = metadata_model(await store.read(session_id))
    terminal = current.transition(
        LifecycleState.ENDED, at=clock.utc_now(), terminal_reason="completed"
    )
    await store.replace(session_id, terminal.model_dump(by_alias=True))
    if recovery_kind == "model":
        await store.release_capacity(session_id, physical_owner_id="owner-fixture")
    reconstructed = recovery_service(
        CosmosCapacityRepository(container, utc_now=clock.utc_now),
        clock,
        community_configuration,
        limits,
    )
    await reconstructed.register_owner("owner-fixture")
    assert await reconstructed.active_count() == 0
    assert await reconstructed.finish_drain("owner-fixture") == "postponed"
    if recovery_kind == "call":
        await reconstructed.release(session_id)
        assert await reconstructed.finish_drain("owner-fixture") == "postponed"
        await reconstructed.reconcile_closed_session(
            session_id, physical_owner_id="owner-fixture", connection_epoch=1
        )
    else:
        await reconstructed.release_model_session(token)
        assert await reconstructed.finish_drain("owner-fixture") == "postponed"
        await reconstructed.reconcile_disposed_model(
            token, session_id=session_id, physical_owner_id="owner-fixture", connection_epoch=1
        )
    assert await reconstructed.finish_drain("owner-fixture") == "drained"


async def reconcile_cleanup(
    service, kind, session_id, *, owner="owner-fixture", epoch=1, token="a" * 32
):
    if kind == "call":
        await service.reconcile_closed_session(
            session_id, physical_owner_id=owner, connection_epoch=epoch
        )
    else:
        await service.reconcile_disposed_model(
            token, session_id=session_id, physical_owner_id=owner, connection_epoch=epoch
        )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["call", "model"])
@pytest.mark.parametrize(
    "violation",
    [
        "unregistered",
        "foreign_owner",
        "wrong_epoch",
        "boolean_epoch",
        "missing_session",
        "missing_authority",
        "unavailable_storage",
        "conflicting_cache",
    ],
)
async def test_capacity_recovery_refuses_unbound_or_unavailable_cleanup(
    cosmos_capacity, community_configuration, kind, violation
) -> None:
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID
    from caller_runtime.adapters.session_repository import metadata_model
    from caller_runtime.domain.session import LifecycleState

    store, container, clock, document, limits = cosmos_capacity
    session_id = document["sessionId"]
    await reserve_stored_capacity(store, document, limits)
    if kind == "model":
        assert await store.reserve_model_capacity(
            session_id, physical_owner_id="owner-fixture", reservation="a" * 32
        )
    else:
        current = metadata_model(await store.read(session_id))
        await store.replace(
            session_id,
            current.transition(
                LifecycleState.ENDED, at=clock.utc_now(), terminal_reason="completed"
            ).model_dump(by_alias=True),
        )
    service = recovery_service(store, clock, community_configuration, limits)
    if violation != "unregistered":
        await service.register_owner("owner-fixture")
    owner, epoch = "owner-fixture", 1
    if violation == "foreign_owner":
        await service.register_owner("foreign-owner")
        owner = "foreign-owner"
    elif violation == "wrong_epoch":
        epoch = 2
    elif violation == "boolean_epoch":
        epoch = True
    elif violation == "missing_session":
        session_id = "unknown-session"
    elif violation == "missing_authority":
        del container.records[AUTHORITY_ID]
    elif violation == "unavailable_storage":
        container.failure = 503
    elif violation == "conflicting_cache":
        if kind == "call":
            service._active[session_id] = "foreign-owner"
        else:
            service._extra_models["a" * 32] = (session_id, "foreign-owner")
    original = copy.deepcopy(container.records)
    active, models = dict(service._active), dict(service._extra_models)
    with pytest.raises((PermissionError, ValueError, RuntimeError)):
        await reconcile_cleanup(service, kind, session_id, owner=owner, epoch=epoch)
    assert container.records == original
    assert service._active == active and service._extra_models == models


@pytest.mark.anyio
async def test_capacity_recovery_does_not_infer_physical_closure_from_live_metadata(
    cosmos_capacity, community_configuration
) -> None:
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID

    store, container, clock, document, limits = cosmos_capacity
    await reserve_stored_capacity(store, document, limits)
    service = recovery_service(store, clock, community_configuration, limits)
    await service.register_owner("owner-fixture")
    clock.advance(31)
    original = copy.deepcopy(container.records[AUTHORITY_ID])
    with pytest.raises(PermissionError, match="capacity_closure_not_confirmed"):
        await reconcile_cleanup(service, "call", document["sessionId"])
    assert container.records[AUTHORITY_ID] == original


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["call", "model"])
async def test_capacity_recovery_lost_release_reply_preserves_cache_until_idempotent_confirmation(
    cosmos_capacity, community_configuration, monkeypatch, kind
) -> None:
    from caller_runtime.adapters.session_repository import metadata_model
    from caller_runtime.domain.session import LifecycleState

    store, _container, clock, document, limits = cosmos_capacity
    service = recovery_service(store, clock, community_configuration, limits)
    await service.register_owner("owner-fixture")
    session_id = document["sessionId"]
    assert (
        await service.admit(session_id=session_id, provider_call_ref="recovery-provider")
    ).outcome == "admitted"
    token = "a" * 32
    if kind == "model":
        token = await service.reserve_model_session(session_id)
        assert token is not None
    else:
        current = metadata_model(await store.read(session_id))
        await store.replace(
            session_id,
            current.transition(
                LifecycleState.ENDED, at=clock.utc_now(), terminal_reason="completed"
            ).model_dump(by_alias=True),
        )
    name = "release_capacity" if kind == "call" else "release_model_capacity"
    release = getattr(store, name)

    async def lost_reply(*arguments, **keywords):
        await release(*arguments, **keywords)
        raise RuntimeError("recovery_reply_unknown")

    active, models = dict(service._active), dict(service._extra_models)
    monkeypatch.setattr(store, name, lost_reply)
    with pytest.raises(RuntimeError, match="recovery_reply_unknown"):
        await reconcile_cleanup(service, kind, session_id, token=token)
    assert service._active == active and service._extra_models == models
    monkeypatch.setattr(store, name, release)
    await reconcile_cleanup(service, kind, session_id, token=token)
    await reconcile_cleanup(service, kind, session_id, token=token)
    assert (
        (session_id not in service._active)
        if kind == "call"
        else (token not in service._extra_models)
    )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["call", "model"])
async def test_capacity_recovery_cancellation_retains_all_charges(
    cosmos_capacity, community_configuration, monkeypatch, kind
) -> None:
    store, container, clock, document, limits = cosmos_capacity
    await reserve_stored_capacity(store, document, limits)
    service = recovery_service(store, clock, community_configuration, limits)
    await service.register_owner("owner-fixture")
    original = copy.deepcopy(container.records)
    started, never = asyncio.Event(), asyncio.Event()

    async def blocked_read(session_id):
        started.set()
        await never.wait()

    monkeypatch.setattr(store, "read", blocked_read)
    task = asyncio.create_task(reconcile_cleanup(service, kind, document["sessionId"]))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert container.records == original


@pytest.mark.anyio
@pytest.mark.parametrize("token", ["invalid-token", None, True])
async def test_capacity_recovery_rejects_malformed_model_tokens(
    cosmos_capacity, community_configuration, token
) -> None:
    store, container, clock, document, limits = cosmos_capacity
    await reserve_stored_capacity(store, document, limits)
    service = recovery_service(store, clock, community_configuration, limits)
    await service.register_owner("owner-fixture")
    original = copy.deepcopy(container.records)
    with pytest.raises(ValueError, match="invalid_model_reservation"):
        await reconcile_cleanup(service, "model", document["sessionId"], token=token)
    assert container.records == original


@pytest.mark.anyio
async def test_capacity_recovery_cannot_release_another_sessions_model_token(
    cosmos_capacity, community_configuration
) -> None:
    from caller_runtime.adapters.session_repository import SessionConflict

    store, container, clock, document, limits = cosmos_capacity
    sibling = {**document, "id": "sibling-session", "sessionId": "sibling-session"}
    await reserve_stored_capacity(store, document, limits)
    await reserve_stored_capacity(store, sibling, limits)
    assert await store.reserve_model_capacity(
        sibling["sessionId"], physical_owner_id="owner-fixture", reservation="a" * 32
    )
    service = recovery_service(store, clock, community_configuration, limits)
    await service.register_owner("owner-fixture")
    original = copy.deepcopy(container.records)
    with pytest.raises(SessionConflict, match="model_capacity_not_owned"):
        await reconcile_cleanup(service, "model", document["sessionId"])
    assert container.records == original


@pytest.mark.anyio
async def test_cosmos_capacity_model_quota_is_shared_and_reconstruction_cannot_reset_it(
    cosmos_capacity,
) -> None:
    from caller_runtime.adapters.capacity_repository import CosmosCapacityRepository

    store, container, clock, document, limits = cosmos_capacity
    other = {**document, "id": "second-session", "sessionId": "second-session"}
    await reserve_stored_capacity(store, document, limits)
    await reserve_stored_capacity(store, other, limits)
    remote = CosmosCapacityRepository(container, utc_now=clock.utc_now)
    tokens = ["a" * 32, "b" * 32]
    results = await asyncio.gather(
        *(
            adapter.reserve_model_capacity(
                session["sessionId"], physical_owner_id="owner-fixture", reservation=token
            )
            for adapter, session, token in zip(
                (store, remote), (document, other), tokens, strict=True
            )
        )
    )
    assert results.count(True) == results.count(False) == 1
    await remote.set_model_capacity_quota(0)
    reconstructed = CosmosCapacityRepository(container, utc_now=clock.utc_now)
    assert await reconstructed.initialize_capacity_authority(**limits) is False
    winner = results.index(True)
    assert await reconstructed.reserve_model_capacity(
        (document, other)[winner]["sessionId"],
        physical_owner_id="owner-fixture",
        reservation=tokens[winner],
    )
    assert (
        await reconstructed.reserve_model_capacity(
            document["sessionId"], physical_owner_id="owner-fixture", reservation="c" * 32
        )
        is False
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["call", "model", "drain"])
@pytest.mark.parametrize("lost_reply", ["unavailable", "canceled"])
async def test_cosmos_capacity_ambiguous_commits_keep_their_charge_or_fence(
    cosmos_capacity, monkeypatch, mutation, lost_reply
) -> None:
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID, CosmosCapacityRepository

    store, container, clock, document, limits = cosmos_capacity
    if mutation != "call":
        await reserve_stored_capacity(store, document, limits)
    replace_item = container.replace_item

    async def committed_without_reply(*args, **keywords):
        await replace_item(*args, **keywords)
        if lost_reply == "canceled":
            raise asyncio.CancelledError
        raise CosmosHttpResponseError(message="sensitive-store-details", status_code=503)

    monkeypatch.setattr(container, "replace_item", committed_without_reply)
    operation = (
        store.reserve_capacity(document, **limits)
        if mutation == "call"
        else store.reserve_model_capacity(
            document["sessionId"], physical_owner_id="owner-fixture", reservation="a" * 32
        )
        if mutation == "model"
        else store.begin_capacity_drain("owner-fixture")
    )
    expected_error = asyncio.CancelledError if lost_reply == "canceled" else RuntimeError
    with pytest.raises(expected_error) as failure:
        await operation
    assert "sensitive-store-details" not in str(failure.value)
    monkeypatch.setattr(container, "replace_item", replace_item)
    reconstructed = CosmosCapacityRepository(container, utc_now=clock.utc_now)
    if mutation == "call":
        assert await reconstructed.reserve_capacity(document, **limits) == "duplicate"
        assert len(container.records[AUTHORITY_ID]["calls"]) == 1
    elif mutation == "model":
        assert await reconstructed.reserve_model_capacity(
            document["sessionId"], physical_owner_id="owner-fixture", reservation="a" * 32
        )
        assert len(container.records[AUTHORITY_ID]["models"]) == 1
    else:
        assert await reconstructed.capacity_owner_state("owner-fixture") == "draining"
        assert await reconstructed.finish_capacity_drain("owner-fixture") == "postponed"
        other = {**document, "id": "second-session", "sessionId": "second-session"}
        assert await reconstructed.reserve_capacity(other, **limits) == "unavailable"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure_mode",
    [
        "missing",
        "unavailable",
        "etag",
        "identity",
        "missing_calls",
        "schema",
        "content",
        "ttl",
        "model_binding",
        "owner_phase",
        "owner_phase_missing",
    ],
)
async def test_cosmos_capacity_authority_loss_or_malformed_state_never_falls_back(
    cosmos_capacity, failure_mode
) -> None:
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID, CosmosCapacityRepository

    _store, container, clock, document, limits = cosmos_capacity
    authority = container.records[AUTHORITY_ID]
    if failure_mode == "missing":
        del container.records[AUTHORITY_ID]
    elif failure_mode == "unavailable":
        container.failure = 503
    elif failure_mode == "etag":
        del authority["_etag"]
    elif failure_mode == "identity":
        del authority["id"]
    elif failure_mode == "missing_calls":
        del authority["calls"]
    elif failure_mode == "schema":
        authority["schema_version"] = True
    elif failure_mode == "content":
        authority["transcript"] = "synthetic-content-must-be-rejected"
    elif failure_mode == "ttl":
        authority["ttl"] = 30
    elif failure_mode == "model_binding":
        authority["models"]["a" * 32] = {
            "session_id": "unknown-session",
            "owner_id": "unknown-owner",
            "connection_epoch": 1,
        }
    elif failure_mode == "owner_phase_missing":
        del authority["owners"]["owner-fixture"]["state"]
    else:
        authority["owners"]["owner-fixture"]["state"] = "unknown"
    creates = sum(call[0] == "create" for call in container.calls)
    reconstructed = CosmosCapacityRepository(container, utc_now=clock.utc_now)
    with pytest.raises(RuntimeError, match="capacity_store_(unavailable|response_invalid)"):
        await reconstructed.reserve_capacity(document, **limits)
    assert sum(call[0] == "create" for call in container.calls) == creates


@pytest.mark.anyio
async def test_cosmos_capacity_expiry_never_renews_or_reclaims_uncertain_calls(cosmos_capacity):
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID, CosmosCapacityRepository

    store, container, clock, document, limits = cosmos_capacity
    await reserve_stored_capacity(store, document, limits)
    deadline = container.records[AUTHORITY_ID]["owners"]["owner-fixture"]["lease_until"]
    clock.advance(31)
    reconstructed = CosmosCapacityRepository(container, utc_now=clock.utc_now)
    assert (
        await reconstructed.register_capacity_owner("owner-fixture", lease_seconds=30) == "serving"
    )
    assert container.records[AUTHORITY_ID]["owners"]["owner-fixture"]["lease_until"] == deadline
    with pytest.raises(PermissionError, match="owner_lease_lost"):
        await reconstructed.renew_capacity_owner("owner-fixture", lease_seconds=30)
    assert await reconstructed.serving_capacity_owners(("owner-fixture",)) == ()
    with pytest.raises(Exception, match="model_capacity_not_owned"):
        await reconstructed.reserve_model_capacity(
            document["sessionId"], physical_owner_id="owner-fixture", reservation="a" * 32
        )
    assert len(container.records[AUTHORITY_ID]["calls"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_session", ["missing", "mismatched"])
async def test_cosmos_capacity_heartbeat_validates_all_calls_before_atomic_renewal(
    cosmos_capacity, invalid_session
) -> None:
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID
    from caller_runtime.adapters.session_repository import SessionConflict

    store, container, clock, document, limits = cosmos_capacity
    other = {**document, "id": "second-session", "sessionId": "second-session"}
    await reserve_stored_capacity(store, document, limits)
    await reserve_stored_capacity(store, other, limits)
    original = copy.deepcopy(container.records[AUTHORITY_ID])
    if invalid_session == "missing":
        del container.records["second-session"]
    else:
        container.records["second-session"]["physicalOwnerId"] = "foreign-owner"
    clock.advance(10)
    with pytest.raises((RuntimeError, SessionConflict)):
        await store.renew_capacity_owner("owner-fixture", lease_seconds=30)
    assert container.records[AUTHORITY_ID] == original


@pytest.mark.anyio
async def test_cosmos_capacity_model_token_cannot_cross_session_or_owner(cosmos_capacity):
    from caller_runtime.adapters.session_repository import SessionConflict

    store, _container, _clock, document, limits = cosmos_capacity
    other = {**document, "id": "second-session", "sessionId": "second-session"}
    await reserve_stored_capacity(store, document, limits)
    await reserve_stored_capacity(store, other, limits)
    assert await store.reserve_model_capacity(
        document["sessionId"], physical_owner_id="owner-fixture", reservation="a" * 32
    )
    with pytest.raises(SessionConflict, match="model_capacity_not_owned"):
        await store.reserve_model_capacity(
            other["sessionId"], physical_owner_id="owner-fixture", reservation="a" * 32
        )
    for session_id, owner_id in (
        (other["sessionId"], "owner-fixture"),
        (document["sessionId"], "foreign-owner"),
    ):
        with pytest.raises(SessionConflict, match="model_capacity_not_owned"):
            await store.release_model_capacity(
                "a" * 32, session_id=session_id, physical_owner_id=owner_id
            )
    await store.release_model_capacity(
        "a" * 32, session_id=document["sessionId"], physical_owner_id="owner-fixture"
    )
    await store.release_model_capacity(
        "a" * 32, session_id=document["sessionId"], physical_owner_id="owner-fixture"
    )


@pytest.mark.anyio
async def test_cosmos_capacity_metadata_cannot_extend_the_authoritative_lease(cosmos_capacity):
    from caller_runtime.adapters.session_repository import CosmosSessionRepository, metadata_model

    store, container, clock, document, limits = cosmos_capacity
    await reserve_stored_capacity(store, document, limits)
    raw = CosmosSessionRepository(container)
    current = await raw.read(document["sessionId"])
    await raw.replace(
        document["sessionId"],
        {
            **current,
            "leaseUntil": (clock.utc_now() + timedelta(days=1)).isoformat(),
        },
    )
    bounded = await store.read(document["sessionId"])
    assert metadata_model(bounded).lease_until == clock.utc_now() + timedelta(seconds=30)


@pytest.mark.anyio
@pytest.mark.parametrize("clock_error", ["naive", "non_utc", "backward"])
async def test_cosmos_capacity_rejects_invalid_or_persistently_backward_clock(
    cosmos_capacity, clock_error
) -> None:
    from datetime import timezone

    from caller_runtime.adapters.capacity_repository import CosmosCapacityRepository

    _store, container, clock, _document, _limits = cosmos_capacity
    instant = clock.utc_now()
    invalid = {
        "naive": instant.replace(tzinfo=None),
        "non_utc": instant.astimezone(timezone(timedelta(hours=1))),
        "backward": instant - timedelta(seconds=1),
    }[clock_error]
    store = CosmosCapacityRepository(container, utc_now=lambda: invalid)
    with pytest.raises(RuntimeError, match="capacity_clock_unavailable"):
        await store.serving_capacity_owners(("owner-fixture",))


@pytest.mark.anyio
async def test_cosmos_capacity_authority_has_no_ttl_and_all_reads_require_strong(cosmos_capacity):
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID

    store, container, _clock, document, limits = cosmos_capacity
    await reserve_stored_capacity(store, document, limits)
    assert container.records[AUTHORITY_ID]["ttl"] == -1
    reads = [call for call in container.calls if call[0] == "read" and call[1] == AUTHORITY_ID]
    assert reads and all(call[2]["consistency_level"] == "Strong" for call in reads)
    writes = [call for call in container.calls if call[0] == "replace" and call[1] == AUTHORITY_ID]
    assert writes and all(call[2]["retry_write"] == 0 for call in writes)


@pytest.mark.anyio
@pytest.mark.parametrize("closure", ["unconfirmed", "foreign_owner", "verified"])
async def test_cosmos_capacity_only_verified_owned_closure_releases_reservation(
    cosmos_capacity, closure
) -> None:
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID, CosmosCapacityRepository
    from caller_runtime.adapters.session_repository import SessionConflict, metadata_model
    from caller_runtime.domain.session import LifecycleState

    store, container, clock, document, limits = cosmos_capacity
    await reserve_stored_capacity(store, document, limits)
    reconstructed = CosmosCapacityRepository(container, utc_now=clock.utc_now)
    if closure != "unconfirmed":
        current = metadata_model(await reconstructed.read(document["sessionId"]))
        terminal = current.transition(
            LifecycleState.ENDED, at=clock.utc_now(), terminal_reason="completed"
        )
        await reconstructed.replace(document["sessionId"], terminal.model_dump(by_alias=True))
        assert await reconstructed.reserve_capacity(document, **limits) == "duplicate"
    owner = "foreign-owner" if closure == "foreign_owner" else "owner-fixture"
    if closure == "verified":
        await reconstructed.release_capacity(document["sessionId"], physical_owner_id=owner)
        await reconstructed.release_capacity(document["sessionId"], physical_owner_id=owner)
        assert not container.records[AUTHORITY_ID]["calls"]
    else:
        with pytest.raises(SessionConflict, match="capacity_closure_not_confirmed"):
            await reconstructed.release_capacity(document["sessionId"], physical_owner_id=owner)
        assert len(container.records[AUTHORITY_ID]["calls"]) == 1


@pytest.mark.anyio
async def test_cosmos_capacity_conflict_retries_are_bounded_without_local_fallback(cosmos_capacity):
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID

    store, container, _clock, document, limits = cosmos_capacity
    original = copy.deepcopy(container.records[AUTHORITY_ID])
    container.calls.clear()
    container.conflict_on_replace = True
    with pytest.raises(RuntimeError, match="capacity_store_unavailable"):
        await store.reserve_capacity(document, **limits)
    writes = [call for call in container.calls if call[0] == "replace"]
    assert len(writes) == 4
    assert container.records[AUTHORITY_ID] == original


@pytest.mark.anyio
async def test_cosmos_capacity_cancellation_during_read_commits_nothing(
    cosmos_capacity, monkeypatch
):
    from caller_runtime.adapters.capacity_repository import AUTHORITY_ID

    store, container, _clock, document, limits = cosmos_capacity
    original = copy.deepcopy(container.records[AUTHORITY_ID])
    started, never = asyncio.Event(), asyncio.Event()

    async def blocked_read(*args, **keywords):
        started.set()
        await never.wait()

    monkeypatch.setattr(container, "read_item", blocked_read)
    task = asyncio.create_task(store.reserve_capacity(document, **limits))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert container.records[AUTHORITY_ID] == original


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
