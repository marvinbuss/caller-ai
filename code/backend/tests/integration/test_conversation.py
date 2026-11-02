"""Owned application WebSocket boundaries; controlled providers are not live proof."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.unit.test_conversation import text_execution_case as text_execution_case
from tests.unit.test_test_admission import admission_case as admission_case
from tests.unit.test_test_admission import authority_store as authority_store
from tests.unit.test_test_admission import live_case as live_case
from tests.unit.test_test_admission import signed_test_permit as signed_test_permit

SOCKET_URL = "wss://runtime.fixture.example/v1/internal/test-sessions"


def run_owned_browser_runtime(fixture_path):
    import asyncio
    import base64
    import json
    import ssl
    import time
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    from pathlib import Path
    from uuid import uuid4

    import httpx
    import httpx2
    import uvicorn

    from caller_runtime.adapters.test_admission_repository import TestAdmissionRepository
    from caller_runtime.composition import TestRuntimeComposition, TestRuntimeOptions
    from caller_runtime.main import create_app
    from tests.unit.test_session_repository import ContainerDouble

    path = Path(fixture_path)
    fixture = json.loads(path.read_text())
    if fixture["mode"] != "local" or not fixture.get("runtime_port"):
        raise ValueError("explicit_owned_browser_runtime_required")
    trust = ssl.create_default_context(cafile=str(path.parent / "certificate.pem"))
    counters = {"modelRequests": 0, "liveConnections": 0, "inputFrames": 0}
    faults = {"unknown_prepare": False}
    container = ContainerDouble()

    class Credential:
        async def get_token(self, *scopes):
            return SimpleNamespace(token="synthetic-browser-workload", expires_on=time.time() + 900)

        async def close(self):
            pass

    class Identity:
        async def require_runtime(self, request):
            if request.headers.get("authorization") != "Bearer synthetic-browser-workload":
                raise HTTPException(401, "fixture_workload_required")
            return "owned-management-browser-workload"

    class Configuration(httpx.AsyncBaseTransport):
        initial = True

        async def handle_async_request(self, request):
            assert str(request.url) == "https://management.fixture.example/v2/runtime/configuration"
            if self.initial:
                self.initial = False
                return httpx.Response(
                    200,
                    json=json.loads((path.parent / "runtime-initial.json").read_text()),
                    headers={"etag": '"fixture-initial"'},
                )
            async with httpx.AsyncClient(verify=trust, trust_env=False, timeout=1) as client:
                response = await client.get(
                    fixture["origin"] + "/v2/runtime/configuration", headers=dict(request.headers)
                )
                return httpx.Response(
                    response.status_code, content=response.content, headers=response.headers
                )

    class Text(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            counters["modelRequests"] += 1
            if faults["unknown_prepare"]:
                return httpx2.Response(503, json={"error": {"message": "Synthetic unknown result"}})
            body = json.loads(request.content)
            assert body["store"] is False and not body.get("parallel_tool_calls")
            names = {tool["function"]["name"] for tool in body.get("tools", [])}
            action = (
                ("enter_routing", {})
                if "enter_routing" in names
                else (
                    ("route_community", {"community_id": "community-alpha"})
                    if "route_community" in names
                    else None
                )
            )
            if not body.get("stream"):
                return httpx2.Response(
                    200,
                    json={
                        "id": "fixture-ready",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": "reasoning-fixture",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "Synthetic ready"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
                    },
                )

            class Chunks(httpx2.AsyncByteStream):
                async def __aiter__(self):
                    if action is None:
                        deltas = [
                            ({"role": "assistant", "content": "Synthetic shared reply"}, None),
                            ({}, "stop"),
                        ]
                    else:
                        name, arguments = action
                        call = {
                            "index": 0,
                            "id": "fixture-" + uuid4().hex,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                        deltas = [
                            ({"role": "assistant", "tool_calls": [call]}, None),
                            ({}, "tool_calls"),
                        ]
                    for delta, finish in deltas:
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "id": "fixture-text",
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": "reasoning-fixture",
                                    "choices": [
                                        {"index": 0, "delta": delta, "finish_reason": finish}
                                    ],
                                }
                            )
                            + "\n\n"
                        ).encode()
                    yield b"data: [DONE]\n\n"

            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Chunks()
            )

    class Live:
        def __init__(self):
            self.events = asyncio.Queue()
            self.offset = 0

        async def send(self, raw):
            event = json.loads(raw)
            kind = event["type"]
            if kind == "session.start":
                await self.events.put(
                    json.dumps(
                        {
                            "type": "session.started",
                            "session": {"id": "fixture-live-" + uuid4().hex, **event["session"]},
                        }
                    )
                )
            elif kind == "session.input_audio.append":
                counters["inputFrames"] += 1
                if self.offset == 0:
                    await self.events.put(
                        json.dumps(
                            {
                                "type": "session.output_audio.delta",
                                "delta": base64.b64encode(b"\x05\x00" * 480).decode(),
                                "start_ms": 0,
                                "end_ms": 20,
                            }
                        )
                    )
                self.offset += 20
            elif kind in {"session.input_audio.mute", "session.input_audio.unmute"}:
                await self.events.put(json.dumps({"type": kind + "d"}))
            elif kind == "session.close":
                await self.events.put(
                    json.dumps(
                        {
                            "type": "session.closed",
                            "reason": "close_requested",
                            "usage": {"seconds": self.offset / 1000},
                        }
                    )
                )
            else:
                raise AssertionError("unexpected_fixture_live_event")

        async def recv(self):
            return await self.events.get()

        async def close(self):
            pass

    async def live_socket(url, **options):
        counters["liveConnections"] += 1
        return Live()

    async def existing_container():
        return container

    async def credential():
        return Credential()

    runtime = TestRuntimeComposition(
        options=TestRuntimeOptions(
            review_reference="owned-browser-fixture",
            management_endpoint="https://management.fixture.example",
            management_audience="11111111-1111-4111-8111-111111111111",
            text_endpoint="https://fixture.openai.azure.com",
            text_deployment="reasoning-fixture",
            live_endpoint="https://fixture.openai.azure.com",
            approved_hosts=frozenset({"management.fixture.example", "fixture.openai.azure.com"}),
            manager_issuer=fixture["origin"],
            manager_audience="caller-browser-test-fixture",
            live_region="eastus2",
            live_model_version="2026-09-10",
            available_live_sessions=5,
            text_features=frozenset(
                {"streaming", "function_tools", "storage_disabled", "structured_output"}
            ),
            region_reviewed=True,
            lifecycle_reviewed=True,
            retention_reviewed=True,
            identity_reviewed=True,
            private_store_reviewed=True,
            volatile_retention_seconds=300,
            max_input_tokens=16384,
            max_output_tokens=512,
            voice="marin",
            max_clock_skew_seconds=0.1,
        ),
        owner_id="owned-browser-runtime",
        container_factory=existing_container,
        credential_factory=credential,
        workload_identity=Identity(),
        verification_keys={"fixture-test-key": (path.parent / "test-public.pem").read_bytes()},
        utc_now=lambda: datetime.now(UTC),
        monotonic=time.monotonic,
        configuration_transport=Configuration(),
        text_transport=Text(),
        live_socket_factory=live_socket,
    )
    application = create_app(test_runtime=runtime)
    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        await TestAdmissionRepository(container=container).initialize(
            now=datetime.now(UTC), deadline=asyncio.get_running_loop().time() + 1
        )
        async with original_lifespan(app):
            yield

    application.router.lifespan_context = lifespan

    @application.get("/_test/runtime-status")
    async def status():
        return counters | {
            "occupied": len(container.records.get("browser-test-authority-v1", {}).get("slots", []))
        }

    @application.post("/_test/unknown-prepare/{enabled}")
    async def unknown_prepare(enabled: str, request: Request):
        await Identity().require_runtime(request)
        if enabled not in {"on", "off"}:
            raise HTTPException(422, "invalid_fixture_fault")
        faults["unknown_prepare"] = enabled == "on"
        return {"configured": True}

    uvicorn.run(
        application,
        host="127.0.0.1",
        port=fixture["runtime_port"],
        ssl_certfile=str(path.parent / "certificate.pem"),
        ssl_keyfile=str(path.parent / "private-key.pem"),
        access_log=False,
        proxy_headers=False,
        ws="websockets-sansio",
        ws_max_size=8192,
        ws_max_queue=1,
    )


def _reserve_browser_replica(state, lock, gate, results, publication, encoded_permit, owner_id):
    import asyncio
    import json
    import os
    from datetime import UTC, datetime

    from azure.core import MatchConditions
    from azure.cosmos.exceptions import CosmosHttpResponseError

    from caller_runtime.adapters.test_admission_repository import TestAdmissionRepository
    from caller_runtime.domain.configuration import RevisionCache
    from caller_runtime.domain.conversation import StartPermit
    from caller_runtime.services.test_admission import AdmissionError, TestAdmissionService

    class Container:
        first = True

        async def read_item(self, item, partition_key, **kwargs):
            assert item == partition_key == "browser-test-authority-v1"
            assert kwargs["initial_headers"] == {"x-ms-consistency-level": "Strong"}
            with lock:
                document = json.loads(state["document"]) | {"_etag": state["etag"]}
                initial = self.first
                if initial:
                    self.first = False
                    state["initial_reads"] = state["initial_reads"] + 1
                    if state["initial_reads"] == 2:
                        gate.set()
            if initial:
                assert gate.wait(10)
            return document

        async def replace_item(self, item, body, **kwargs):
            assert item == "browser-test-authority-v1"
            assert kwargs["match_condition"] == MatchConditions.IfNotModified
            assert kwargs["retry_write"] == 0
            with lock:
                if kwargs["etag"] != state["etag"]:
                    raise CosmosHttpResponseError(status_code=412, message="synthetic-cas-conflict")
                state["version"] = state["version"] + 1
                state["etag"] = '"authority-' + str(state["version"]) + '"'
                state["document"] = json.dumps(body)
                return dict(body) | {"_etag": state["etag"]}

    async def reserve():
        permit = StartPermit.model_validate_json(encoded_permit)
        now = permit.admission_started_at.astimezone(UTC)
        assert isinstance(now, datetime)
        cache = RevisionCache(monotonic=lambda: 0.0)
        cache.install(publication, etag='"process-publication"')
        service = TestAdmissionService(
            repository=TestAdmissionRepository(container=Container()),
            configuration=cache,
            owner_id=owner_id,
            utc_now=lambda: now,
            monotonic=lambda: 0.0,
            max_clock_skew_seconds=0,
        )
        try:
            slot = await service.reserve(permit)
            results.put({"outcome": "reserved", "pid": os.getpid(), "mode": slot.mode})
        except AdmissionError as error:
            results.put({"outcome": error.code, "pid": os.getpid()})

    asyncio.run(reserve())


@pytest.fixture
def reviewed_runtime_options():
    return {
        "review_reference": "synthetic-controlled-review",
        "management_endpoint": "https://management.fixture.example",
        "management_audience": "11111111-2222-4333-8444-555555555555",
        "text_endpoint": "https://approved-fixture.openai.azure.com",
        "text_deployment": "reasoning-fixture",
        "live_endpoint": "https://approved-fixture.openai.azure.com",
        "approved_hosts": frozenset(
            {"management.fixture.example", "approved-fixture.openai.azure.com"}
        ),
        "manager_issuer": "https://management.fixture.example",
        "manager_audience": "caller-test-runtime-fixture",
        "live_region": "eastus2",
        "live_model_version": "2026-09-10",
        "available_live_sessions": 5,
        "text_features": frozenset(
            {"streaming", "function_tools", "storage_disabled", "structured_output"}
        ),
        "region_reviewed": True,
        "lifecycle_reviewed": True,
        "retention_reviewed": True,
        "identity_reviewed": True,
        "private_store_reviewed": True,
        "volatile_retention_seconds": 300,
        "max_input_tokens": 16384,
        "max_output_tokens": 512,
        "voice": "marin",
        "max_clock_skew_seconds": 0,
    }


@pytest.mark.parametrize(
    "region", ["eastus2", "centralus", "canadacentral", "swedencentral", "unreviewed-region"]
)
def test_runtime_region_allowlist_requires_independent_review(reviewed_runtime_options, region):
    from caller_runtime.composition import TestRuntimeOptions

    document = reviewed_runtime_options | {"live_region": region}
    if region == "unreviewed-region":
        with pytest.raises(ValueError):
            TestRuntimeOptions(**document)
        return
    assert TestRuntimeOptions(**document).live_region == region
    with pytest.raises(ValueError):
        TestRuntimeOptions(**(document | {"region_reviewed": False}))


@pytest.mark.parametrize(
    "changes",
    [
        None,
        {"available_live_sessions": 4},
        {"retention_reviewed": False},
        {"text_features": frozenset({"streaming"})},
        {"text_deployment": "gpt-live-1"},
        {"text_endpoint": "https://api.openai.com"},
        {"approved_hosts": frozenset()},
        {"volatile_retention_seconds": 301},
        {
            "management_endpoint": "https://api.openai.com",
            "text_endpoint": "https://api.openai.com",
            "live_endpoint": "https://api.openai.com",
            "approved_hosts": frozenset({"api.openai.com"}),
        },
    ],
    ids=[
        "no-review",
        "quota",
        "retention",
        "features",
        "voice-only-reasoning",
        "public-provider",
        "no-approved-host",
        "retention-limit",
        "equal-public-origins",
    ],
)
def test_runtime_composition_rejects_unreviewed_binding_before_any_factory_call(
    reviewed_runtime_options, signed_test_permit, changes
):
    from caller_runtime.composition import TestRuntimeComposition, TestRuntimeOptions

    public, sign, clock, assertion = signed_test_permit
    containers, credentials = AsyncMock(), AsyncMock()
    with pytest.raises(ValueError):
        options = (
            None if changes is None else TestRuntimeOptions(**(reviewed_runtime_options | changes))
        )
        TestRuntimeComposition(
            options=options,
            owner_id="runtime-fixture-owner",
            container_factory=containers,
            credential_factory=credentials,
            workload_identity=SimpleNamespace(require_runtime=AsyncMock()),
            verification_keys={"manager-fixture-key": public},
            utc_now=clock.utc_now,
            monotonic=clock.monotonic,
        )
    containers.assert_not_called()
    credentials.assert_not_called()


def test_composition_requires_explicit_structured_output_review_for_fragment_clarity(
    reviewed_runtime_options,
):
    from caller_runtime.composition import TestRuntimeOptions

    with pytest.raises(ValueError):
        TestRuntimeOptions(
            **(
                reviewed_runtime_options
                | {"text_features": frozenset({"streaming", "function_tools", "storage_disabled"})}
            )
        )


def test_reviewed_composition_construction_is_credential_free(
    reviewed_runtime_options, signed_test_permit
):
    from caller_runtime.composition import TestRuntimeComposition, TestRuntimeOptions

    public, sign, clock, assertion = signed_test_permit
    containers, credentials = AsyncMock(), AsyncMock()
    runtime = TestRuntimeComposition(
        options=TestRuntimeOptions(**reviewed_runtime_options),
        owner_id="runtime-fixture-owner",
        container_factory=containers,
        credential_factory=credentials,
        workload_identity=SimpleNamespace(require_runtime=AsyncMock()),
        verification_keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
    )
    assert runtime.options.review_reference == "synthetic-controlled-review"
    containers.assert_not_called()
    credentials.assert_not_called()


def test_composition_refuses_uninitialized_authority_before_credential_acquisition(
    reviewed_runtime_options, signed_test_permit, authority_store
):
    import asyncio

    from caller_runtime.composition import TestRuntimeComposition, TestRuntimeOptions
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.test_admission import AdmissionError

    public, sign, clock, assertion = signed_test_permit
    repository, container = authority_store
    containers, credentials = AsyncMock(return_value=container), AsyncMock()
    runtime = TestRuntimeComposition(
        options=TestRuntimeOptions(**reviewed_runtime_options),
        owner_id="runtime-fixture-owner",
        container_factory=containers,
        credential_factory=credentials,
        workload_identity=SimpleNamespace(require_runtime=AsyncMock()),
        verification_keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
    )
    telemetry = Telemetry("caller-runtime", "local")

    async def scenario():
        with pytest.raises(AdmissionError, match="test_runtime_unavailable"):
            async with runtime.running(telemetry):
                pytest.fail("uninitialized_test_authority_admitted")
        containers.assert_awaited_once()
        credentials.assert_not_called()

    try:
        asyncio.run(scenario())
    finally:
        telemetry.close()


@pytest.mark.parametrize("retained_terminal", [False, True])
def test_composition_owns_authenticated_refresh_without_model_or_default_identity(
    reviewed_runtime_options,
    signed_test_permit,
    admission_case,
    runtime_revision_v2,
    retained_terminal,
):
    import asyncio
    import time

    import httpx

    from caller_runtime.composition import TestRuntimeComposition, TestRuntimeOptions
    from caller_runtime.observability import Telemetry

    public, sign, clock, assertion = signed_test_permit
    previous, repository, container, previous_clock, permit, previous_cache = admission_case
    requests, scopes, disposed = [], [], []

    class Credentials:
        async def get_token(self, scope):
            scopes.append(scope)
            return SimpleNamespace(
                token="synthetic-composition-token", expires_on=time.time() + 900
            )

        async def close(self):
            disposed.append(True)

    async def configuration(request):
        requests.append(request)
        assert str(request.url) == "https://management.fixture.example/v2/runtime/configuration"
        assert request.headers["authorization"] == "Bearer synthetic-composition-token"
        return httpx.Response(
            200, json=runtime_revision_v2(), headers={"etag": '"current-fixture"'}
        )

    containers, credentials = (
        AsyncMock(return_value=container),
        AsyncMock(return_value=Credentials()),
    )
    runtime = TestRuntimeComposition(
        options=TestRuntimeOptions(**reviewed_runtime_options),
        owner_id="runtime-fixture-owner",
        container_factory=containers,
        credential_factory=credentials,
        workload_identity=SimpleNamespace(require_runtime=AsyncMock()),
        verification_keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        configuration_transport=httpx.MockTransport(configuration),
    )
    telemetry = Telemetry("caller-runtime", "local")

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        retained = None
        if retained_terminal:
            retained = await previous.reserve(permit(99))
            await previous.mark_terminal(retained.session_id, retained.owner_epoch)
        async with runtime.running(telemetry):
            assert runtime.cache.pin().revision_id == runtime_revision_v2()["id"]
            assert not runtime.active_sessions
            containers.assert_awaited_once()
            credentials.assert_awaited_once()
            assert len(requests) == 1
            if retained_terminal:
                assert any(
                    event["event"] == "test.cleanup"
                    and event.get("reason_code") == "cleanup_pending"
                    for event in telemetry.events
                )
        assert disposed == [True]
        assert scopes == ["api://11111111-2222-4333-8444-555555555555/.default"]
        slots = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots
        assert len(slots) == int(retained_terminal)
        if retained is not None:
            assert slots[0].owner_id == retained.owner_id and slots[0].attempted_through == 0

    try:
        asyncio.run(scenario())
    finally:
        telemetry.close()


@pytest.mark.parametrize(
    "outcome",
    ["ready", "stale", "late", "unknown", "unsupported_deployment", "construction", "publication"],
)
@pytest.mark.parametrize("mode", ["text", "speech"])
def test_composed_runtime_prepares_same_pinned_backend_and_resource_aware_cleanup(
    reviewed_runtime_options,
    signed_test_permit,
    admission_case,
    text_execution_case,
    runtime_revision_v2,
    live_case,
    mode,
    outcome,
    monkeypatch,
):
    import asyncio
    import time

    import httpx

    from caller_runtime.composition import TestRuntimeComposition, TestRuntimeOptions
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.test_admission import AdmissionError

    public, sign, clock, assertion = signed_test_permit
    old_service, repository, container, controlled_clock, permit, cache = admission_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    live_service, live_repository, live_clock, socket, opened = live_case
    sockets, disposed = [], []
    publication = [1]
    controls["prepare"] = True
    controls["prepare_error"] = outcome in {"unknown", "unsupported_deployment"}
    controls["prepare_error_status"] = 404 if outcome == "unsupported_deployment" else 503
    controls["prepare_late"] = outcome == "late"

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(token="synthetic-composed-token", expires_on=time.time() + 900)

        async def close(self):
            disposed.append(True)

    async def configuration(request):
        assert str(request.url) == "https://management.fixture.example/v2/runtime/configuration"
        if outcome == "stale" and clock.monotonic() >= 30:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=runtime_revision_v2(publication[0]),
            headers={"etag": '"current-fixture-' + str(publication[0]) + '"'},
        )

    async def connect(url, **options):
        assert mode == "speech"
        fresh = type(socket)()
        sockets.append(fresh)
        return fresh

    options = TestRuntimeOptions(
        **(
            reviewed_runtime_options
            | {"manager_issuer": assertion.issuer, "manager_audience": assertion.audience}
        )
    )
    runtime = TestRuntimeComposition(
        options=options,
        owner_id="runtime-fixture-owner",
        container_factory=AsyncMock(return_value=container),
        credential_factory=AsyncMock(return_value=Credentials()),
        workload_identity=SimpleNamespace(
            require_runtime=AsyncMock(return_value="management-workload-fixture")
        ),
        verification_keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        configuration_transport=httpx.MockTransport(configuration),
        text_transport=adapter._transport,
        live_socket_factory=connect,
    )
    telemetry = Telemetry("caller-runtime", "local")

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        async with runtime.running(telemetry):
            if outcome == "publication":
                original = await runtime.start(permit(1, mode=mode), emit=emit)
                publication[0] = 2
                changed = await runtime.start(
                    permit(2, actor="other-manager", mode=mode), emit=emit
                )
                assert changed.policy.slot.revision_id == runtime_revision_v2(2)["id"]
                assert original.policy.slot.revision_id == runtime_revision_v2(1)["id"]
                assert await changed.close() and await original.close()
                return
            if outcome != "ready":
                if outcome == "stale":
                    clock.advance(30)
                if outcome == "construction":

                    def failed_pin(slot):
                        raise RuntimeError("synthetic-before-controller-failure")

                    monkeypatch.setattr(runtime.admission, "snapshot_for", failed_pin)
                with pytest.raises(AdmissionError):
                    await runtime.start(permit(1, mode=mode), emit=emit)
                if runtime._cleanup_tasks:
                    await asyncio.gather(*tuple(runtime._cleanup_tasks))
                slots = (
                    await repository.read(deadline=asyncio.get_running_loop().time() + 1)
                ).authority.slots
                assert len(requests) == (0 if outcome in {"stale", "construction"} else 1)
                assert not sockets and not any(message["type"] == "ready" for message in messages)
                if outcome in {"unknown", "unsupported_deployment"}:
                    assert len(slots) == 1 and slots[0].terminal_fence
                    assert (
                        slots[0].resources["model"].attempted == 1
                        and slots[0].resources["model"].settled == 0
                    )
                    if outcome == "unsupported_deployment":
                        assert controls["prepare_statuses"] == [404]
                        assert "synthetic-private-provider-response" not in str(telemetry.events)
                else:
                    assert not slots
                return
            controller = await runtime.start(permit(1, mode=mode), emit=emit)
            slot = controller.policy.slot
            assert slot.ready_at is not None and slot.active_agent_id == "welcome"
            assert controller.policy.snapshot is runtime.admission.snapshot_for(slot)
            assert (
                len(requests) == 1
                and requests[0]["messages"][0]["content"] == "Synthetic welcome prompt"
            )
            assert slot.resources["model"].attempted == slot.resources["model"].settled == 1
            assert slot.resources["capability"].attempted == 0
            assert slot.resources["live"].attempted == (2 if mode == "speech" else 0)
            assert len(sockets) == int(mode == "speech")
            assert await controller.close()
            assert controller.retained_bytes == 0
            assert not (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots
        assert disposed == [True] and all(socket.closed == 1 for socket in sockets)
        await adapter.close()

    try:
        asyncio.run(scenario())
    finally:
        telemetry.close()


def test_app_mounts_browser_runtime_only_when_reviewed_composition_is_explicit(
    reviewed_runtime_options, signed_test_permit
):
    from caller_runtime.composition import TestRuntimeComposition, TestRuntimeOptions
    from caller_runtime.main import create_app

    public, sign, clock, assertion = signed_test_permit
    containers, credentials = AsyncMock(), AsyncMock()
    runtime = TestRuntimeComposition(
        options=TestRuntimeOptions(**reviewed_runtime_options),
        owner_id="runtime-fixture-owner",
        container_factory=containers,
        credential_factory=credentials,
        workload_identity=SimpleNamespace(
            require_runtime=AsyncMock(return_value="management-workload-fixture")
        ),
        verification_keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
    )
    plain = create_app()
    explicit = create_app(test_runtime=runtime)
    assert "/v1/internal/test-sessions" not in {
        getattr(route, "path", "") for route in plain.routes
    }
    with TestClient(explicit).websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        assert socket.accepted_subprotocol == "caller.test.v1"
        socket.close()
    assert explicit.state.test_runtime is runtime
    containers.assert_not_called()
    credentials.assert_not_called()


def test_app_lifespan_owns_reviewed_runtime_and_fixed_signed_permit_socket(
    reviewed_runtime_options,
    signed_test_permit,
    admission_case,
    text_execution_case,
    runtime_revision_v2,
):
    import asyncio
    import time

    import httpx

    from caller_runtime.composition import TestRuntimeComposition, TestRuntimeOptions
    from caller_runtime.main import create_app

    public, sign, clock, assertion = signed_test_permit
    old_service, repository, container, controlled_clock, permit, cache = admission_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controls["prepare"] = True
    disposed = []

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-app-runtime-token", expires_on=time.time() + 900
            )

        async def close(self):
            disposed.append(True)

    async def configuration(request):
        return httpx.Response(
            200, json=runtime_revision_v2(), headers={"etag": '"current-fixture"'}
        )

    options = TestRuntimeOptions(
        **(
            reviewed_runtime_options
            | {"manager_issuer": assertion.issuer, "manager_audience": assertion.audience}
        )
    )
    runtime = TestRuntimeComposition(
        options=options,
        owner_id="runtime-fixture-owner",
        container_factory=AsyncMock(return_value=container),
        credential_factory=AsyncMock(return_value=Credentials()),
        workload_identity=SimpleNamespace(
            require_runtime=AsyncMock(return_value="management-workload-fixture")
        ),
        verification_keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        configuration_transport=httpx.MockTransport(configuration),
        text_transport=adapter._transport,
    )

    async def initialize():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )

    asyncio.run(initialize())
    with TestClient(create_app(test_runtime=runtime)) as client:
        with client.websocket_connect(
            SOCKET_URL,
            subprotocols=["caller.test.v1"],
            headers={"Authorization": "Bearer synthetic-management-workload"},
        ) as socket:
            socket.send_json(
                {"type": "authenticate", "permit": sign(), "requestId": assertion.request_id}
            )
            ready = socket.receive_json()
            assert ready["type"] == "ready" and ready["mode"] == "text"
            assert len(requests) == 1 and ready["agentId"] == "welcome"
            socket.send_json({"type": "stop", "sequence": 1, "sessionId": ready["sessionId"]})
            ended = socket.receive_json()
            assert ended["type"] == "ended" and ended["cleanupStatus"] == "complete"
    assert disposed == [True] and not runtime.active_sessions


@pytest.fixture
def owned_text_socket(text_execution_case, admission_case, live_case):
    from caller_runtime.api.test_sessions import create_router
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.conversation import (
        ConversationExecutor,
        ConversationPolicy,
        SpeechSegments,
    )

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    admission, repository, container, clock, permit, cache = admission_case

    class Identity:
        async def require_runtime(self, request):
            if request.headers.get("authorization") != "Bearer synthetic-management-workload":
                raise HTTPException(401, "synthetic-private-auth")
            if controls.get("identity_none"):
                return None
            return "management-workload-fixture"

    class Permits:
        async def verify(self, raw):
            if raw != "synthetic-signed-test-permit":
                raise PermissionError("synthetic-private-permit")
            return permit(1, mode=service.mode)

    class Service:
        calls = 0
        controller = None
        mode = "text"
        segment = None
        provider_socket = None

        async def start(self, assertion, *, emit):
            import asyncio

            self.loop = asyncio.get_running_loop()
            self.calls += 1
            if controls.get("block_admission"):
                if self.calls > 2:
                    raise RuntimeError("unexpected_third_actor_admission")
                if self.calls == 2:
                    controls["two_admissions"].set()
                await controls["release_admissions"].wait()
            assert assertion.request_id == permit(1).request_id
            if self.mode == "speech":
                live_admission, live_repository, live_clock, provider_socket, opened = live_case
                original, segment = await opened()
                slot = await admission.check_output(
                    original.session_id, original.owner_epoch, generation=1
                )
                segments = SpeechSegments(
                    current=segment,
                    instructions_for=lambda current: "Synthetic continuation voice",
                    emit=emit,
                )
                policy = ConversationPolicy(
                    slot=slot,
                    snapshot=cache.pin(),
                    admission=admission,
                    retire_speech=segments.retire,
                    continue_speech=segments.continue_with,
                )
                self.segment, self.provider_socket = segment, provider_socket
                self.controller = ConversationExecutor(
                    policy=policy,
                    text_adapter=adapter,
                    utc_now=clock.utc_now,
                    speech_segments=segments,
                )
            else:
                self.controller = await setup()
            return self.controller

    telemetry = Telemetry("caller-runtime", "local")
    service = Service()
    service.controls = controls
    app = FastAPI()
    app.include_router(
        create_router(
            service=service,
            workload_identity=Identity(),
            permits=Permits(),
            telemetry=telemetry,
            utc_now=clock.utc_now,
            max_clock_skew_seconds=0,
        )
    )
    try:
        with TestClient(app) as client:
            yield client, service, requests, clock, permit(1), telemetry
    finally:
        telemetry.close()


@contextmanager
def owned_tls_listener(application, tmp_path):
    import socket
    import ssl
    import threading
    from datetime import UTC, datetime, timedelta
    from ipaddress import ip_address

    import uvicorn
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    certificate_path, key_path = tmp_path / "certificate.pem", tmp_path / "private-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    entered, failures = threading.Event(), []

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            entered.set()

    config = uvicorn.Config(
        application,
        host="127.0.0.1",
        port=port,
        ssl_certfile=str(certificate_path),
        ssl_keyfile=str(key_path),
        loop="asyncio",
        ws="websockets-sansio",
        lifespan="on",
        proxy_headers=False,
        ws_max_size=8192,
        ws_max_queue=1,
        timeout_graceful_shutdown=10,
        log_config=None,
        log_level="critical",
        access_log=False,
    )
    server = Server(config)

    def run():
        try:
            server.run(sockets=[listener])
        except BaseException as error:
            failures.append(type(error).__name__)
            entered.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert entered.wait(10) and server.started and not failures
        context = ssl.create_default_context(cafile=str(certificate_path))
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        yield "wss://127.0.0.1:" + str(port) + "/v1/internal/test-sessions", context
    finally:
        server.should_exit = True
        thread.join(12)
        if thread.is_alive():
            server.force_exit = True
            thread.join(2)
        listener.close()
        assert not thread.is_alive() and not failures


@pytest.fixture
def owned_tls_runtime(owned_text_socket, tmp_path):
    client, service, requests, clock, assertion, telemetry = owned_text_socket
    with owned_tls_listener(client.app, tmp_path) as (url, context):
        yield url, context, service, requests, assertion


def authenticate(socket, assertion):
    socket.send_json(
        {
            "type": "authenticate",
            "permit": "synthetic-signed-test-permit",
            "requestId": assertion.request_id,
        }
    )
    return socket.receive_json()


def test_owned_socket_streams_bounded_text_and_acknowledges_duplicate_without_replay(
    owned_text_socket,
):
    client, service, requests, clock, assertion, telemetry = owned_text_socket
    with client.websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        ready = authenticate(socket, assertion)
        assert ready["type"] == "ready" and ready["mode"] == "text"
        assert ready["revisionId"] == service.controller.policy.slot.revision_id
        command = {
            "type": "text.submit",
            "sequence": 1,
            "sessionId": ready["sessionId"],
            "text": "Synthetic owned socket question",
        }
        socket.send_json(command)
        events = []
        while not events or events[-1]["type"] != "text.completed":
            events.append(socket.receive_json())
        assert (
            "".join(event["text"] for event in events if event["type"] == "text.delta")
            == "Synthetic shared streamed reply"
        )
        socket.send_json(command)
        ack = socket.receive_json()
        assert ack["type"] == "command.ack" and ack["acceptedSequence"] == 1
        assert len(requests) == service.calls == 1
        socket.send_json({"type": "stop", "sequence": 2, "sessionId": ready["sessionId"]})
        assert socket.receive_json()["type"] == "ended"
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 1000


@pytest.mark.parametrize(
    "fault,close_code", [("unknown-control", 1008), ("oversized", 1009), ("binary-text-mode", 1008)]
)
def test_owned_socket_rejects_bad_frames_without_model_dispatch(
    owned_text_socket, fault, close_code
):
    client, service, requests, clock, assertion, telemetry = owned_text_socket
    with client.websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        ready = authenticate(socket, assertion)
        if fault == "binary-text-mode":
            socket.send_bytes(b"\x01" + b"\0" * 10)
        elif fault == "oversized":
            socket.send_text("x" * 8193)
        else:
            socket.send_json(
                {"type": "provider.override", "sequence": 1, "sessionId": ready["sessionId"]}
            )
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == close_code
        assert not requests


def test_plaintext_socket_is_rejected_before_any_admission(owned_text_socket):
    client, service, requests, clock, assertion, telemetry = owned_text_socket
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "ws://runtime.fixture.example/v1/internal/test-sessions",
            subprotocols=["caller.test.v1"],
            headers={"Authorization": "Bearer synthetic-management-workload"},
        ):
            pytest.fail("plaintext_test_socket_admitted")
    assert service.calls == 0 and not requests


def test_owned_socket_rejects_expired_session_even_for_heartbeat(owned_text_socket):
    client, service, requests, clock, assertion, telemetry = owned_text_socket
    with client.websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        ready = authenticate(socket, assertion)
        clock.advance(121)
        socket.send_json(
            {
                "type": "ping",
                "sequence": 1,
                "sessionId": ready["sessionId"],
                "nonce": "liveness-only",
            }
        )
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 1008 and not requests


def test_owned_socket_runs_independent_authority_watch_and_ends_on_control_loss(
    owned_text_socket, admission_case, monkeypatch
):
    client, service, requests, clock, assertion, telemetry = owned_text_socket
    admission, repository, container, controlled_clock, permit, cache = admission_case
    calls = []

    async def lost_authority(session_id, owner_epoch, *, stopped, observed):
        calls.append(session_id)
        admission._terminal[session_id].set()

    monkeypatch.setattr(admission, "watch", lost_authority)
    with client.websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        ready = authenticate(socket, assertion)
        socket.send_json(
            {
                "type": "ping",
                "sequence": 1,
                "sessionId": ready["sessionId"],
                "nonce": "transport-only",
            }
        )
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 1008 and calls == [ready["sessionId"]]
        assert not requests


def test_owned_speech_socket_exchanges_credited_pcm_mute_and_stop_without_text_model(
    owned_text_socket,
):
    import base64
    import struct

    client, service, requests, clock, assertion, telemetry = owned_text_socket
    service.mode = "speech"
    with client.websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        ready = authenticate(socket, assertion)
        assert ready["mode"] == "speech" and ready["audioFormat"] == {
            "codec": "pcm16le",
            "sampleRate": 24000,
            "channels": 1,
        }
        credit = socket.receive_json()
        assert credit["type"] == "audio.credit" and 0 < credit["availableSamples"] <= 6000
        pcm = b"\x05\0" * 480
        socket.send_bytes(struct.pack(">BII", 1, ready["generation"], 1) + pcm)
        socket.send_json(
            {"type": "input.mute", "sessionId": ready["sessionId"], "sequence": 1, "muted": True}
        )
        while True:
            event = socket.receive_json()
            if event["type"] == "command.ack":
                assert event["acceptedSequence"] == 1
                break
        appended = [
            event
            for event in service.provider_socket.sent
            if event["type"] == "session.input_audio.append"
        ]
        assert len(appended) == 1 and base64.b64decode(appended[0]["audio"]) == pcm
        assert any(
            event["type"] == "session.input_audio.mute" for event in service.provider_socket.sent
        )
        socket.send_json({"type": "stop", "sessionId": ready["sessionId"], "sequence": 2})
        while socket.receive_json()["type"] != "ended":
            pass
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 1000 and not requests and service.provider_socket.closed == 1


def test_owned_speech_socket_writes_actual_pcm_and_accepts_only_sent_played_sequence(
    owned_text_socket,
):
    import base64
    import json
    import struct

    client, service, requests, clock, assertion, telemetry = owned_text_socket
    service.mode = "speech"
    with client.websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        ready = authenticate(socket, assertion)
        assert socket.receive_json()["type"] == "audio.credit"
        pcm = b"\x05\0" * 480
        assert client.portal is not None
        client.portal.call(
            service.provider_socket.events.put,
            json.dumps(
                {
                    "type": "session.output_audio.delta",
                    "delta": base64.b64encode(pcm).decode(),
                    "start_ms": 0,
                    "end_ms": 20,
                }
            ),
        )
        while True:
            message = socket.receive()
            if message.get("bytes") is not None:
                break
            assert json.loads(message["text"])["type"] == "audio.credit"
        frame = message["bytes"]
        assert struct.unpack(">BII", frame[:9]) == (1, ready["generation"], 1) and frame[9:] == pcm
        socket.send_json(
            {
                "type": "audio.played",
                "sessionId": ready["sessionId"],
                "sequence": 1,
                "audioSequence": 1,
                "generation": ready["generation"],
            }
        )
        socket.send_json(
            {
                "type": "ping",
                "sessionId": ready["sessionId"],
                "sequence": 2,
                "nonce": "played-processed",
            }
        )
        while socket.receive_json()["type"] != "pong":
            pass
        socket.send_json({"type": "stop", "sessionId": ready["sessionId"], "sequence": 3})
        while socket.receive_json()["type"] != "ended":
            pass
        assert not requests


@pytest.mark.parametrize(
    "fault,close_code",
    [("odd", 1008), ("oversized", 1009), ("sequence", 1008), ("unsent-ack", 1008)],
)
def test_owned_speech_socket_rejects_malformed_pcm_and_forged_played_ack(
    owned_text_socket, fault, close_code
):
    import struct

    client, service, requests, clock, assertion, telemetry = owned_text_socket
    service.mode = "speech"
    with client.websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        ready = authenticate(socket, assertion)
        assert socket.receive_json()["type"] == "audio.credit"
        if fault == "unsent-ack":
            socket.send_json(
                {
                    "type": "audio.played",
                    "sessionId": ready["sessionId"],
                    "sequence": 1,
                    "audioSequence": 1,
                    "generation": ready["generation"],
                }
            )
        else:
            pcm = b"\0" * (1922 if fault == "oversized" else 959 if fault == "odd" else 960)
            socket.send_bytes(
                struct.pack(">BII", 1, ready["generation"], 2 if fault == "sequence" else 1) + pcm
            )
        with pytest.raises(WebSocketDisconnect) as failure:
            while True:
                event = socket.receive_json()
                assert event["type"] == "audio.credit"
        assert failure.value.code == close_code and not requests


@pytest.mark.parametrize("clear", [False, True])
def test_owned_speech_correlates_verified_fragments_then_uses_shared_clarity_and_reasoning(
    owned_text_socket, monkeypatch, clear
):
    import asyncio
    import json
    import struct

    client, service, requests, clock, assertion, telemetry = owned_text_socket
    service.mode = "speech"
    service.controls["clarity"] = clear
    with client.websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        ready = authenticate(socket, assertion)
        assert socket.receive_json()["type"] == "audio.credit"
        socket.send_bytes(struct.pack(">BII", 1, ready["generation"], 1) + b"\0" * 960)
        socket.send_json(
            {
                "type": "ping",
                "sequence": 1,
                "sessionId": ready["sessionId"],
                "nonce": "audio-forwarded",
            }
        )
        while socket.receive_json()["type"] != "pong":
            pass
        assert client.portal is not None

        async def watch_commentary():
            completed = asyncio.Event()
            send = service.provider_socket.send

            async def observed_send(raw):
                await send(raw)
                if json.loads(raw)["type"] == "session.commentary.append":
                    completed.set()

            monkeypatch.setattr(service.provider_socket, "send", observed_send)
            await service.provider_socket.events.put(
                json.dumps(
                    {
                        "type": "session.input_transcript.delta",
                        "delta": "Synthetic correlated task?",
                        "start_ms": 0,
                        "end_ms": 20,
                    }
                )
            )
            await service.provider_socket.events.put(
                json.dumps(
                    {
                        "type": "session.delegation.created",
                        "delegation": {
                            "id": "delegation-wire-task",
                            "type": "delegation",
                            "target": "client",
                        },
                        "offset_ms": 20,
                    }
                )
            )
            await asyncio.wait_for(completed.wait(), 10)

        client.portal.call(watch_commentary)
        assert len(requests) == 1 + int(clear)
        appended = [
            event
            for event in service.provider_socket.sent
            if event["type"] == "session.commentary.append"
        ]
        assert appended[-1]["delegation_id"] == "delegation-wire-task"
        assert ("clarify" in appended[-1]["content"].lower()) is not clear
        socket.send_json({"type": "stop", "sequence": 2, "sessionId": ready["sessionId"]})
        while True:
            event = socket.receive_json()
            assert not event["type"].startswith("text.")
            if event["type"] == "ended":
                break


def test_owned_speech_interrupt_retires_socket_once_and_acknowledges_duplicate(
    owned_text_socket, monkeypatch
):
    client, service, requests, clock, assertion, telemetry = owned_text_socket
    service.mode = "speech"
    with client.websocket_connect(
        SOCKET_URL,
        subprotocols=["caller.test.v1"],
        headers={"Authorization": "Bearer synthetic-management-workload"},
    ) as socket:
        ready = authenticate(socket, assertion)
        assert socket.receive_json()["type"] == "audio.credit"
        created = []

        async def connect(url, **options):
            assert service.provider_socket.closed == 1 and service.segment.protocol.closed
            fresh = type(service.provider_socket)()
            created.append(fresh)
            return fresh

        monkeypatch.setattr(service.segment.adapter, "_factory", connect)
        command = {
            "type": "output.interrupt",
            "sessionId": ready["sessionId"],
            "sequence": 1,
            "generation": ready["generation"],
        }
        socket.send_json(command)
        generation = ready["generation"]
        while True:
            event = socket.receive_json()
            if event["type"] == "generation.changed":
                generation = event["generation"]
            if event["type"] == "command.ack":
                break
        assert generation > ready["generation"] and len(created) == 1
        socket.send_json(command)
        while True:
            event = socket.receive_json()
            if event["type"] == "command.ack":
                assert event["acceptedSequence"] == 1
                break
        assert len(created) == 1 and service.controller.policy.slot.generation == generation
        socket.send_json({"type": "stop", "sessionId": ready["sessionId"], "sequence": 2})
        while socket.receive_json()["type"] != "ended":
            pass
        assert created[0].closed == 1 and not requests


def test_owned_socket_limits_verified_pre_auth_to_two_per_stable_actor(owned_text_socket):
    import asyncio
    from contextlib import ExitStack

    client, service, requests, clock, assertion, telemetry = owned_text_socket
    service.controls.update(
        block_admission=True, two_admissions=asyncio.Event(), release_admissions=asyncio.Event()
    )
    with ExitStack() as stack:
        sockets = [
            stack.enter_context(
                client.websocket_connect(
                    SOCKET_URL,
                    subprotocols=["caller.test.v1"],
                    headers={"Authorization": "Bearer synthetic-management-workload"},
                )
            )
            for _ in range(2)
        ]
        for socket in sockets:
            socket.send_json(
                {
                    "type": "authenticate",
                    "permit": "synthetic-signed-test-permit",
                    "requestId": assertion.request_id,
                }
            )
        assert client.portal is not None

        async def two_entered():
            await asyncio.wait_for(service.controls["two_admissions"].wait(), 1)

        client.portal.call(two_entered)
        try:
            third = stack.enter_context(
                client.websocket_connect(
                    SOCKET_URL,
                    subprotocols=["caller.test.v1"],
                    headers={"Authorization": "Bearer synthetic-management-workload"},
                )
            )
            third.send_json(
                {
                    "type": "authenticate",
                    "permit": "synthetic-signed-test-permit",
                    "requestId": assertion.request_id,
                }
            )
            with pytest.raises(WebSocketDisconnect) as failure:
                third.receive_json()
            assert failure.value.code == 1013 and service.calls == 2
            assert not requests
        finally:
            client.portal.call(service.controls["release_admissions"].set)
            admitted = 0
            for pending in sockets:
                try:
                    ready = pending.receive_json()
                except WebSocketDisconnect as rejected:
                    assert rejected.code == 1013
                    continue
                assert ready["type"] == "ready"
                admitted += 1
                pending.send_json({"type": "stop", "sequence": 1, "sessionId": ready["sessionId"]})
                assert pending.receive_json()["type"] == "ended"
                with pytest.raises(WebSocketDisconnect) as stopped:
                    pending.receive_json()
                assert stopped.value.code == 1000
            assert admitted == 1


def test_owned_socket_limits_unverified_connections_to_ten_before_allocation(owned_text_socket):
    from contextlib import ExitStack

    client, service, requests, clock, assertion, telemetry = owned_text_socket
    with ExitStack() as stack:
        for _ in range(10):
            stack.enter_context(
                client.websocket_connect(
                    SOCKET_URL,
                    subprotocols=["caller.test.v1"],
                    headers={"Authorization": "Bearer synthetic-management-workload"},
                )
            )
        with pytest.raises(WebSocketDisconnect) as failure:
            with client.websocket_connect(
                SOCKET_URL,
                subprotocols=["caller.test.v1"],
                headers={"Authorization": "Bearer synthetic-management-workload"},
            ):
                pytest.fail("eleventh_pre_auth_socket_admitted")
        assert failure.value.code == 1013 and service.calls == 0 and not requests


@pytest.mark.parametrize("missing", [False, True])
def test_workload_identity_must_return_verified_principal_before_upgrade(
    owned_text_socket, missing
):
    client, service, requests, clock, assertion, telemetry = owned_text_socket
    service.controls["identity_none"] = not missing
    with pytest.raises(WebSocketDisconnect) as failure:
        with client.websocket_connect(
            SOCKET_URL,
            subprotocols=["caller.test.v1"],
            headers={} if missing else {"Authorization": "Bearer synthetic-management-workload"},
        ):
            pytest.fail("unverified_workload_socket_upgraded")
    assert failure.value.code == 1008 and service.calls == 0 and not requests
    assert "synthetic-private" not in repr(telemetry.events)


def test_owned_tls_socket_requires_trusted_ca_and_streams_without_cloud(owned_tls_runtime):
    import asyncio
    import json
    import ssl

    from websockets.asyncio.client import connect

    url, context, service, requests, assertion = owned_tls_runtime

    async def scenario():
        with pytest.raises(ssl.SSLCertVerificationError):
            async with connect(url, ssl=ssl.create_default_context(), proxy=None, open_timeout=3):
                pytest.fail("untrusted_local_certificate_accepted")
        assert not requests
        async with connect(
            url,
            ssl=context,
            proxy=None,
            compression=None,
            open_timeout=3,
            subprotocols=["caller.test.v1"],
            additional_headers={"Authorization": "Bearer synthetic-management-workload"},
            max_size=8192,
            max_queue=1,
            write_limit=12000,
        ) as socket:
            await socket.send(
                json.dumps(
                    {
                        "type": "authenticate",
                        "permit": "synthetic-signed-test-permit",
                        "requestId": assertion.request_id,
                    }
                )
            )
            ready = json.loads(await asyncio.wait_for(socket.recv(), 3))
            assert ready["type"] == "ready" and ready["mode"] == "text"
            await socket.send(
                json.dumps(
                    {
                        "type": "text.submit",
                        "sequence": 1,
                        "sessionId": ready["sessionId"],
                        "text": "Synthetic actual TLS question",
                    }
                )
            )
            output = []
            while True:
                event = json.loads(await asyncio.wait_for(socket.recv(), 3))
                if event["type"] == "text.delta":
                    output.append(event["text"])
                if event["type"] == "text.completed":
                    break
            assert "".join(output) == "Synthetic shared streamed reply" and len(requests) == 1
            await socket.send(
                json.dumps({"type": "stop", "sequence": 2, "sessionId": ready["sessionId"]})
            )
            ended = json.loads(await asyncio.wait_for(socket.recv(), 3))
            assert ended["type"] == "ended" and ended["cleanupStatus"] == "pending"
        assert service.controller.retained_bytes == 0

    asyncio.run(scenario())


def test_owned_tls_twenty_publications_new_starts_preserve_pin_and_current_revocation(
    reviewed_runtime_options,
    signed_test_permit,
    admission_case,
    text_execution_case,
    runtime_revision_v2,
    tmp_path,
):
    import asyncio
    import hashlib
    import json
    import time

    import httpx
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    from caller_runtime.composition import TestRuntimeComposition, TestRuntimeOptions
    from caller_runtime.main import create_app

    public, sign, clock, assertion = signed_test_permit
    old_service, repository, container, controlled_clock, permit, cache = admission_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controls["prepare"] = True
    publication, revoked, capability_attempts, credentials_closed = [1], [False], [], []

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-publication-token", expires_on=time.time() + 900
            )

        async def close(self):
            credentials_closed.append(True)

    async def management(request):
        if request.method == "GET":
            assert request.url.path == "/v2/runtime/configuration"
            return httpx.Response(
                200,
                json=runtime_revision_v2(publication[0]),
                headers={"etag": '"publication-' + str(publication[0]) + '"'},
            )
        assert request.url.path == "/v1/runtime/capabilities/execute"
        attempt = json.loads(request.content)
        capability_attempts.append(attempt)
        assert revoked[0] and attempt["pinnedRevisionId"] == runtime_revision_v2(1)["id"]
        assert attempt["agentId"] == "b-entry" and attempt["singleAttempt"] is True
        return httpx.Response(403, json={"code": "capability_not_allowed"})

    async def initialize():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )

    asyncio.run(initialize())
    options = TestRuntimeOptions(
        **(
            reviewed_runtime_options
            | {"manager_issuer": assertion.issuer, "manager_audience": assertion.audience}
        )
    )
    runtime = TestRuntimeComposition(
        options=options,
        owner_id="runtime-publication-owner",
        container_factory=AsyncMock(return_value=container),
        credential_factory=AsyncMock(return_value=Credentials()),
        workload_identity=SimpleNamespace(
            require_runtime=AsyncMock(return_value="management-workload-fixture")
        ),
        verification_keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        configuration_transport=httpx.MockTransport(management),
        capability_transport=httpx.MockTransport(management),
        text_transport=adapter._transport,
    )

    with owned_tls_listener(create_app(test_runtime=runtime), tmp_path) as (url, context):

        async def open_session(number):
            socket = await connect(
                url,
                ssl=context,
                proxy=None,
                compression=None,
                max_size=8192,
                max_queue=1,
                write_limit=12000,
                open_timeout=3,
                subprotocols=["caller.test.v1"],
                additional_headers={"Authorization": "Bearer synthetic-management-workload"},
            )
            changes = {
                "actorId": "manager-" + str(number),
                "requestId": "request-" + str(number),
                "nonce": f"{number:064x}",
                "managementSessionDigest": f"{number:064x}",
            }
            await socket.send(
                json.dumps(
                    {
                        "type": "authenticate",
                        "permit": sign(changes),
                        "requestId": changes["requestId"],
                    }
                )
            )
            ready = json.loads(await asyncio.wait_for(socket.recv(), 3))
            assert ready["type"] == "ready"
            return socket, ready

        async def stop(socket, ready):
            await socket.send(
                json.dumps({"type": "stop", "sequence": 1, "sessionId": ready["sessionId"]})
            )
            ended = json.loads(await asyncio.wait_for(socket.recv(), 3))
            assert ended["type"] == "ended" and ended["cleanupStatus"] == "complete"
            await socket.close()

        async def scenario():
            original, pinned = await open_session(1)
            try:
                started = time.monotonic()
                for number in range(2, 22):
                    publication[0] = number
                    current, ready = await open_session(number)
                    assert ready["revisionId"] == runtime_revision_v2(number)["id"]
                    assert ready["revisionNumber"] == number
                    assert pinned["revisionId"] == runtime_revision_v2(1)["id"]
                    await stop(current, ready)
                assert time.monotonic() - started < 60
                controls["wire_actions"] = [
                    ("enter_routing", {}),
                    ("route_community", {"community_id": "beta"}),
                ]
                await original.send(
                    json.dumps(
                        {
                            "type": "text.submit",
                            "sequence": 1,
                            "sessionId": pinned["sessionId"],
                            "text": "Synthetic original pinned request",
                        }
                    )
                )
                while True:
                    event = json.loads(await asyncio.wait_for(original.recv(), 3))
                    if event["type"] == "text.completed":
                        assert event["generation"] > pinned["generation"]
                        break
                revoked[0] = True
                assert runtime._sessions[pinned["sessionId"]].policy.active_agent_id == "b-entry", (
                    controls.get("wire_action_observations")
                )
                assert controls["wire_actions"] == []
                public_name = (
                    "public_" + hashlib.sha256(b"beta-tool:public-lookup").hexdigest()[:32]
                )
                controls["wire_actions"] = [(public_name, {"inputs": {}})]
                await original.send(
                    json.dumps(
                        {
                            "type": "text.submit",
                            "sequence": 2,
                            "sessionId": pinned["sessionId"],
                            "text": "Synthetic revoked capability proposal",
                        }
                    )
                )
                try:
                    while True:
                        event = json.loads(await asyncio.wait_for(original.recv(), 3))
                        assert event["type"] != "text.completed"
                        if event["type"] == "error":
                            break
                except ConnectionClosed:
                    pass
                assert len(capability_attempts) == 1
                assert (
                    runtime._sessions[pinned["sessionId"]].policy.slot.revision_id
                    == runtime_revision_v2(1)["id"]
                )
            finally:
                await original.close()

        asyncio.run(scenario())
    assert credentials_closed == [True] and not runtime.active_sessions


def test_independent_runtime_processes_share_actor_capacity_and_cas_history(
    admission_case, runtime_revision_v2, monkeypatch
):
    import asyncio
    import multiprocessing
    import os
    from multiprocessing.managers import SyncManager
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    service, repository, container, clock, permit, cache = admission_case

    async def initialize():
        return await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )

    initial = asyncio.run(initialize())
    context = multiprocessing.get_context("spawn")
    with SyncManager(address=("127.0.0.1", 0), authkey=os.urandom(32), ctx=context) as manager:
        state = manager.dict(
            document=initial.authority.model_dump_json(by_alias=True),
            etag='"authority-1"',
            version=1,
            initial_reads=0,
        )
        lock, gate, results = manager.Lock(), manager.Event(), manager.Queue()
        processes = []
        for number, mode in ((1, "text"), (2, "speech")):
            process = context.Process(
                target=_reserve_browser_replica,
                args=(
                    state,
                    lock,
                    gate,
                    results,
                    runtime_revision_v2(),
                    permit(number, mode=mode).model_dump_json(by_alias=True),
                    "process-owner-" + str(number),
                ),
            )
            processes.append(process)
        try:
            for process in processes:
                process.start()
            observed = [results.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(20)
                assert process.exitcode == 0
            assert sorted(result["outcome"] for result in observed) == ["manager_busy", "reserved"]
            from caller_runtime.domain.conversation import TestAuthority

            authority = TestAuthority.model_validate_json(state["document"])
            assert len(authority.slots) == 1 and len(authority.consumed_nonces) == 2
            assert authority.slots[0].actor_key == permit(1).actor_key
            assert authority.slots[0].attempted_through == 0 and state["version"] >= 3
            assert len({result["pid"] for result in observed}) == 2
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(5)
                assert not process.is_alive()


@pytest.mark.parametrize("case", ["pressure", "stale-generation"])
def test_owned_tls_speech_pressure_and_retired_audio_never_relabel(
    owned_tls_runtime, monkeypatch, case
):
    import asyncio
    import base64
    import json
    import struct

    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    url, context, service, requests, assertion = owned_tls_runtime
    service.mode = "speech"

    async def inject(socket, pcm):
        raw = json.dumps(
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(pcm).decode(),
                "start_ms": 0,
                "end_ms": len(pcm) / 48,
            }
        )
        await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(socket.events.put(raw), service.loop)
        )

    async def scenario():
        async with connect(
            url,
            ssl=context,
            proxy=None,
            compression=None,
            max_size=8192,
            max_queue=1,
            write_limit=12000,
            open_timeout=3,
            subprotocols=["caller.test.v1"],
            additional_headers={"Authorization": "Bearer synthetic-management-workload"},
        ) as socket:
            await socket.send(
                json.dumps(
                    {
                        "type": "authenticate",
                        "permit": "synthetic-signed-test-permit",
                        "requestId": assertion.request_id,
                    }
                )
            )
            ready = json.loads(await asyncio.wait_for(socket.recv(), 3))
            assert ready["mode"] == "speech"
            assert json.loads(await asyncio.wait_for(socket.recv(), 3))["type"] == "audio.credit"
            if case == "pressure":
                await inject(service.provider_socket, b"\x05\0" * 12000)
                frames = []
                while len(frames) < 25:
                    message = await asyncio.wait_for(socket.recv(), 3)
                    if isinstance(message, bytes):
                        frames.append(message)
                    else:
                        assert json.loads(message)["type"] == "audio.credit"
                assert sum(len(frame) - 9 for frame in frames) == 24000
                await inject(service.provider_socket, b"\x06\0" * 480)
                with pytest.raises(ConnectionClosed) as failure:
                    while True:
                        message = await asyncio.wait_for(socket.recv(), 3)
                        assert not isinstance(message, bytes)
                assert failure.value.rcvd.code == 1013
            else:
                old = service.provider_socket
                created = []

                async def connect_next(url, **options):
                    assert old.closed == 1 and service.segment.protocol.closed
                    fresh = type(old)()
                    created.append(fresh)
                    return fresh

                monkeypatch.setattr(service.segment.adapter, "_factory", connect_next)
                await socket.send(
                    json.dumps(
                        {
                            "type": "output.interrupt",
                            "sequence": 1,
                            "sessionId": ready["sessionId"],
                            "generation": ready["generation"],
                        }
                    )
                )
                changed = ready["generation"]
                while True:
                    event = json.loads(await asyncio.wait_for(socket.recv(), 3))
                    if event["type"] == "generation.changed":
                        changed = event["generation"]
                    if event["type"] == "command.ack":
                        break
                assert changed > ready["generation"] and len(created) == 1
                await inject(old, b"\x07\0" * 480)
                pcm = b"\x08\0" * 480
                await inject(created[0], pcm)
                while True:
                    message = await asyncio.wait_for(socket.recv(), 3)
                    if isinstance(message, bytes):
                        assert struct.unpack(">BII", message[:9]) == (1, changed, 1)
                        assert message[9:] == pcm
                        break
                    assert json.loads(message)["type"] == "audio.credit"
                await socket.send(
                    json.dumps({"type": "stop", "sequence": 2, "sessionId": ready["sessionId"]})
                )
                while json.loads(await asyncio.wait_for(socket.recv(), 3))["type"] != "ended":
                    pass
        assert not requests

    asyncio.run(scenario())
    assert service.controller.retained_bytes == 0


def test_default_runtime_has_no_ambient_browser_conversation_socket():
    from caller_runtime.main import create_app

    with TestClient(create_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/v1/internal/test-sessions", subprotocols=["caller.test.v1"]
            ):
                pytest.fail("default_runtime_allocated_test_socket")


def test_owned_socket_rejects_unverified_permit_before_admission():
    from caller_runtime.api.test_sessions import create_router
    from caller_runtime.observability import Telemetry

    class Identity:
        calls = 0

        async def require_runtime(self, request):
            self.calls += 1
            if request.headers.get("authorization") != "Bearer synthetic-management-workload":
                raise HTTPException(401, "synthetic-private-identity-details")
            return "management-workload-fixture"

    class Permits:
        calls = 0

        async def verify(self, raw):
            self.calls += 1
            raise PermissionError("synthetic-private-permit-details")

    identity, permits = Identity(), Permits()
    service = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("must_not_admit")))
    telemetry = Telemetry("caller-runtime", "local")
    application = FastAPI()
    application.include_router(
        create_router(
            service=service,
            workload_identity=identity,
            permits=permits,
            telemetry=telemetry,
        )
    )
    try:
        with TestClient(application) as client:
            with client.websocket_connect(
                SOCKET_URL,
                subprotocols=["caller.test.v1"],
                headers={"Authorization": "Bearer synthetic-management-workload"},
            ) as socket:
                socket.send_json(
                    {
                        "type": "authenticate",
                        "permit": "untrusted-fixture",
                        "requestId": "request-fixture",
                    }
                )
                with pytest.raises(WebSocketDisconnect) as failure:
                    socket.receive_json()
                assert failure.value.code == 1008
                assert "synthetic-private" not in failure.value.reason
        service.start.assert_not_awaited()
        assert identity.calls == permits.calls == 1
        assert "synthetic-private" not in repr(telemetry.events)
    finally:
        telemetry.close()
