"""US6 pre-drain contracts; no grace-period-only or live-socket migration claim."""

import asyncio

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def drain_case(capacity_case):
    from caller_runtime.services.drain import DrainService
    from caller_runtime.services.owner_routing import OwnerRouting

    capacity, repository, clock = capacity_case
    routing = OwnerRouting(
        repository=repository,
        operation_budget=0.1,
        monotonic=clock.monotonic,
        utc_now=clock.utc_now,
    )

    async def authorize(principal):
        if principal != "operations-fixture":
            raise PermissionError("drain_permission_required")
        return "operations-fixture"

    service = DrainService(capacity=capacity, owner_routing=routing, authorize=authorize)
    return service, capacity, routing, repository, clock


async def admitted(capacity, number=1):
    result = await capacity.admit(
        session_id=f"session-drain-{number}", provider_call_ref=f"provider-drain-{number}"
    )
    assert result.outcome == "admitted"
    return f"session-drain-{number}"


async def test_drain_postpones_termination_until_last_established_call_finishes(drain_case):
    drain, capacity, routing, repository, clock = drain_case
    await capacity.register_owner("owner-one")
    session_id = await admitted(capacity)
    before = await repository.read(session_id)
    assert await drain.state("owner-one") == "serving"
    assert await drain.begin("owner-one", principal="operations-fixture") == "draining"
    assert await drain.can_terminate("owner-one") is False
    assert await drain.finish("owner-one", principal="operations-fixture") == "postponed"
    for _interval in range(20):
        clock.advance(30)
        await capacity.heartbeat("owner-one")
    assert await drain.can_terminate("owner-one") is False
    assert (await repository.read(session_id))["state"] != "Ended"
    assert (await repository.read(session_id))["physicalOwnerId"] == before["physicalOwnerId"]
    await capacity.release(session_id)
    assert await drain.finish("owner-one", principal="operations-fixture") == "drained"
    assert await drain.can_terminate("owner-one") is True


async def test_no_new_assignments_after_drain_commit_even_with_competing_admissions(drain_case):
    drain, capacity, routing, repository, clock = drain_case
    await capacity.register_owner("owner-one")
    existing = await admitted(capacity)
    await drain.begin("owner-one", principal="operations-fixture")
    results = await asyncio.gather(
        *(
            capacity.admit(
                session_id=f"new-drain-{index}", provider_call_ref=f"provider-new-{index}"
            )
            for index in range(100)
        )
    )
    assert all(result.outcome == "unavailable" for result in results)
    assert await capacity.active_count() == 1
    assert (await repository.read(existing))["state"] != "Ended"
    for index in range(100):
        assert await repository.read(f"new-drain-{index}") is None


async def test_existing_media_and_callbacks_stay_at_draining_owner(drain_case):
    drain, capacity, routing, repository, clock = drain_case

    class Connection:
        hangups = 0

        async def hangup(self):
            self.hangups += 1
            return True

    await capacity.register_owner("owner-one")
    session_id = await admitted(capacity)
    stored = await repository.read(session_id)
    connection = Connection()
    await routing.attach(
        session_id,
        physical_owner_id=stored["physicalOwnerId"],
        connection_epoch=stored["connectionEpoch"],
        connection=connection,
    )
    await capacity.register_owner("owner-two")
    await drain.begin("owner-one", principal="operations-fixture")
    assert await routing.owner_for_callback(session_id) == "owner-one"
    assert routing.connection_for(session_id) is connection
    assert (
        await capacity.admit(session_id="session-drain-new", provider_call_ref="provider-drain-new")
    ).outcome == "admitted"
    assert (await repository.read("session-drain-new"))["physicalOwnerId"] == "owner-two"
    assert (await repository.read(session_id))["physicalOwnerId"] == "owner-one"
    assert connection.hangups == 0


