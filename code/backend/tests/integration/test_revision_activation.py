"""Ordinary refresh affects new calls, never an existing call's SDK handover revision."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def activation_case(revision_document):
    from caller_runtime.adapters.configuration_client import ConfigurationClient
    from caller_runtime.domain.configuration import RevisionCache
    from caller_runtime.services.configuration_activation import ConfigurationActivation

    class Source:
        document = revision_document()
        etag = '"active-one"'
        calls = 0
        unavailable = False

        async def send(self, request):
            self.calls += 1
            assert request.url == "https://management.example.invalid/v2/runtime/configuration"
            assert request.headers["authorization"] == "Bearer synthetic-runtime-token"
            if self.unavailable:
                return httpx.Response(503)
            if request.headers.get("if-none-match") == self.etag:
                return httpx.Response(304, headers={"ETag": self.etag})
            return httpx.Response(200, json=self.document, headers={"ETag": self.etag})

    class Credential:
        async def get_token(self, scope):
            assert scope == "api://00000000-0000-4000-8000-000000000001/.default"
            return SimpleNamespace(token="synthetic-runtime-token", expires_on=time.time() + 3600)

    source, clock = Source(), [0.0]
    cache = RevisionCache(monotonic=lambda: clock[0])
    client = ConfigurationClient(
        endpoint="https://management.example.invalid",
        audience="00000000-0000-4000-8000-000000000001",
        credential=Credential(),
        cache=cache,
        transport=httpx.MockTransport(source.send),
        monotonic=lambda: clock[0],
    )
    return source, cache, clock, client, ConfigurationActivation(cache=cache)


async def test_twenty_valid_changes_reach_new_calls_within_refresh_window_without_mutating_pins(
    activation_case, revision_document
):
    source, cache, clock, client, activation = activation_case
    assert await client.refresh() is True
    original = activation.select_new_call()
    original_view = original.view_for("a-source")
    for number in range(2, 22):
        source.document, source.etag = revision_document(number), f'"active-{number}"'
        before = source.calls
        clock[0] += 29
        assert await client.refresh() is True
        assert source.calls == before
        clock[0] += 1
        assert await client.refresh() is True
        selected = activation.select_new_call()
        assert selected.revision_id == source.document["id"]
        assert selected.view_for("a-source")["agent"]["prompt"].endswith(f"revision {number}")
        assert original.view_for("a-source") == original_view
        assert original.revision_id == "revision-fixture-1"


async def test_existing_sdk_handover_keeps_original_revision_after_current_pointer_advances(
    activation_case, revision_document, handover_host
):
    source, cache, clock, client, activation = activation_case
    assert await client.refresh()
    pinned = activation.select_new_call()
    async with handover_host(configuration=pinned.for_runtime()) as (
        coordinator,
        repository,
        phone,
        models,
        telemetry,
    ):
        source.document, source.etag = revision_document(2), '"active-two"'
        clock[0] += 30
        assert await client.refresh()
        assert activation.select_new_call().revision_id == "revision-fixture-2"
        assert (
            await coordinator.transfer("session-us3-fixture", "a-peer", context={})
        ).outcome == "committed"
        current = await repository.read("session-us3-fixture")
        assert current["configurationRevisionId"] == pinned.revision_id
        assert current["physicalOwnerId"] == "physical-us3-owner"
        assert phone.hangups == 0


async def test_outage_uses_only_last_validated_snapshot_and_empty_restart_declines_new_calls(
    activation_case,
):
    from caller_runtime.domain.configuration import RevisionCache
    from caller_runtime.services.configuration_activation import ConfigurationActivation

    source, cache, clock, client, activation = activation_case
    source.unavailable = True
    assert await client.refresh() is False
    with pytest.raises(PermissionError, match="configuration_unavailable"):
        activation.select_new_call()
    source.unavailable = False
    clock[0] += 30
    assert await client.refresh()
    pinned = activation.select_new_call()
    source.unavailable = True
    clock[0] += 30
    assert await client.refresh() is False
    assert cache.current is pinned
    with pytest.raises(PermissionError, match="configuration_unavailable"):
        activation.select_new_call()
    assert pinned.view_for("a-source")["agent"]["agentId"] == "a-source"
    with pytest.raises(PermissionError):
        ConfigurationActivation(cache=RevisionCache()).select_new_call()


async def test_actual_lifecycle_admission_stores_the_selected_immutable_revision(
    activation_case, revision_document
):
    from caller_runtime.adapters.session_repository import LocalSessionRepository

    source, cache, clock, client, activation = activation_case
    repository = LocalSessionRepository()
    assert await client.refresh()
    first = await activation.admit(
        session_id="call-first",
        provider_call_ref="phone-first",
        event_id="event-first",
        repository=repository,
        agents=object(),
        phone=object(),
    )
    assert first is not None
    source.document, source.etag = revision_document(2), '"active-two"'
    clock[0] += 30
    assert await client.refresh()
    second = await activation.admit(
        session_id="call-second",
        provider_call_ref="phone-second",
        event_id="event-second",
        repository=repository,
        agents=object(),
        phone=object(),
    )
    assert second is not None and second.lifecycle is not first.lifecycle
    assert (await repository.read("call-first"))[
        "configurationRevisionId"
    ] == first.snapshot.revision_id
    assert (await repository.read("call-second"))[
        "configurationRevisionId"
    ] == second.snapshot.revision_id
    assert first.snapshot.revision_id == "revision-fixture-1"
    assert second.snapshot.revision_id == "revision-fixture-2"


async def test_current_revocation_blocks_next_real_sdk_action_even_while_revision_stays_pinned(
    activation_case, revision_document
):
    from agents import RunContextWrapper

    from caller_runtime.adapters.sdk_tools import SdkTools, ToolBinding

    source, cache, clock, client, activation = activation_case
    assert await client.refresh()
    pinned = activation.select_new_call()
    binding = ToolBinding(
        session_id="session-pinned",
        agent_id="b-entry",
        agent_epoch=1,
        revision_id=pinned.revision_id,
    )

    class Gateway:
        revoked = False
        effects = 0
        calls = []

        async def execute(self, document, *, deadline):
            self.calls.append(document)
            if self.revoked:
                raise PermissionError("synthetic-private-revocation-details")
            self.effects += 1
            return {
                key: document[key] for key in ("requestId", "sessionId", "agentId", "agentEpoch")
            } | {"result": {"name": "Synthetic public result"}}

    async def current(candidate):
        return candidate == binding

    gateway = Gateway()
    tools = SdkTools(
        client=gateway,
        binding_for=lambda session, agent, revision: binding,
        is_current=current,
        catalog=[
            {
                "toolId": "beta-tool",
                "actionId": "public-lookup",
                "description": "Synthetic lookup",
                "inputSchema": {"type": "object"},
            }
        ],
    )
    projection = next(
        agent for agent in pinned.for_runtime()["agents"] if agent["agentId"] == "b-entry"
    )
    context = {"session_id": binding.session_id, "revision_id": binding.revision_id}
    tool = tools.tools_for(projection, context)[0]
    assert (
        json.loads(await tool.on_invoke_tool(RunContextWrapper(context=context), "{}"))["name"]
        == "Synthetic public result"
    )
    source.document, source.etag = revision_document(2), '"active-two"'
    clock[0] += 30
    assert await client.refresh()
    gateway.revoked = True
    assert "error" in json.loads(
        await tool.on_invoke_tool(RunContextWrapper(context=context), "{}")
    )
    assert gateway.effects == 1 and len(gateway.calls) == 2
    assert all(document["pinnedRevisionId"] == pinned.revision_id for document in gateway.calls)
    assert pinned.revision_id != activation.select_new_call().revision_id


async def test_explicit_runtime_lifespan_owns_refresh_worker_and_cleans_it_up(activation_case):
    from caller_runtime.main import create_app
    from caller_runtime.services.configuration_activation import ConfigurationActivation

    source, cache, clock, client, activation = activation_case
    activation = ConfigurationActivation(cache=cache, client=client)
    application = create_app(configuration_activation=activation)
    before = set(asyncio.all_tasks())
    async with application.router.lifespan_context(application):
        assert application.state.configuration_activation is activation
        assert activation.select_new_call().revision_id == "revision-fixture-1"
        assert source.calls == 1
    assert not (set(asyncio.all_tasks()) - before)


async def test_unqualified_live_startup_never_enters_configuration_scope(activation_case):
    from caller_runtime.main import create_app
    from caller_runtime.settings import Settings

    class ForbiddenActivation:
        def running(self):
            pytest.fail("live_mode_constructed_configuration_scope")

    application = create_app(Settings(mode="live"), configuration_activation=ForbiddenActivation())
    with pytest.raises(RuntimeError, match="live_mode_unavailable"):
        async with application.router.lifespan_context(application):
            pass