@pytest.mark.parametrize("principal", [None, "anonymous", "runtime-fixture", "manager-fixture"])
async def test_drain_requires_explicit_operations_authority_before_state_change(
    drain_case, principal
):
    drain, capacity, routing, repository, clock = drain_case
    await capacity.register_owner("owner-one")
    with pytest.raises(PermissionError):
        await drain.begin("owner-one", principal=principal)
    assert await drain.state("owner-one") == "serving"
    assert (
        await capacity.admit(session_id="authorized-call", provider_call_ref="provider-authorized")
    ).outcome == "admitted"


async def test_repeated_drain_does_not_hang_up_or_duplicate_existing_call(drain_case):
    drain, capacity, routing, repository, clock = drain_case
    await capacity.register_owner("owner-one")
    session_id = await admitted(capacity)
    await drain.begin("owner-one", principal="operations-fixture")
    await drain.begin("owner-one", principal="operations-fixture")
    assert await capacity.active_count() == 1
    assert (await repository.read(session_id))["state"] != "Ended"
    assert await drain.can_terminate("owner-one") is False


async def test_terminal_metadata_without_acknowledged_connection_closure_still_blocks_retirement(
    drain_case,
):
    drain, capacity, routing, repository, clock = drain_case

    class Connection:
        async def hangup(self):
            return False

    await capacity.register_owner("owner-one")
    session_id = await admitted(capacity)
    await routing.attach(
        session_id, physical_owner_id="owner-one", connection_epoch=1, connection=Connection()
    )
    await drain.begin("owner-one", principal="operations-fixture")
    await capacity.release(session_id)
    assert await drain.finish("owner-one", principal="operations-fixture") == "postponed"
    assert await drain.can_terminate("owner-one") is False
    with pytest.raises(PermissionError):
        await routing.detach(
            session_id, physical_owner_id="owner-one", connection_epoch=1, hangup_acknowledged=False
        )
    await routing.detach(
        session_id, physical_owner_id="owner-one", connection_epoch=1, hangup_acknowledged=True
    )
    assert await drain.finish("owner-one", principal="operations-fixture") == "drained"
    assert await drain.can_terminate("owner-one") is True


async def test_rollout_orchestrator_uses_real_drain_api_and_requires_connection_ack(
    drain_case, monkeypatch
):
    import importlib
    from pathlib import Path

    import httpx
    from fastapi import FastAPI, HTTPException

    from caller_runtime.api.operations import create_router
    from caller_runtime.observability import Telemetry

    monkeypatch.syspath_prepend(str(Path(__file__).parents[3] / "tools/scripts"))
    rollout = importlib.import_module("drain_rollout")
    drain, capacity, routing, repository, clock = drain_case
    await capacity.register_owner("owner-one")
    session_id = await admitted(capacity)

    class Connection:
        hangups = 0

        async def hangup(self):
            self.hangups += 1
            return False

    class Identity:
        async def require_operator(self, request):
            if request.headers.get("X-Fixture-Principal") != "operations-fixture":
                raise HTTPException(403, "synthetic-private-auth-details")
            return "operations-fixture"

    class Cluster:
        deleted = []

        async def fence(self, request, *, deadline):
            return rollout.RemovalFence(request, "fence-fixture", True)

        async def retire(self, request, fence, snapshot, *, deadline):
            assert fence.request == request and snapshot["canTerminate"] is True
            assert await drain.can_terminate(request.target.owner_id)
            self.deleted.append(request.target.pod_uid)
            return "retired"

    connection, cluster = Connection(), Cluster()
    await routing.attach(
        session_id, physical_owner_id="owner-one", connection_epoch=1, connection=connection
    )
    telemetry = Telemetry("caller-runtime", "local")
    application = FastAPI()
    application.include_router(create_router(drain=drain, identity=Identity(), telemetry=telemetry))
    target = rollout.Target(
        "cluster-fixture", "runtime", "calls", "owner-one", "call-worker", "pod-fixture"
    )
    request = rollout.RemovalRequest("operation-fixture", "rollout", target)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
            headers={"X-Fixture-Principal": "operations-fixture"},
        ) as client:

            class Operations:
                async def status(self, owner_id, *, deadline):
                    response = await client.get(f"/v1/operations/drain/{owner_id}")
                    response.raise_for_status()
                    return response.json()

                async def begin(self, owner_id, *, deadline):
                    response = await client.post(f"/v1/operations/drain/{owner_id}")
                    response.raise_for_status()
                    return response.json()

                async def complete(self, owner_id, *, deadline):
                    response = await client.post(f"/v1/operations/drain/{owner_id}/complete")
                    response.raise_for_status()
                    return response.json()

            operations = Operations()
            assert (
                await rollout.orchestrate(request, operations=operations, cluster=cluster)
            ).outcome == "postponed"
            await capacity.release(session_id)
            assert (await repository.read(session_id))["state"] == "Ended"
            assert (
                await rollout.orchestrate(request, operations=operations, cluster=cluster)
            ).outcome == "postponed"
            assert not cluster.deleted and connection.hangups == 0
            await routing.detach(
                session_id,
                physical_owner_id="owner-one",
                connection_epoch=1,
                hangup_acknowledged=True,
            )
            assert (
                await rollout.orchestrate(request, operations=operations, cluster=cluster)
            ).outcome == "retired"
            assert cluster.deleted == [target.pod_uid] and connection.hangups == 0
    finally:
        telemetry.close()


def test_public_health_does_not_expose_drain_operations():
    from caller_runtime.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        assert client.post("/healthz").status_code == 405
        assert client.post("/v1/operations/drain").status_code == 404


@pytest.fixture
def operations_api(drain_case):
    from fastapi import FastAPI, HTTPException

    from caller_runtime.api.operations import create_router
    from caller_runtime.observability import Telemetry

    drain, capacity, routing, repository, clock = drain_case
    asyncio.run(capacity.register_owner("owner-one"))

    class Identity:
        async def require_operator(self, request):
            actor = request.headers.get("X-Fixture-Principal")
            if actor != "operations-fixture":
                raise HTTPException(403 if actor else 401, "synthetic-private-auth-diagnostic")
            return actor

    telemetry = Telemetry("caller-runtime", "local")
    app = FastAPI()
    app.include_router(create_router(drain=drain, identity=Identity(), telemetry=telemetry))
    with TestClient(app) as client:
        yield client, telemetry
    telemetry.close()


@pytest.mark.parametrize("principal", [None, "manager-fixture", "runtime-fixture"])
def test_operations_api_authenticates_before_any_state_change(operations_api, principal):
    client, telemetry = operations_api
    headers = {"X-Fixture-Principal": principal} if principal else {}
    response = client.post("/v1/operations/drain/owner-one", headers=headers)
    assert response.status_code == (403 if principal else 401)
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "synthetic-private-auth-diagnostic" not in response.text
    state = client.get(
        "/v1/operations/drain/owner-one", headers={"X-Fixture-Principal": "operations-fixture"}
    )
    assert state.json()["state"] == "serving" and state.json()["canTerminate"] is False


def test_explicit_operations_api_starts_checks_and_finishes_an_empty_owner(operations_api):
    client, telemetry = operations_api
    headers = {"X-Fixture-Principal": "operations-fixture"}
    first = client.post("/v1/operations/drain/owner-one", headers=headers)
    assert first.status_code == 200 and first.json()["state"] == "draining"
    complete = client.post("/v1/operations/drain/owner-one/complete", headers=headers)
    assert complete.status_code == 200 and complete.json()["state"] == "drained"
    assert complete.json()["canTerminate"] is True
    assert complete.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("kind", ["body", "query", "unknown-owner"])
def test_operations_api_rejects_unexpected_data_without_reflection(operations_api, kind):
    client, telemetry = operations_api
    path = "/v1/operations/drain/owner-one"
    options = {"headers": {"X-Fixture-Principal": "operations-fixture"}}
    if kind == "body":
        options["json"] = {"transcript": "synthetic-private-request-content"}
    elif kind == "query":
        path += "?override=synthetic-private-request-content"
    else:
        path = "/v1/operations/drain/synthetic-private-unknown-owner"
    response = client.post(path, **options)
    assert response.status_code == (404 if kind == "unknown-owner" else 422)
    assert "synthetic-private" not in response.text
