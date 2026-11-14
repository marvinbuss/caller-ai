"""Fast phase-aware conversation deadlines; no real-minute sleeps or model calls."""

import asyncio
import json
from datetime import timedelta

import pytest

from caller_runtime.services import modality_defaults
from tests.doubles import model_capped, model_reply, model_stream
from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store
from tests.unit.test_admission import live_case as live_case


def test_shared_policy_routes_initial_entry_but_rejects_missing_agent_edge(admission_case):
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        policy = ConversationPolicy(slot=slot, snapshot=cache.pin(), admission=service)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        work = WorkBudget.accept(limits, clock.utc_now())
        assert policy.active_agent_id == "welcome"
        await policy.enter_routing(work)
        await policy.route("alpha", work)
        assert policy.active_agent_id == "a-entry" and policy.active_community_id == "alpha"
        previous = policy.agent_epoch
        with pytest.raises(PermissionError, match="handover_not_allowed"):
            await policy.route_to_peer(
                "a-source", work, source_agent_id="a-entry", source_agent_epoch=previous
            )
        assert policy.active_agent_id == "a-entry" and policy.agent_epoch == previous

    asyncio.run(scenario())


def test_shared_policy_keeps_source_through_routing_and_denies_stale_epochs(admission_case):
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        policy = ConversationPolicy(slot=slot, snapshot=cache.pin(), admission=service)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        work = WorkBudget.accept(limits, clock.utc_now())
        assert policy.routing_directory() is None
        await policy.enter_routing(work)
        directory = policy.routing_directory()
        assert directory is not None
        assert [entry["communityId"] for entry in json.loads(directory.split("\n", 1)[1])] == [
            "alpha",
            "beta",
        ]
        await policy.route("alpha", work)
        assert policy.routing_directory() is None
        await policy.transfer_community("beta", work)
        assert policy.active_agent_id == "routing" and policy.active_community_id == "alpha"
        # A transfer may only complete to its pending destination, so only that one is listed.
        directory = policy.routing_directory()
        assert directory is not None
        assert json.loads(directory.split("\n", 1)[1]) == [
            {"communityId": "beta", "name": "Beta", "routingDescription": "Synthetic beta routing"}
        ]
        reconstructed = ConversationPolicy(
            slot=policy.slot, snapshot=cache.pin(), admission=service
        )
        with pytest.raises(PermissionError, match="handover_not_allowed"):
            await reconstructed.route("beta", work)
        with pytest.raises(PermissionError):
            await policy.route("alpha", work)
        await policy.route("beta", work)
        assert policy.active_agent_id == "b-entry" and policy.active_community_id == "beta"
        old_epoch = policy.agent_epoch
        await policy.route_to_peer(
            "b-peer", work, source_agent_id="b-entry", source_agent_epoch=old_epoch
        )
        assert policy.active_agent_id == "b-peer"
        with pytest.raises(PermissionError, match="stale_agent_epoch"):
            await policy.route_to_peer(
                "b-entry", work, source_agent_id="b-entry", source_agent_epoch=old_epoch
            )
        with pytest.raises(PermissionError):
            await policy.transfer_community("alpha", work)
        await policy.goodbye(work)
        assert policy.active_agent_id == "goodbye"
        with pytest.raises(PermissionError):
            await policy.enter_routing(work)

    asyncio.run(scenario())


def test_speech_policy_requires_confirmed_segment_retirement_before_owner_changes(admission_case):
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1, mode="speech"))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        policy = ConversationPolicy(slot=slot, snapshot=cache.pin(), admission=service)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        with pytest.raises(PermissionError, match="speech_transition_unavailable"):
            await policy.enter_routing(WorkBudget.accept(limits, clock.utc_now()))
        assert policy.active_agent_id == "welcome"

    asyncio.run(scenario())


def test_speech_policy_requires_real_bound_segment_settlement_not_a_noop_hook(live_case):
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, clock, socket, opened = live_case

    async def scenario():
        slot, segment = await opened()
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        work = WorkBudget.accept(limits, clock.utc_now())

        async def pretend_retired(previous, budget):
            return None

        policy = ConversationPolicy(
            slot=slot,
            snapshot=service.configuration.pin(),
            admission=service,
            retire_speech=pretend_retired,
        )
        with pytest.raises(PermissionError, match="live_segment_not_closed"):
            await policy.enter_routing(work)
        assert policy.active_agent_id == "welcome" and policy.agent_epoch == slot.agent_epoch

        async def retire(previous, budget):
            await segment.close()

        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service, retire_speech=retire
        )
        await policy.enter_routing(work)
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert (
            observed.active_agent_id == "routing" and observed.agent_epoch == slot.agent_epoch + 1
        )
        assert observed.closure_receipt is not None and observed.resources["live"].settled == 1
        assert socket.closed == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome", ["success", "revoked", "unknown", "stopped", "late", "redirect"]
)
def test_capability_dispatch_is_bound_intent_gated_and_never_replayed(admission_case, outcome):
    from types import SimpleNamespace

    import httpx

    from caller_runtime.adapters.capability_client import ConversationCapabilityClient
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget
    from caller_runtime.services.cleanup import CleanupObservations
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, container, clock, permit, cache = admission_case
    requests, scopes = [], []

    class Credentials:
        async def get_token(self, scope):
            scopes.append(scope)
            if outcome == "stopped":
                await service.mark_terminal(policy.slot.session_id, policy.slot.owner_epoch)
            return SimpleNamespace(
                token="synthetic-token", expires_on=clock.utc_now().timestamp() + 900
            )

    async def dispatch(request):
        document = json.loads(request.content)
        requests.append(document)
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.resources["capability"].attempt is not None
        assert document["agentId"] == "b-entry" and document["agentEpoch"] == policy.agent_epoch
        assert document["singleAttempt"] is True
        assert document["deadlineAt"] == work.work_deadline_utc.isoformat()
        assert request.url == "https://management.fixture.example/v1/runtime/capabilities/execute"
        if outcome == "unknown":
            raise httpx.ReadError("synthetic-unknown")
        if outcome == "redirect":
            return httpx.Response(
                302,
                text="synthetic-private-error-payload",
                headers={"Location": "https://foreign.example.invalid/"},
            )
        if outcome == "revoked":
            return httpx.Response(403, json={"code": "capability_not_allowed"})
        if outcome == "late":
            clock.advance(11)
        return httpx.Response(
            200,
            json={key: document[key] for key in ("requestId", "sessionId", "agentId", "agentEpoch")}
            | {"result": {"name": "Synthetic public result"}},
        )

    async def scenario():
        nonlocal policy, work
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        policy = ConversationPolicy(slot=slot, snapshot=cache.pin(), admission=service)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        initial = WorkBudget.accept(limits, clock.utc_now())
        await policy.enter_routing(initial)
        await policy.route("beta", initial)
        clock.advance(60)
        work = WorkBudget.accept(limits, clock.utc_now())
        observations = CleanupObservations(owner_id=service.owner_id, utc_now=clock.utc_now)

        async def disposed():
            return True

        observations.register(policy.slot, disposed=disposed)
        client = ConversationCapabilityClient(
            endpoint="https://management.fixture.example",
            approved_hosts=frozenset({"management.fixture.example"}),
            audience="11111111-2222-4333-8444-555555555555",
            credential=Credentials(),
            admission=service,
            utc_now=clock.utc_now,
            transport=httpx.MockTransport(dispatch),
            settlement_observer=observations,
        )
        if outcome == "success":
            result = await policy.capability(client, "beta-tool", "public-lookup", {}, work)
            assert result == {"name": "Synthetic public result"}
        else:
            with pytest.raises((PermissionError, RuntimeError)) as failure:
                await policy.capability(client, "beta-tool", "public-lookup", {}, work)
            assert "synthetic-private-error-payload" not in str(failure.value)
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        progress = observed.resources["capability"]
        assert len(requests) == (0 if outcome == "stopped" else 1)
        assert progress.attempted == (0 if outcome == "stopped" else 1)
        if outcome in {"success", "revoked", "late"}:
            assert progress.settled == progress.attempted
        elif outcome in {"unknown", "redirect"}:
            assert progress.settled == 0
            with pytest.raises((PermissionError, RuntimeError)):
                await policy.capability(client, "beta-tool", "public-lookup", {}, work)
            assert len(requests) == 1
        assert scopes == ["api://11111111-2222-4333-8444-555555555555/.default"]
        assert observed.resources["live"].attempted == 0
        terminal = await service.mark_terminal(policy.slot.session_id, policy.slot.owner_epoch)
        proof = await observations.observe(terminal, asyncio.get_running_loop().time() + 1)
        assert (proof is not None) == (outcome not in {"unknown", "redirect"})

    policy = work = None
    asyncio.run(scenario())


@pytest.mark.parametrize(
    "tool_id,action_id,inputs",
    [
        ("foreign-tool", "public-lookup", {}),
        ("beta-tool", "foreign-action", {}),
        ("beta-tool", "public-lookup", {"oversized": "x" * (64 * 1024)}),
    ],
    ids=["foreign-tool", "foreign-action", "oversized-input"],
)
def test_capability_invalid_input_never_requests_credentials_or_records_intent(
    admission_case, tool_id, action_id, inputs
):
    from caller_runtime.adapters.capability_client import ConversationCapabilityClient
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, container, clock, permit, cache = admission_case

    class Credentials:
        async def get_token(self, scope):
            raise AssertionError("invalid_input_requested_credentials")

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        policy = ConversationPolicy(slot=slot, snapshot=cache.pin(), admission=service)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        work = WorkBudget.accept(limits, clock.utc_now())
        await policy.enter_routing(work)
        await policy.route("beta", work)
        client = ConversationCapabilityClient(
            endpoint="https://management.fixture.example",
            approved_hosts=frozenset({"management.fixture.example"}),
            audience="11111111-2222-4333-8444-555555555555",
            credential=Credentials(),
            admission=service,
            utc_now=clock.utc_now,
        )
        with pytest.raises(PermissionError):
            await policy.capability(client, tool_id, action_id, inputs, work)
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.attempted_through == 0

    asyncio.run(scenario())


def test_work_queue_keeps_acceptance_deadline_duplicates_and_one_pending(admission_case):
    from caller_runtime.services.delegation import DelegationQueue

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        slot = await service.mark_ready(slot.session_id, slot.owner_epoch)
        clock.advance(60)
        queue = DelegationQueue()
        first = queue.accept(slot, task_id="text-1", text="Synthetic first", now=clock.utc_now())
        assert (
            not first.duplicate
            and first.receipt.work.work_deadline_utc == clock.utc_now() + timedelta(seconds=10)
        )
        executing = queue.take(now=clock.utc_now())
        clock.advance(3)
        second = queue.accept(slot, task_id="text-2", text="Synthetic pending", now=clock.utc_now())
        repeated = queue.accept(
            slot, task_id="text-1", text="Changed duplicate is not executed", now=clock.utc_now()
        )
        assert repeated.duplicate and repeated.receipt == first.receipt
        with pytest.raises(PermissionError, match="work_queue_full"):
            queue.accept(slot, task_id="text-3", text="Synthetic overflow", now=clock.utc_now())
        for _ in range(8):
            executing.step(clock.utc_now())
        with pytest.raises(PermissionError, match="work_step_limit"):
            executing.step(clock.utc_now())
        queue.finish(first.receipt)
        clock.advance(10)
        with pytest.raises(PermissionError, match="work_deadline_exceeded"):
            queue.take(now=clock.utc_now())
        assert second.receipt.work.work_deadline_utc <= clock.utc_now()
        assert queue.take(now=clock.utc_now()) is None
        assert queue.accept(
            slot, task_id="text-2", text="Duplicate expired", now=clock.utc_now()
        ).duplicate
        queue.close()
        with pytest.raises(PermissionError, match="work_queue_closed"):
            queue.accept(slot, task_id="text-new", text="After close", now=clock.utc_now())

    asyncio.run(scenario())


def test_delegation_queue_correlates_actual_segment_and_requires_clear_task(live_case):
    from caller_runtime.services.delegation import DelegationQueue

    service, repository, clock, socket, opened = live_case

    async def scenario():
        slot, segment = await opened()
        queue = DelegationQueue()
        slot = await service.check_output(
            slot.session_id, slot.owner_epoch, generation=slot.generation
        )
        with pytest.raises(PermissionError, match="stale_generation"):
            queue.accept(
                slot,
                task_id="speech-foreign",
                text=None,
                now=clock.utc_now(),
                provider_session_id="foreign-segment",
                delegation_id="delegation-1",
            )
        accepted = queue.accept(
            slot,
            task_id="speech-1",
            text=None,
            now=clock.utc_now(),
            provider_session_id=segment.provider_session_id,
            delegation_id="delegation-1",
        )
        item = queue.take(now=clock.utc_now())
        assert item.clarification_required and item.text is None
        queue.finish(accepted.receipt)
        repeated = queue.accept(
            slot,
            task_id="speech-other-task",
            text="Guessed task",
            now=clock.utc_now(),
            provider_session_id=segment.provider_session_id,
            delegation_id="delegation-1",
        )
        assert repeated.duplicate and repeated.receipt == accepted.receipt
        assert queue.take(now=clock.utc_now()) is None
        await segment.close()

    asyncio.run(scenario())


@pytest.fixture
def text_execution_case(admission_case):
    from types import SimpleNamespace

    import httpx2

    from caller_runtime.adapters.foundry_text import FoundryTextAdapter
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, container, clock, permit, cache = admission_case
    entered, release = asyncio.Event(), asyncio.Event()
    controls = {"blocked": False, "oversized": False}
    requests, messages = [], []

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-text-token", expires_on=clock.utc_now().timestamp() + 900
            )

    class Chunks(httpx2.AsyncByteStream):
        def __init__(self):
            self.action = controls["wire_actions"].pop(0) if controls.get("wire_actions") else None
            if self.action is not None:
                controls.setdefault("wire_action_observations", []).append(
                    (len(requests), self.action[0])
                )

        async def __aiter__(self):
            text = (
                "x" * 65537
                if controls["oversized"]
                else controls.get("reply", "Synthetic shared streamed reply")
            )
            call = None
            if self.action is not None:
                call_name, arguments = self.action
                call = (call_name, arguments)
            elif controls.get("initial_flow") and len(requests) <= 2:
                name = "enter_routing" if len(requests) == 1 else "route_community"
                arguments = {} if len(requests) == 1 else {"community_id": "beta"}
                call = (name, arguments)
            elif controls.get("handover") and len(requests) == 1:
                arguments = {
                    "agent_id": "b-peer",
                    "request_summary": "Synthetic approved summary",
                    "transfer_reason": "Synthetic directed transfer",
                }
                call = ("handover_agent", arguments)
            for chunk in model_stream(
                "Synthetic greeting that the live model streams before it routes"
                if call and controls.get("text_with_call")
                else None
                if call
                else text,
                call,
            ):
                yield chunk

    async def dispatch(request):
        body = json.loads(request.content)
        if body.get("max_output_tokens") == 256:
            # Greeting requests are kept apart so dispatch counts stay those of the work.
            controls.setdefault("greeting_requests", []).append(body)
            if controls.get("greeting_delays"):
                # Load tests draw Welcome-agent latency from a fixed list, one per request.
                delay = controls["greeting_delays"].pop(0)
                controls["greeting_in_flight"] = controls.get("greeting_in_flight", 0) + 1
                controls["greeting_peak"] = max(
                    controls.get("greeting_peak", 0), controls["greeting_in_flight"]
                )
                try:
                    await asyncio.sleep(delay)
                finally:
                    controls["greeting_in_flight"] -= 1
            if controls.get("greeting_block"):
                entered.set()
                await release.wait()
            if "greeting" not in controls or controls.get("greeting_status"):
                return httpx2.Response(
                    controls.get("greeting_status", 400),
                    json={"error": {"message": "synthetic-private-provider-response"}},
                )
            return httpx2.Response(200, json=model_reply(controls["greeting"]))
        requests.append(body)
        if controls.get("prepare") and not body.get("stream"):
            assert not body.get("tools") and body["store"] is False
            if controls.get("prepare_error"):
                status = controls.get("prepare_error_status", 503)
                controls.setdefault("prepare_statuses", []).append(status)
                return httpx2.Response(
                    status, json={"error": {"message": "synthetic-private-provider-response"}}
                )
            if controls.get("prepare_late"):
                clock.advance(11)
            return httpx2.Response(
                200, json=model_capped(controls.get("prepare_reason", "max_output_tokens"))
            )
        if "clarity" in controls and not body.get("stream"):
            assert body["text"]["format"]["type"] == "json_schema"
            assert not body.get("tools") and body["store"] is False
            controls.setdefault("timeouts", []).append(request.extensions["timeout"])
            clock.advance(4)
            return httpx2.Response(
                200, json=model_reply(json.dumps({"unambiguous": controls["clarity"]}))
            )
        assert body["stream"] is True and body["store"] is False
        if "clarity" in controls:
            controls.setdefault("timeouts", []).append(request.extensions["timeout"])
        entered.set()
        if controls["blocked"]:
            await release.wait()
        return httpx2.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Chunks())

    adapter = FoundryTextAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        deployment="reasoning-fixture",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        transport=httpx2.MockTransport(dispatch),
        max_input_tokens=16384,
        max_output_tokens=512,
    )

    async def setup():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        slot = await service.mark_ready(slot.session_id, slot.owner_epoch)
        policy = ConversationPolicy(slot=slot, snapshot=cache.pin(), admission=service)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        work = WorkBudget.accept(limits, clock.utc_now())
        if not controls.get("initial_flow"):
            await policy.enter_routing(work)
            await policy.route("beta", work)
        adapter._telemetry = controls.get("telemetry")
        return ConversationExecutor(
            policy=policy,
            text_adapter=adapter,
            utc_now=clock.utc_now,
            cleanup_observations=controls.get("observations"),
            telemetry=controls.get("telemetry"),
        )

    async def emit(message):
        messages.append(message)

    return setup, emit, messages, requests, controls, entered, release, adapter


@pytest.mark.parametrize("oversized", [False, True])
def test_text_executor_streams_real_sdk_reply_without_live_and_bounds_complete_reply(
    text_execution_case, admission_case, oversized
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        controller = await setup()
        controls["oversized"] = oversized
        clock.advance(60)
        accepted = await controller.submit_text("text-1", "Synthetic user question")
        assert accepted.receipt.work.work_deadline_utc == clock.utc_now() + timedelta(seconds=10)
        await controller.process_next(emit=emit)
        assert len(requests) == 1
        expected = modality_defaults.TEXT_PLATFORM_RULES + "\n\nSynthetic prompt for b-entry"
        assert requests[0]["instructions"] == expected
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.resources["live"].attempted == 0
        if oversized:
            assert not any(message["type"] == "text.completed" for message in messages)
            assert messages[-1]["type"] == "error"
        else:
            assert (
                "".join(message["text"] for message in messages if message["type"] == "text.delta")
                == "Synthetic shared streamed reply"
            )
            assert messages[-1]["type"] == "text.completed"
            assert observed.resources["model"].attempted == observed.resources["model"].settled == 1
            old_idle = observed.idle_expires_at
            clock.advance(1)
            assert (await controller.submit_text("text-1", "Repeated text")).duplicate
            with pytest.raises(PermissionError, match="invalid_work_input"):
                await controller.submit_text("text-invalid", "\u00e9" * 2049)
            observed = (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots[0]
            assert observed.idle_expires_at == old_idle
            assert not await controller.process_next(emit=emit)
        await adapter.close()

    asyncio.run(scenario())


def test_text_executor_has_one_pending_input_and_busy_does_not_refresh_idle(
    text_execution_case, admission_case
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        controller = await setup()
        controls["blocked"] = True
        await controller.submit_text("text-1", "Synthetic first")
        processing = asyncio.create_task(controller.process_next(emit=emit))
        await asyncio.wait_for(entered.wait(), 1)
        clock.advance(1)
        second = await controller.submit_text("text-2", "Synthetic second")
        idle = (
            (await repository.read(deadline=asyncio.get_running_loop().time() + 1))
            .authority.slots[0]
            .idle_expires_at
        )
        clock.advance(1)
        with pytest.raises(PermissionError, match="work_queue_full"):
            await controller.submit_text("text-3", "Synthetic excess")
        assert (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0].idle_expires_at == idle
        release.set()
        await processing
        assert await controller.process_next(emit=emit)
        assert len(requests) == 2 and second.receipt.work.work_accepted_at < clock.utc_now()
        await adapter.close()

    asyncio.run(scenario())


def test_text_executor_applies_directed_tool_handover_with_filtered_context(text_execution_case):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        controller = await setup()
        controls["handover"] = True
        original_epoch = controller.policy.agent_epoch
        await controller.submit_text("text-handover", "Synthetic original source-only detail")
        await controller.process_next(emit=emit)
        assert controller.policy.active_agent_id == "b-peer"
        assert controller.policy.agent_epoch == original_epoch + 1 and len(requests) == 2
        expected = modality_defaults.TEXT_PLATFORM_RULES + "\n\nSynthetic prompt for b-peer"
        assert requests[1]["instructions"] == expected
        target_context = json.dumps(requests[1]["input"])
        assert "Synthetic approved summary" in target_context
        assert "Synthetic original source-only detail" not in target_context
        assert "Synthetic prompt for b-entry" not in target_context
        assert any(message["type"] == "agent.changed" for message in messages)
        assert messages[-1]["type"] == "text.completed"
        await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("clear", [False, True])
def test_speech_delegation_uses_shared_executor_and_exact_id_without_guessing(
    live_case, text_execution_case, clear
):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy
    from caller_runtime.services.modality_defaults import VOICE_PLATFORM_RULES

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now
        )
        await socket.events.put(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-bound",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 10,
                }
            )
        )
        event = await segment.next_event()
        accepted = await controller.submit_delegation(
            segment,
            event,
            task_text="Synthetic correlated task" if clear else None,
            unambiguous=clear,
        )
        await controller.process_next(emit=emit)
        assert len(requests) == int(clear)
        appended = [event for event in socket.sent if event["type"] == "session.commentary.append"]
        assert len(appended) == 1 and appended[0]["delegation_id"] == "delegation-bound"
        assert len(appended[0]["content"].encode("utf-8")) <= 500
        if clear:
            assert appended[0]["content"] == "Synthetic shared streamed reply"
            assert (
                requests[0]["instructions"] == VOICE_PLATFORM_RULES + "\n\nSynthetic welcome prompt"
            )
        else:
            assert "clarify" in appended[0]["content"].lower()
        assert not any(event["type"].startswith("text.") for event in messages)
        assert (
            await controller.submit_delegation(
                segment, event, task_text="Duplicate guessed work", unambiguous=True
            )
        ).duplicate
        assert not await controller.process_next(emit=emit)
        assert accepted.receipt.delegation_id == "delegation-bound"
        await segment.close()
        await adapter.close()

    asyncio.run(scenario())


LONG_SPOKEN_REPLY = "This spoken sentence carries one part of a long answer to the caller. " * 30


@pytest.mark.parametrize(
    "scenario_name", ["acknowledged", "unacknowledged", "interrupted", "spent"]
)
def test_long_speech_reply_is_delivered_in_paced_parts(
    live_case, text_execution_case, monkeypatch, scenario_name
):
    from caller_runtime.observability import Telemetry
    from caller_runtime.services import conversation
    from caller_runtime.services.conversation import WORK_FAILED_LINE, ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy
    from caller_runtime.services.speech_parts import QUIET_HANDOVER

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    telemetry = Telemetry("caller-runtime", "local")
    controls["reply"] = LONG_SPOKEN_REPLY
    if scenario_name == "unacknowledged":
        monkeypatch.setattr(conversation, "PART_ACK_SECONDS", 0.05)
    original_send = socket.send

    async def send(raw):
        await original_send(raw)
        event = json.loads(raw)
        if event["type"] != "session.commentary.append" or event.get("delegation_id") is None:
            return
        if scenario_name == "acknowledged" and "event_id" in event:
            await socket.events.put(
                json.dumps(
                    {"type": "session.commentary.appended", "client_event_id": event["event_id"]}
                )
            )
        if scenario_name == "spent":
            clock.advance(40)

    socket.send = send

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now, telemetry=telemetry
        )

        async def read():
            while True:
                await segment.next_event()

        await socket.events.put(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-long",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 10,
                }
            )
        )
        event = await segment.next_event()
        reader = asyncio.create_task(read())
        await controller.submit_delegation(
            segment, event, task_text="Synthetic correlated task", unambiguous=True
        )
        work = asyncio.create_task(controller.process_next(emit=emit))
        if scenario_name == "interrupted":
            for _ in range(1000):
                if [e for e in socket.sent if e["type"] == "session.commentary.append"]:
                    break
                await asyncio.sleep(0.01)
            controller.interrupt_speech()
        await asyncio.wait_for(work, 10)
        reader.cancel()
        await asyncio.wait({reader})
        spoken = [e for e in socket.sent if e["type"] == "session.commentary.append"]
        thinking = [e for e in socket.sent if e["type"] == "session.thinking.append"]
        # The first quiet append is the handover update; any after it is the dropped note.
        assert thinking[0]["content"] == QUIET_HANDOVER
        notes = thinking[1:]
        assert all(e["delegation_id"] == "delegation-long" for e in spoken + notes)
        assert all(len(e["content"].encode("utf-8")) <= 500 for e in spoken + notes)
        assert WORK_FAILED_LINE not in json.dumps(spoken)
        if scenario_name in {"acknowledged", "unacknowledged"}:
            assert "".join(e["content"] for e in spoken) == LONG_SPOKEN_REPLY
            assert len(spoken) >= 5 and not notes
            assert all("event_id" in e for e in spoken[:-1]) and "event_id" not in spoken[-1]
        elif scenario_name == "interrupted":
            assert len(spoken) == 1 and 1 <= len(notes) <= 3
            assert notes[0]["content"].startswith("The rest of the earlier answer was not spoken: ")
        else:
            assert len(spoken) == 1 and not notes
        await segment.close()
        await adapter.close()
        return len(spoken)

    try:
        sent = asyncio.run(scenario())
        measured = {
            e["measurement"]: int(e["value"])
            for e in telemetry.events
            if e["event"] == "test.speech_parts"
        }
        assert measured["parts"] >= 5
        assert measured["dropped_parts"] == measured["parts"] - sent
    finally:
        telemetry.close()


def test_spoken_greeting_is_the_first_assistant_history_entry(live_case, text_execution_case):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now
        )
        for start, delta in ((0, "Hello and welcome. "), (100, "How can I help?")):
            await controller.accept_transcript(
                {
                    "type": "session.output_transcript.delta",
                    "delta": delta,
                    "start_ms": start,
                    "end_ms": start + 50,
                },
                generation=1,
                input_audio_accepted=False,
            )
        await socket.events.put(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-after",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 200,
                }
            )
        )
        event = await segment.next_event()
        await controller.submit_delegation(
            segment, event, task_text="Synthetic correlated task", unambiguous=True
        )
        await controller.accept_transcript(
            {
                "type": "session.output_transcript.delta",
                "delta": "Later answer speech",
                "start_ms": 300,
                "end_ms": 350,
            },
            generation=1,
            input_audio_accepted=False,
        )
        await controller.process_next(emit=emit)
        sent = requests[-1]["input"]
        assert sent[0]["role"] == "assistant"
        assert "Hello and welcome. How can I help?" in json.dumps(sent[0])
        assert "Later answer speech" not in json.dumps(sent)
        assert "Synthetic correlated task" in json.dumps(sent[1])
        await segment.close()
        await adapter.close()

    asyncio.run(scenario())


def test_text_executor_runs_initial_welcome_routing_and_exact_entry_on_one_budget(
    text_execution_case,
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        controls["initial_flow"] = True
        controller = await setup()
        accepted = await controller.submit_text("text-initial", "Synthetic beta request")
        await controller.process_next(emit=emit)
        assert controller.policy.active_agent_id == "b-entry" and len(requests) == 3
        directory = (
            "Communities you can route to (JSON). Call `route_community` with one communityId; "
            "never tell the user an id:\n"
            + json.dumps(
                [
                    {
                        "communityId": community,
                        "name": community.title(),
                        "routingDescription": "Synthetic " + community + " routing",
                    }
                    for community in ("alpha", "beta")
                ],
                separators=(",", ":"),
            )
        )
        assert [request["instructions"] for request in requests] == [
            modality_defaults.TEXT_PLATFORM_RULES + "\n\n" + prompt
            for prompt in (
                "Synthetic welcome prompt",
                "Synthetic routing prompt\n\n" + directory,
                "Synthetic prompt for b-entry",
            )
        ]
        assert all("Synthetic beta request" in json.dumps(request["input"]) for request in requests)
        assert controller.queue.lookup("text-initial") == accepted.receipt
        assert messages[-1]["type"] == "text.completed"
        await adapter.close()

    asyncio.run(scenario())


def test_a_reply_that_greets_and_routes_in_one_response_continues_to_the_entry(
    text_execution_case, admission_case, monkeypatch
):
    """The live model answers with text and a function call in the same Responses output, and the
    store answers slowly enough that stream gating overlaps the tool's own transition."""
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case
    read = container.read_item

    async def slow_read(*arguments, **keywords):
        await asyncio.sleep(0.02)
        return await read(*arguments, **keywords)

    monkeypatch.setattr(container, "read_item", slow_read)

    async def scenario():
        controls["initial_flow"] = True
        controls["text_with_call"] = True
        controller = await setup()
        await controller.submit_text("text-greeting", "Synthetic beta request")
        await controller.process_next(emit=emit)
        assert not any(message["type"] == "error" for message in messages), messages
        assert controller.policy.active_agent_id == "b-entry" and len(requests) == 3
        assert messages[-1]["type"] == "text.completed"
        await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("confirmed", [True, False])
def test_speech_handover_retires_actual_socket_before_any_continuation(
    live_case, monkeypatch, confirmed
):
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    service, repository, clock, socket, opened = live_case
    created, messages = [], []

    async def emit(message):
        messages.append(message)

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)

        async def connect(url, **options):
            assert socket.closed == 1 and segment.protocol.closed
            observed = (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots[0]
            assert observed.resources["live"].attempted > observed.resources["live"].settled
            # Clients learn the new generation before its credits or audio can reach them.
            assert messages[-1]["type"] == "agent.changed"
            # No capture credit is granted until the new segment has started.
            assert segments.starting
            fresh = type(socket)()
            created.append(fresh)
            return fresh

        monkeypatch.setattr(segment.adapter, "_factory", connect)
        if not confirmed:

            async def fail_receive():
                raise ConnectionError("synthetic-unknown-close")

            socket.close_reply = False
            monkeypatch.setattr(socket, "recv", fail_receive)
        segments = SpeechSegments(
            current=segment,
            instructions_for=lambda current: "Synthetic continuation instructions",
            emit=emit,
            context_for=lambda current: [f"Synthetic note for {current.active_agent_id}"],
        )
        policy = ConversationPolicy(
            slot=slot,
            snapshot=service.configuration.pin(),
            admission=service,
            retire_speech=segments.retire,
            continue_speech=segments.continue_with,
        )
        work = WorkBudget(clock.utc_now(), clock.utc_now() + timedelta(seconds=10))
        if confirmed:
            await policy.enter_routing(work)
            assert policy.active_agent_id == "routing" and len(created) == 1
            # The new segment gets the context note for the new agent as quiet thinking.
            assert [
                (event["delegation_id"], event["content"])
                for event in created[0].sent
                if event["type"] == "session.thinking.append"
            ] == [(None, "Synthetic note for routing")]
            assert segments.current.intent.generation == policy.slot.generation > slot.generation
            assert (
                policy.slot.actor_key == slot.actor_key
                and policy.slot.expires_at == slot.expires_at
            )
            assert policy.slot.revision_id == slot.revision_id
            with pytest.raises(PermissionError):
                await segment.next_event()
            assert messages[0]["type"] == "generation.changed"
            assert messages[1:] == [
                {
                    "type": "agent.changed",
                    "agentId": "routing",
                    "communityId": None,
                    "generation": policy.slot.generation,
                }
            ]
            assert policy.slot.generation > messages[0]["generation"]
            assert not segments.starting
            await segments.current.close()
        else:
            with pytest.raises(PermissionError):
                await policy.enter_routing(work)
            assert not created and policy.active_agent_id == "welcome"
            observed = (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots[0]
            assert observed.terminal_fence and observed.resources["live"].settled == 0
            with pytest.raises(PermissionError):
                await segments.continue_with(slot, work)

    asyncio.run(scenario())


def test_speech_interrupt_retires_generation_without_adopting_old_work(
    live_case, text_execution_case, monkeypatch
):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)

        async def connect(url, **options):
            assert socket.closed == 1 and segment.protocol.closed
            return type(socket)()

        monkeypatch.setattr(segment.adapter, "_factory", connect)
        segments = SpeechSegments(
            current=segment,
            instructions_for=lambda current: "Synthetic interrupted continuation",
            emit=emit,
        )
        policy = ConversationPolicy(
            slot=slot,
            snapshot=service.configuration.pin(),
            admission=service,
            retire_speech=segments.retire,
            continue_speech=segments.continue_with,
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now, speech_segments=segments
        )
        await socket.events.put(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-old",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 10,
                }
            )
        )
        event = await segment.next_event()
        accepted = await controller.submit_delegation(
            segment, event, task_text="Synthetic old work", unambiguous=True
        )
        old_work = controller.queue.take(now=clock.utc_now())
        await policy.interrupt(accepted.receipt.work)
        assert (
            policy.agent_epoch == slot.agent_epoch and policy.slot.generation == slot.generation + 1
        )
        assert policy.slot.expires_at == slot.expires_at
        with pytest.raises(PermissionError, match="stale_generation"):
            await controller._gate(old_work)
        with pytest.raises(PermissionError):
            await segment.append_context("delegation-old", "Obsolete facts")
        assert not requests
        controller.queue.close()
        await segments.current.close()
        await adapter.close()

    asyncio.run(scenario())


def test_speech_transport_disposal_failure_never_authorizes_continuation(live_case, monkeypatch):
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    service, repository, clock, socket, opened = live_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)

        async def fail_dispose():
            raise ConnectionError("synthetic-disposal-unknown")

        async def emit(message):
            return None

        monkeypatch.setattr(socket, "close", fail_dispose)
        segments = SpeechSegments(
            current=segment, instructions_for=lambda current: "Synthetic continuation", emit=emit
        )
        policy = ConversationPolicy(
            slot=slot,
            snapshot=service.configuration.pin(),
            admission=service,
            retire_speech=segments.retire,
            continue_speech=segments.continue_with,
        )
        with pytest.raises(PermissionError):
            await policy.enter_routing(
                WorkBudget(clock.utc_now(), clock.utc_now() + timedelta(seconds=10))
            )
        assert segment.protocol.closed and not segment.disposed
        assert policy.active_agent_id == "welcome"
        with pytest.raises(PermissionError):
            await segments.continue_with(
                slot, WorkBudget(clock.utc_now(), clock.utc_now() + timedelta(seconds=10))
            )
        assert (
            (await repository.read(deadline=asyncio.get_running_loop().time() + 1))
            .authority.slots[0]
            .terminal_fence
        )

    asyncio.run(scenario())


def test_speech_sdk_handover_uses_new_segment_general_context_not_old_delegation(
    live_case, text_execution_case, monkeypatch
):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        sockets = [socket]

        async def connect(url, **options):
            assert all(previous.closed == 1 for previous in sockets)
            fresh = type(socket)()
            sockets.append(fresh)
            return fresh

        monkeypatch.setattr(segment.adapter, "_factory", connect)
        configured = {
            agent["agentId"]: agent for agent in service.configuration.pin().for_runtime()["agents"]
        }
        segments = SpeechSegments(
            current=segment,
            instructions_for=lambda current: configured[current.active_agent_id]["prompt"],
            emit=emit,
        )
        policy = ConversationPolicy(
            slot=slot,
            snapshot=service.configuration.pin(),
            admission=service,
            retire_speech=segments.retire,
            continue_speech=segments.continue_with,
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now, speech_segments=segments
        )
        controls["initial_flow"] = True
        await socket.events.put(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-initial",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 10,
                }
            )
        )
        accepted = await controller.submit_delegation(
            segment,
            await segment.next_event(),
            task_text="Synthetic beta request",
            unambiguous=True,
        )
        await controller.process_next(emit=emit)
        assert policy.active_agent_id == "b-entry" and len(requests) == 3 and len(sockets) == 3
        final = [
            event for event in sockets[-1].sent if event["type"] == "session.commentary.append"
        ]
        assert len(final) == 1 and final[0]["delegation_id"] is None
        assert final[0]["content"] == "Synthetic shared streamed reply"
        assert all(
            not any(event["type"] == "session.commentary.append" for event in previous.sent)
            for previous in sockets[:-1]
        )
        assert not any(event["type"].startswith("text.") for event in messages)
        assert (
            controller.queue.lookup(
                accepted.receipt.task_id,
                accepted.receipt.provider_session_id,
                accepted.receipt.delegation_id,
            )
            == accepted.receipt
        )
        await segments.current.close()
        await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", [False, True])
def test_speech_reader_tracks_authorized_generation_only_and_never_fault_reconnects(
    live_case, monkeypatch, fault
):
    import base64

    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    service, repository, clock, socket, opened = live_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        entered = asyncio.Event()
        receive = socket.recv
        created = []

        async def observed_receive():
            entered.set()
            if fault:
                raise ConnectionError("synthetic-provider-fault")
            return await receive()

        async def connect(url, **options):
            assert not fault and socket.closed == 1
            fresh = type(socket)()
            created.append(fresh)
            return fresh

        async def emit(message):
            return None

        monkeypatch.setattr(socket, "recv", observed_receive)
        monkeypatch.setattr(segment.adapter, "_factory", connect)
        segments = SpeechSegments(
            current=segment, instructions_for=lambda current: "Synthetic continuation", emit=emit
        )
        policy = ConversationPolicy(
            slot=slot,
            snapshot=service.configuration.pin(),
            admission=service,
            retire_speech=segments.retire,
            continue_speech=segments.continue_with,
        )
        if fault:
            with pytest.raises(PermissionError):
                await segments.next_event()
            assert not created
            assert (
                (await repository.read(deadline=asyncio.get_running_loop().time() + 1))
                .authority.slots[0]
                .terminal_fence
            )
        else:
            reader = asyncio.create_task(segments.next_event())
            await asyncio.wait_for(entered.wait(), 1)
            await policy.enter_routing(
                WorkBudget(clock.utc_now(), clock.utc_now() + timedelta(seconds=10))
            )
            pcm = b"\0" * 960
            await created[0].events.put(
                json.dumps(
                    {
                        "type": "session.output_audio.delta",
                        "delta": base64.b64encode(pcm).decode(),
                    }
                )
            )
            generation, event = await asyncio.wait_for(reader, 1)
            assert generation == policy.slot.generation > slot.generation and event["pcm"] == pcm
            await segments.current.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("completed", [False, True])
def test_text_stop_clears_volatile_context_but_only_definitive_work_releases(
    text_execution_case, admission_case, monkeypatch, completed
):
    from caller_runtime.services.admission import AdmissionError
    from caller_runtime.services.cleanup import CleanupObservations, ResourceCleanupVerifier

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        observations = CleanupObservations(owner_id=service.owner_id, utc_now=clock.utc_now)
        controls["observations"] = observations
        monkeypatch.setattr(adapter, "_settlement_observer", observations)
        monkeypatch.setattr(
            service,
            "_cleanup_verifier",
            ResourceCleanupVerifier(observe=observations.observe, utc_now=clock.utc_now),
        )
        controller = await setup()
        controls["blocked"] = not completed
        await controller.submit_text("text-cleanup", "Synthetic volatile private question")
        if completed:
            await controller.process_next(emit=emit)
            assert controller.retained_bytes > 0
            clock.advance(700)
        else:
            processing = asyncio.create_task(controller.process_next(emit=emit))
            await asyncio.wait_for(entered.wait(), 1)
        released = await controller.close()
        assert released is completed
        assert controller.retained_bytes == 0
        assert adapter.disposed is completed
        slots = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots
        if completed:
            assert not slots and await controller.close()
        else:
            assert slots[0].terminal_fence and slots[0].resources["model"].attempted == 1
            assert slots[0].resources["model"].settled == 0
            assert processing.done() and not await controller.close()
            # The held slot refuses the same manager until the stopped request finishes.
            with pytest.raises(AdmissionError, match="manager_busy"):
                await service.reserve(permit(2))
            # The stopped request finishes in the background; its outcome releases the slot.
            release.set()
            await asyncio.wait_for(controller._late_cleanup, 5)
            assert adapter.disposed and len(requests) == 1
            assert not (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots
            assert (await service.reserve(permit(3))).request_id == "request-3"

    asyncio.run(scenario())


def test_speech_transcript_budget_is_volatile_and_only_verified_user_speech_refreshes_idle(
    live_case, text_execution_case
):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        segments = SpeechSegments(
            current=segment, instructions_for=lambda current: "Synthetic voice", emit=emit
        )
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now, speech_segments=segments
        )
        clock.advance(60)
        base = {
            "type": "session.output_transcript.delta",
            "delta": "Synthetic output",
            "start_ms": 0,
            "end_ms": 20,
            "event_id": "event_Eii7Pei1Kd9FKoUKP9eeQ",
        }
        await controller.accept_transcript(base, generation=1, input_audio_accepted=True)
        await controller.accept_transcript(
            base | {"type": "session.input_transcript.delta"},
            generation=1,
            input_audio_accepted=False,
        )
        unchanged = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert unchanged.idle_expires_at == slot.idle_expires_at
        await controller.accept_transcript(
            base | {"type": "session.input_transcript.delta"},
            generation=1,
            input_audio_accepted=True,
        )
        updated = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert updated.idle_expires_at == clock.utc_now() + timedelta(seconds=120)
        for index in range(9):
            await controller.accept_transcript(
                base
                | {"delta": "x" * 7000, "start_ms": index * 1000, "end_ms": (index + 1) * 1000},
                generation=1,
                input_audio_accepted=False,
            )
        assert 63000 < controller.retained_bytes < 256 * 1024
        with pytest.raises(PermissionError, match="transcript_limit"):
            await controller.accept_transcript(
                base | {"delta": "x" * 7000}, generation=1, input_audio_accepted=False
            )
        assert controller.retained_bytes == 0
        await controller.close()
        assert not requests and socket.closed == 1

    asyncio.run(scenario())


def test_failed_work_keeps_session_and_never_dispatches_expired_queue(
    text_execution_case, admission_case
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        controller = await setup()
        controls["blocked"] = True
        await controller.submit_text("text-first", "Synthetic first volatile input")
        processing = asyncio.create_task(controller.process_next(emit=emit))
        await asyncio.wait_for(entered.wait(), 1)
        clock.advance(1)
        await controller.submit_text("text-pending", "Synthetic pending volatile input")
        clock.advance(11)
        release.set()
        await processing
        assert len(requests) == 1 and not any(
            message["type"] == "text.completed" for message in messages
        )
        assert messages[-1] == {
            "type": "error",
            "code": "work_unavailable",
            "generation": controller.policy.slot.generation,
            "messageId": "text-first",
            "cleanupStatus": "active",
        }
        # A failure bounded to one item keeps the session; the expired item is dropped unsent.
        assert not controller.ended
        assert await controller.process_next(emit=emit)
        assert len(requests) == 1 and messages[-1]["code"] == "work_unavailable"
        assert not await controller.process_next(emit=emit)
        assert not (
            (await repository.read(deadline=asyncio.get_running_loop().time() + 1))
            .authority.slots[0]
            .terminal_fence
        )
        await controller.close()
        assert controller.retained_bytes == 0
        await asyncio.wait_for(adapter.drained(), 5)
        assert adapter.disposed

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["text", "speech"])
def test_zero_attempt_expired_start_releases_without_allocating_cleanup_model_or_live(
    text_execution_case, admission_case, monkeypatch, mode
):
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.cleanup import CleanupObservations, ResourceCleanupVerifier
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1, mode=mode))
        observations = CleanupObservations(owner_id=service.owner_id, utc_now=clock.utc_now)
        monkeypatch.setattr(
            service,
            "_cleanup_verifier",
            ResourceCleanupVerifier(observe=observations.observe, utc_now=clock.utc_now),
        )
        policy = ConversationPolicy(slot=slot, snapshot=cache.pin(), admission=service)
        telemetry = Telemetry("caller-runtime", "local")
        try:
            controller = ConversationExecutor(
                policy=policy,
                text_adapter=adapter,
                utc_now=clock.utc_now,
                cleanup_observations=observations,
                telemetry=telemetry,
            )
            clock.advance(700)
            assert await controller.close()
            assert not requests and adapter.disposed and controller.retained_bytes == 0
            assert not (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots
            assert telemetry.events[-1]["event"] == "test.cleanup"
            assert (
                telemetry.events[-1]["kind"] == ("voice" if mode == "speech" else "text")
                and telemetry.events[-1]["outcome"] == "success"
            )
            serialized = json.dumps(telemetry.events)
            assert slot.session_id not in serialized and slot.actor_key not in serialized
        finally:
            telemetry.close()

    asyncio.run(scenario())


def test_cleanup_timeout_never_fabricates_disposal_and_always_purges(
    text_execution_case, admission_case, monkeypatch
):
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.conversation import ConversationExecutor

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        existing = await setup()
        telemetry = Telemetry("caller-runtime", "local")
        controller = ConversationExecutor(
            policy=existing.policy, text_adapter=adapter, utc_now=clock.utc_now, telemetry=telemetry
        )
        await controller.submit_text("text-pending-close", "Synthetic volatile stop content")
        actual_close = adapter.close

        async def blocked_close():
            await asyncio.Event().wait()

        monkeypatch.setattr(adapter, "close", blocked_close)
        try:
            assert not await controller.close(deadline=asyncio.get_running_loop().time() + 0.05)
            assert controller.retained_bytes == 0 and not adapter.disposed
            assert telemetry.events[-1]["reason_code"] == "cleanup_pending"
            pending_events = [
                event for event in telemetry.events if event.get("reason_code") == "cleanup_pending"
            ]
            assert len(pending_events) == 3
            assert len({event["correlation_id"] for event in pending_events}) == 1
            assert (
                (await repository.read(deadline=asyncio.get_running_loop().time() + 1))
                .authority.slots[0]
                .terminal_fence
            )
        finally:
            await actual_close()
            telemetry.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("speech", [False, True])
def test_authority_loss_still_disposes_owned_resources_but_never_releases(
    text_execution_case, live_case, admission_case, monkeypatch, speech
):
    from caller_runtime.adapters.admission_repository import AuthorityUnavailable
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, clock, socket, opened = live_case

    async def scenario():
        if speech:
            original, segment = await opened()
            slot = await service.check_output(
                original.session_id, original.owner_epoch, generation=1
            )
            segments = SpeechSegments(
                current=segment, instructions_for=lambda current: "Synthetic voice", emit=emit
            )
            policy = ConversationPolicy(
                slot=slot, snapshot=service.configuration.pin(), admission=service
            )
            controller = ConversationExecutor(
                policy=policy, text_adapter=adapter, utc_now=clock.utc_now, speech_segments=segments
            )
        else:
            controller = await setup()

        async def unavailable(*, deadline):
            raise AuthorityUnavailable()

        monkeypatch.setattr(repository, "read", unavailable)
        assert not await controller.close()
        assert adapter.disposed and controller.retained_bytes == 0
        if speech:
            assert socket.closed == 1 and segment.disposed
        assert not requests

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["provider-body", "oversized"])
def test_wire_rejects_unreviewed_or_oversized_server_control_before_enqueue(
    text_execution_case, fault
):
    from types import SimpleNamespace

    from caller_runtime.api.voice import VoiceWire

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        controller = await setup()
        wire = VoiceWire(SimpleNamespace())
        wire.session = controller
        event = (
            {
                "type": "session.delegation.created",
                "provider_body": "synthetic-private-provider-body",
            }
            if fault == "provider-body"
            else {"type": "text.delta", "messageId": "text-1", "text": "x" * 8193}
        )
        with pytest.raises(PermissionError):
            await wire.emit(event)
        assert wire._outbound.empty() and not requests
        await controller.close()

    asyncio.run(scenario())


def test_wire_control_queue_never_exceeds_sixty_four_pending_messages(text_execution_case):
    from types import SimpleNamespace

    from caller_runtime.api.voice import VoiceWire

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        controller = await setup()
        wire = VoiceWire(SimpleNamespace())
        wire.session = controller
        for index in range(64):
            await wire.emit({"type": "pong", "nonce": "n" + str(index)})
        with pytest.raises(PermissionError, match="control_queue_full"):
            await wire.emit({"type": "pong", "nonce": "overflow"})
        assert wire._outbound.qsize() == 64
        generation, raw = wire._outbound.get_nowait()
        wire._outbound.task_done()
        envelope = json.loads(raw)
        assert envelope["sessionId"] == controller.policy.slot.session_id
        assert envelope["sequence"] == 1 and generation == envelope["generation"]
        await controller.close()

    asyncio.run(scenario())


def test_wire_rechecks_authority_before_queued_output_not_just_at_enqueue(
    text_execution_case, admission_case
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from caller_runtime.api.voice import VoiceWire
    from caller_runtime.services.admission import AdmissionError

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        controller = await setup()
        socket = SimpleNamespace(send_text=AsyncMock())
        wire = VoiceWire(socket)
        wire.session = controller
        await wire.emit({"type": "pong", "nonce": "queued-before-expiry"})
        clock.advance(121)
        with pytest.raises(AdmissionError, match="session_expired"):
            await asyncio.wait_for(wire.write(), 1)
        socket.send_text.assert_not_awaited()
        await controller.close()

    asyncio.run(scenario())


def test_audio_flow_bounds_capture_rate_sequences_and_generation_without_idle_flags():
    import struct

    from caller_runtime.api.voice import AudioFlow

    ticks = [0.0]
    flow = AudioFlow(generation=7, monotonic=lambda: ticks[0])
    pcm = b"\0" * 1920
    for sequence in range(1, 7):
        assert flow.input(struct.pack(">BII", 1, 7, sequence) + pcm) == (sequence, pcm)
    with pytest.raises(PermissionError, match="capture_credit_exhausted"):
        flow.input(struct.pack(">BII", 1, 7, 7) + b"\0" * 960)
    ticks[0] += 0.02
    assert flow.input(struct.pack(">BII", 1, 7, 7) + b"\0" * 960)[0] == 7
    with pytest.raises(PermissionError, match="invalid_audio_sequence"):
        flow.input(struct.pack(">BII", 1, 7, 7) + b"\0" * 960)
    # Capture of a retired generation that was still on the wire is dropped; a newer one fails.
    assert flow.input(struct.pack(">BII", 1, 6, 8) + b"\0" * 960) is None
    with pytest.raises(PermissionError, match="stale_generation"):
        flow.input(struct.pack(">BII", 1, 8, 8) + b"\0" * 960)
    assert flow.input_sequence == 7
    ticks[0] -= 1
    with pytest.raises(PermissionError, match="audio_clock_invalid"):
        flow.input(struct.pack(">BII", 1, 7, 8) + b"\0" * 960)


def test_audio_flow_played_ack_cannot_free_unsent_audio_or_exceed_500ms():
    import struct

    from caller_runtime.api.voice import AudioFlow

    flow = AudioFlow(generation=3, monotonic=lambda: 0.0)
    frames = flow.output(b"\0" * 24000, generation=3)
    assert len(frames) == 25 and flow.pending_bytes == 24000
    assert struct.unpack(">BII", frames[0][:9]) == (1, 3, 1)
    assert all(0 < len(frame) - 9 <= 1920 and (len(frame) - 9) % 2 == 0 for frame in frames)
    with pytest.raises(PermissionError, match="playback_credit_exhausted"):
        flow.output(b"\0" * 960, generation=3)
    with pytest.raises(PermissionError, match="invalid_played_ack"):
        flow.played(1, generation=3)
    for sequence in range(1, 11):
        flow.sent(sequence, generation=3)
    flow.played(10, generation=3)
    assert flow.pending_bytes == 14400
    flow.played(10, generation=3)
    assert flow.pending_bytes == 14400
    with pytest.raises(PermissionError, match="invalid_played_ack"):
        flow.played(9, generation=3)
    flow.output(b"\0" * 960, generation=3)
    flow.reset(4)
    assert flow.pending_bytes == 0
    with pytest.raises(PermissionError, match="stale_generation"):
        flow.played(10, generation=3)


@pytest.mark.parametrize("fault", ["short", "protocol", "odd", "oversized"])
def test_audio_flow_rejects_malformed_before_pcm_acceptance(fault):
    import struct

    from caller_runtime.api.voice import AudioFlow

    frame = struct.pack(">BII", 2 if fault == "protocol" else 1, 1, 1)
    frame += b"\0" * (1922 if fault == "oversized" else 959 if fault == "odd" else 960)
    if fault == "short":
        frame = frame[:8]
    with pytest.raises(PermissionError):
        AudioFlow(generation=1, monotonic=lambda: 0.0).input(frame)


def test_wire_audio_shares_writer_budget_and_discards_actual_retired_generation(
    live_case, text_execution_case, monkeypatch
):
    import struct
    from types import SimpleNamespace

    from caller_runtime.api.voice import VoiceWire
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        wire = VoiceWire(SimpleNamespace())

        async def connect(url, **options):
            assert socket.closed == 1
            return type(socket)()

        monkeypatch.setattr(segment.adapter, "_factory", connect)
        segments = SpeechSegments(
            current=segment,
            instructions_for=lambda current: "Synthetic continuation",
            emit=wire.emit,
        )
        policy = ConversationPolicy(
            slot=slot,
            snapshot=service.configuration.pin(),
            admission=service,
            retire_speech=segments.retire,
            continue_speech=segments.continue_with,
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now, speech_segments=segments
        )
        wire.session = controller
        await wire.audio_output(b"\0" * 24000, generation=1)
        assert wire.audio.pending_bytes == 24000 and wire._outbound.qsize() == 25
        await policy.interrupt(WorkBudget(clock.utc_now(), clock.utc_now() + timedelta(seconds=10)))
        assert wire.audio.pending_bytes == 0 and wire._outbound.qsize() == 1
        await wire.audio_output(b"\0" * 960, generation=1)
        assert wire._outbound.qsize() == 1
        await wire.audio_output(b"\0" * 960, generation=policy.slot.generation)
        generation, changed = wire._outbound.get_nowait()
        wire._outbound.task_done()
        assert json.loads(changed)["type"] == "generation.changed"
        generation, frame = wire._outbound.get_nowait()
        wire._outbound.task_done()
        assert isinstance(frame, bytes)
        assert struct.unpack(">BII", frame[:9]) == (1, policy.slot.generation, 1)
        wire.dispose()
        assert wire.audio.pending_bytes == 0 and wire._outbound.empty()
        await controller.close()

    asyncio.run(scenario())


def test_speech_fragments_correlate_bounded_verified_input_without_inventing_turns():
    from caller_runtime.services.delegation import SpeechFragments

    fragments = SpeechFragments()
    fragments.append("Synthetic ", start_ms=0, end_ms=100, generation=1, accepted=True)
    fragments.append("request?", start_ms=100, end_ms=200, generation=1, accepted=True)
    assert fragments.retained_bytes > 0
    assert fragments.consume(offset_ms=250, generation=1) == "Synthetic request?"
    assert fragments.retained_bytes == 0
    fragments.append("Unverified speech", start_ms=0, end_ms=100, generation=1, accepted=False)
    assert fragments.consume(offset_ms=150, generation=1) is None
    fragments.append("Old generation", start_ms=0, end_ms=100, generation=1, accepted=True)
    assert fragments.consume(offset_ms=150, generation=2) is None


@pytest.mark.parametrize("fault", ["overlap", "stale", "oversized"])
def test_speech_fragments_require_clarification_on_ambiguous_context(fault):
    from caller_runtime.services.delegation import SpeechFragments

    fragments = SpeechFragments()
    fragments.append("Synthetic ", start_ms=0, end_ms=100, generation=1, accepted=True)
    if fault == "overlap":
        fragments.append("overlap", start_ms=50, end_ms=120, generation=1, accepted=True)
    elif fault == "oversized":
        fragments.append("x" * 4097, start_ms=100, end_ms=200, generation=1, accepted=True)
    assert fragments.consume(offset_ms=2201 if fault == "stale" else 200, generation=1) is None
    assert fragments.retained_bytes == 0


@pytest.mark.parametrize("clear", [False, True])
def test_correlated_fragments_need_shared_backend_clarity_before_any_actions(
    live_case, text_execution_case, clear
):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy
    from caller_runtime.services.modality_defaults import VOICE_PLATFORM_RULES

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now
        )
        controls["clarity"] = clear
        event = segment.protocol.accept(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-fragments",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 20,
                }
            )
        )
        accepted = await controller.submit_delegation(
            segment,
            event,
            task_text="Synthetic correlated fragment text",
            unambiguous=False,
            requires_clarity_check=True,
        )
        deadline = accepted.receipt.work.work_deadline_utc
        # A speech delegation gets the longer speech budget; text keeps 10 s.
        assert deadline == accepted.receipt.work.work_accepted_at + timedelta(seconds=30)
        await controller.process_next(emit=emit)
        assert len(requests) == 1 + int(clear)
        expected = VOICE_PLATFORM_RULES + "\n\nSynthetic welcome prompt"
        assert requests[0]["instructions"] == expected
        assert "Synthetic correlated fragment text" in json.dumps(requests[0]["input"])
        # The clarity check and the speech work both ask for low reasoning effort.
        assert all(request["reasoning"]["effort"] == "low" for request in requests)
        if clear:
            assert requests[1]["instructions"] == expected
            # Client timeouts are the background cap; the work budget bounds the wait.
            assert max(controls["timeouts"][1].values()) == 60
        final = [event for event in socket.sent if event["type"] == "session.commentary.append"]
        assert len(final) == 1 and final[0]["delegation_id"] == "delegation-fragments"
        assert ("clarify" in final[0]["content"].lower()) is not clear
        assert (
            controller.queue.lookup(
                accepted.receipt.task_id,
                accepted.receipt.provider_session_id,
                accepted.receipt.delegation_id,
            ).work.work_deadline_utc
            == deadline
        )
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.resources["capability"].attempted == 0
        await segment.close()
        await controller.close()

    asyncio.run(scenario())


def test_slow_speech_delegation_has_no_holding_line_then_fails_alone_and_next_delegation_runs(
    live_case, text_execution_case
):
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.conversation import WORK_FAILED_LINE, ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        telemetry = Telemetry("caller-runtime", "local")
        controller = ConversationExecutor(
            policy=policy,
            text_adapter=adapter,
            utc_now=clock.utc_now,
            telemetry=telemetry,
        )

        async def delegate(delegation_id):
            event = segment.protocol.accept(
                json.dumps(
                    {
                        "type": "session.delegation.created",
                        "delegation": {
                            "id": delegation_id,
                            "type": "delegation",
                            "target": "client",
                        },
                        "offset_ms": 20,
                    }
                )
            )
            return await controller.submit_delegation(
                segment, event, task_text="Synthetic clear request", unambiguous=True
            )

        def said(delegation_id):
            return [
                event["content"]
                for event in socket.sent
                if event["type"] == "session.commentary.append"
                and event["delegation_id"] == delegation_id
            ]

        controls["blocked"] = True
        await delegate("delegation-slow")
        processing = asyncio.create_task(controller.process_next(emit=emit))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.2)
        # The fixed holding line is gone; progress comes only from the agent (FR-023).
        assert said("delegation-slow") == []
        clock.advance(31)
        release.set()
        assert await asyncio.wait_for(processing, 5)
        # Only this item failed: without a final state the caller hears the fallback line.
        assert said("delegation-slow") == [WORK_FAILED_LINE]
        assert [
            (e["outcome"], e["reason_code"])
            for e in telemetry.events
            if e["event"] == "test.progress"
        ] == [("failure", "fallback_line")]
        assert messages[-1]["code"] == "work_unavailable" and not controller.ended
        assert any(
            event["event"] == "test.work" and event["outcome"] == "failure"
            for event in telemetry.events
        )
        controls["blocked"] = False
        entered.clear()
        # The late reply is discarded but still settles the attempt, unblocking new work.
        await asyncio.wait_for(adapter.drained(), 5)
        await delegate("delegation-next")
        assert await controller.process_next(emit=emit)
        assert said("delegation-next") == ["Synthetic shared streamed reply"]
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert not observed.terminal_fence
        assert observed.resources["model"].attempted == observed.resources["model"].settled
        assert len(requests) == 2
        assert all(request["reasoning"]["effort"] == "low" for request in requests)
        await segment.close()
        await controller.close()

    asyncio.run(scenario())


def test_synchronous_fence_blocks_dispatch_before_awaited_socket_or_store_cleanup(
    text_execution_case, admission_case, monkeypatch
):
    from types import SimpleNamespace

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        controller = await setup()
        token_entered, token_release = asyncio.Event(), asyncio.Event()

        async def token(scope):
            token_entered.set()
            await token_release.wait()
            return SimpleNamespace(
                token="synthetic-delayed-token", expires_on=clock.utc_now().timestamp() + 900
            )

        monkeypatch.setattr(adapter.credential, "get_token", token)
        await controller.submit_text("text-fence", "Synthetic volatile pending question")
        processing = asyncio.create_task(controller.process_next(emit=emit))
        await asyncio.wait_for(token_entered.wait(), 1)
        controller.fence()
        assert controller.ended and controller.retained_bytes == 0
        assert service._terminal[controller.policy.slot.session_id].is_set()
        token_release.set()
        await processing
        assert not requests
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.terminal_fence and observed.attempted_through == 0

    asyncio.run(scenario())


def test_wire_rejects_old_played_generation_even_when_audio_sequence_is_reused(
    live_case, text_execution_case
):
    from types import SimpleNamespace

    from caller_runtime.api.voice import VoiceWire
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now
        )
        wire = VoiceWire(SimpleNamespace())
        wire.session = controller
        wire._advance(2)
        with pytest.raises(PermissionError, match="stale_generation"):
            wire.command(
                json.dumps(
                    {
                        "type": "audio.played",
                        "sequence": 1,
                        "sessionId": slot.session_id,
                        "audioSequence": 1,
                        "generation": 3,
                    }
                )
            )
        # An ack of generation 1 was on the wire when it was retired; it is taken and ignored.
        assert (
            wire.command(
                json.dumps(
                    {
                        "type": "audio.played",
                        "sequence": 1,
                        "sessionId": slot.session_id,
                        "audioSequence": 1,
                        "generation": 1,
                    }
                )
            )[1]
            is False
        )
        assert (
            wire.command(
                json.dumps(
                    {
                        "type": "audio.played",
                        "sequence": 2,
                        "sessionId": slot.session_id,
                        "audioSequence": 0,
                        "generation": 2,
                    }
                )
            )[1]
            is False
        )
        wire.dispose()
        await segment.close()
        await controller.close()

    asyncio.run(scenario())


def test_wire_duplicate_accepted_interrupt_is_acknowledged_after_generation_advances(
    live_case, text_execution_case
):
    from types import SimpleNamespace

    from caller_runtime.api.voice import VoiceWire
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        controller = ConversationExecutor(
            policy=ConversationPolicy(
                slot=slot, snapshot=service.configuration.pin(), admission=service
            ),
            text_adapter=adapter,
            utc_now=clock.utc_now,
        )
        wire = VoiceWire(SimpleNamespace())
        wire.session = controller
        raw = json.dumps(
            {
                "type": "output.interrupt",
                "sequence": 1,
                "sessionId": slot.session_id,
                "generation": 1,
            }
        )
        assert wire.command(raw)[1] is False
        wire._advance(2)
        assert wire.command(raw)[1] is True
        with pytest.raises(PermissionError, match="invalid_sequence"):
            wire.command(
                json.dumps(
                    {
                        "type": "output.interrupt",
                        "sequence": 1,
                        "sessionId": slot.session_id,
                        "generation": 2,
                    }
                )
            )
        wire.dispose()
        await segment.close()
        await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "event",
    [
        {"type": "audio.credit", "availableSamples": 6001},
        {"type": "audio.credit", "availableSamples": True},
        {"type": "command.ack", "acceptedSequence": 0},
        {"type": "pong", "nonce": "bad nonce"},
        {
            "type": "transcript.delta",
            "speaker": "provider-body",
            "fragment": "private",
            "start_ms": 0,
            "end_ms": 1,
        },
        {
            "type": "transcript.delta",
            "speaker": "user",
            "fragment": "private",
            "start_ms": 20,
            "end_ms": 1,
        },
    ],
    ids=["credit-bound", "credit-type", "ack-sequence", "nonce", "speaker", "timeline"],
)
def test_server_control_field_values_are_closed_not_only_key_names(event):
    from caller_runtime.api.voice import server_control

    with pytest.raises(PermissionError):
        server_control(event)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "progress", "state": "unknown", "text": "Checking."},
        {"type": "progress", "state": "completed", "text": ""},
        {"type": "progress", "state": "completed", "text": "x" * 201},
        {"type": "progress", "state": "completed", "text": "Done.", "messageId": "m1"},
    ],
    ids=["state", "empty", "long", "extra-field"],
)
def test_voice_progress_control_is_validated(event):
    from caller_runtime.api.voice import server_control

    with pytest.raises(PermissionError):
        server_control(event)
    valid = {"type": "progress", "state": "still_working", "text": "Checking.", "generation": 1}
    assert server_control(valid) == valid


def progress_actions(*pairs):
    return [("report_progress", {"state": state, "message": message}) for state, message in pairs]


def tool_outputs(request):
    return [
        item["output"]
        for item in request["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]


def test_report_progress_validates_streams_and_lets_the_agent_continue(
    text_execution_case, admission_case
):
    from caller_runtime.observability import Telemetry

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controls["telemetry"] = telemetry = Telemetry("caller-runtime", "local")
    controls["wire_actions"] = progress_actions(
        ("still_working", "Checking the order status."),
        ("bogus", "Not a state."),
        ("completed", ""),
        ("completed", "x" * 201),
        ("still_working", "Still checking."),
        ("waiting_for_input", "Which order do you mean?"),
        ("completed", "Your order was updated."),
    )

    async def scenario():
        controller = await setup()
        await controller.submit_text("text-progress", "Synthetic order request")
        assert await controller.process_next(emit=emit)
        progress = [message for message in messages if message["type"] == "progress"]
        assert [(p["messageId"], p["state"], p["text"]) for p in progress] == [
            ("text-progress", "still_working", "Checking the order status."),
            ("text-progress", "waiting_for_input", "Which order do you mean?"),
            ("text-progress", "completed", "Your order was updated."),
        ]
        # Every call answers the model, which then continues; the turn still completes.
        assert [tool_outputs(request)[-1] for request in requests[1:]] == [
            '"reported"',
            '"refused"',
            '"refused"',
            '"refused"',
            '"reported"',
            '"reported"',
            '"reported"',
        ]
        assert messages[-1]["type"] == "text.completed"
        tools = [tool["name"] for tool in requests[0]["tools"]]
        assert "report_progress" in tools
        schema = next(t for t in requests[0]["tools"] if t["name"] == "report_progress")
        assert schema["parameters"]["properties"]["state"]["enum"] == [
            "still_working",
            "waiting_for_input",
            "completed",
            "failed",
            "cancellation_confirmed",
        ]
        # Final and waiting states enter history before the answer; still_working does not.
        turns = controller.text_turns()
        assert [turn["content"] for turn in turns if turn["content"].startswith("Status")] == [
            "Status (waiting_for_input): Which order do you mean?",
            "Status (completed): Your order was updated.",
        ]
        assert turns[-1]["content"] == "Synthetic shared streamed reply"
        reasons = [
            (event["outcome"], event["reason_code"])
            for event in telemetry.events
            if event["event"] == "test.progress"
        ]
        assert reasons == [
            ("success", "still_working"),
            ("failure", "rate_limited"),
            ("success", "waiting_for_input"),
            ("success", "completed"),
        ]
        assert "Checking the order" not in json.dumps(telemetry.events)
        await controller.close()

    asyncio.run(scenario())


def test_progress_rate_limit_repeats_still_working_after_ten_seconds_and_drops_stale(
    text_execution_case, admission_case
):
    from types import SimpleNamespace

    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.conversation import ProgressState

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case
    controls["telemetry"] = telemetry = Telemetry("caller-runtime", "local")

    async def scenario():
        controller = await setup()
        work = WorkBudget(clock.utc_now(), clock.utc_now() + timedelta(seconds=60))
        item = SimpleNamespace(
            generation=controller.policy.slot.generation,
            receipt=SimpleNamespace(task_id="text-rate", work=work, delegation_id=None),
        )
        controller._current_item = item
        state = ProgressState()

        async def report(name, text="Working on it."):
            await controller._report_progress(item, state, name, text, emit)

        await report("still_working")
        await report("still_working")
        clock.advance(10)
        await report("still_working")
        await report("completed")
        await report("completed")
        controller._current_item = None
        await report("failed")
        assert [m["state"] for m in messages if m["type"] == "progress"] == [
            "still_working",
            "still_working",
            "completed",
        ]
        assert state.final_reported
        reasons = [e["reason_code"] for e in telemetry.events if e["event"] == "test.progress"]
        assert reasons == [
            "still_working",
            "rate_limited",
            "still_working",
            "completed",
            "rate_limited",
            "stale",
        ]
        await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(("setting", "sent"), [(None, True), (True, True), (False, False)])
def test_quiet_handover_follows_the_published_switch(
    live_case, text_execution_case, monkeypatch, setting, sent
):
    from caller_runtime.domain.configuration import RevisionSnapshot
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy
    from caller_runtime.services.speech_parts import QUIET_HANDOVER

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    settings = {} if setting is None else {"quietHandover": setting}
    monkeypatch.setattr(RevisionSnapshot, "conversation_settings", lambda self: dict(settings))

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now
        )
        event = segment.protocol.accept(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-quiet",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 10,
                }
            )
        )
        await controller.submit_delegation(segment, event, task_text=None, unambiguous=False)
        quiet = [
            e["content"]
            for e in socket.sent
            if e["type"] == "session.thinking.append" and e["delegation_id"] == "delegation-quiet"
        ]
        assert quiet == ([QUIET_HANDOVER] if sent else [])
        await segment.close()
        await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("final", [False, True])
def test_failure_line_stands_in_only_without_a_final_progress_state(
    live_case, text_execution_case, monkeypatch, final
):
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.conversation import WORK_FAILED_LINE, ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        telemetry = Telemetry("caller-runtime", "local")
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now, telemetry=telemetry
        )

        async def failing(item, emit):
            if final:
                progress = controller._progress[item.receipt.task_id]
                await controller._report_progress(
                    item, progress, "failed", "The lookup failed.", emit
                )
            raise PermissionError("model_unavailable")

        monkeypatch.setattr(controller, "_execute", failing)
        event = segment.protocol.accept(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-fail",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 10,
                }
            )
        )
        await controller.submit_delegation(
            segment, event, task_text="Synthetic clear request", unambiguous=True
        )
        assert await controller.process_next(emit=emit)
        spoken = [
            e["content"]
            for e in socket.sent
            if e["type"] == "session.commentary.append" and e["delegation_id"] == "delegation-fail"
        ]
        reasons = [e["reason_code"] for e in telemetry.events if e["event"] == "test.progress"]
        if final:
            assert spoken == ["The lookup failed."] and reasons == ["failed"]
            assert controller.text_turns()[-1]["content"] == "Status (failed): The lookup failed."
        else:
            assert spoken == [WORK_FAILED_LINE] and reasons == ["fallback_line"]
        await segment.close()
        await controller.close()

    asyncio.run(scenario())


def test_speech_progress_is_spoken_with_the_delegation_and_shown_in_the_browser(
    live_case, text_execution_case
):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    service, repository, clock, socket, opened = live_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controls["wire_actions"] = progress_actions(("still_working", "Checking that for you."))

    async def scenario():
        original, segment = await opened()
        slot = await service.check_output(original.session_id, original.owner_epoch, generation=1)
        policy = ConversationPolicy(
            slot=slot, snapshot=service.configuration.pin(), admission=service
        )
        controller = ConversationExecutor(
            policy=policy, text_adapter=adapter, utc_now=clock.utc_now
        )
        event = segment.protocol.accept(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "delegation-progress",
                        "type": "delegation",
                        "target": "client",
                    },
                    "offset_ms": 10,
                }
            )
        )
        await controller.submit_delegation(
            segment, event, task_text="Synthetic clear request", unambiguous=True
        )
        assert await controller.process_next(emit=emit)
        spoken = [
            e["content"]
            for e in socket.sent
            if e["type"] == "session.commentary.append"
            and e["delegation_id"] == "delegation-progress"
        ]
        assert spoken == ["Checking that for you.", "Synthetic shared streamed reply"]
        assert [m for m in messages if m["type"] == "progress"] == [
            {
                "type": "progress",
                "state": "still_working",
                "text": "Checking that for you.",
                "generation": slot.generation,
            }
        ]
        await segment.close()
        await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("end_ms", "since_first_s", "since_last_s", "verified"),
    [
        # Recorded on int: Live transcript time against time since the first forwarded frame.
        (8400, 8.86, 0.4, True),
        (75800, 73.38, 0.4, True),
        (297600, 293.88, 0.4, True),
        # Microphone paused for 25 s: Live ran 4 s past the 21.4 s actually forwarded.
        (25400, 46.35, 0.4, True),
        # A first utterance shortly after ready, with the Live clock a second ahead.
        (4600, 2.0, 0.4, True),
        # About ten minutes with Live's clock 12 s ahead.
        (612000, 600.0, 0.4, True),
        # Speech after the microphone stopped forwarding, and speech past the forwarded time.
        (8000, 20.0, 10.5, False),
        (14000, 3.0, 0.4, False),
        (700000, 600.0, 0.4, False),
    ],
)
def test_input_speech_counts_while_audio_is_forwarded_and_within_the_live_clock_drift(
    end_ms, since_first_s, since_last_s, verified
):
    from caller_runtime.api.voice import observed_speech

    # Counting forwarded samples rejected speech once capture had gaps, so idle expired mid-answer.
    assert observed_speech(end_ms, (100.0 - since_first_s, 100.0 - since_last_s), 100.0) is verified
    assert observed_speech(end_ms, None, 100.0) is False


def test_shared_model_readiness_uses_pinned_welcome_before_initial_ready_without_tools(
    text_execution_case, admission_case
):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        controller = ConversationExecutor(
            policy=ConversationPolicy(
                slot=slot, snapshot=service.snapshot_for(slot), admission=service
            ),
            text_adapter=adapter,
            utc_now=clock.utc_now,
        )
        controls["prepare"] = True
        await controller.prepare()
        assert len(requests) == 1 and requests[0]["instructions"] == "Synthetic welcome prompt"
        # A full Welcome reply on the live reasoning model took 4-10 s; readiness must stay short.
        assert requests[0]["max_output_tokens"] == 16 and not requests[0].get("tools")
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert (
            observed.ready_at is None
            and observed.resources["model"].attempted == observed.resources["model"].settled == 1
        )
        assert (
            observed.resources["live"].attempted == observed.resources["capability"].attempted == 0
        )
        assert controller.retained_bytes == 0
        await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["text", "speech"])
def test_greeting_request_uses_welcome_with_mode_rules_low_effort_and_no_tools(
    text_execution_case, admission_case, mode
):
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.services import modality_defaults
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy
    from caller_runtime.services.greeting import TEXT_GREETING_INPUT, VOICE_GREETING_INPUT

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1, mode=mode))
        slot = await service.mark_ready(slot.session_id, slot.owner_epoch)
        controller = ConversationExecutor(
            policy=ConversationPolicy(
                slot=slot, snapshot=service.snapshot_for(slot), admission=service
            ),
            text_adapter=adapter,
            utc_now=clock.utc_now,
        )
        controls["greeting"] = "  Hello, welcome to the service. How can I help?  "
        now = clock.utc_now()
        work = WorkBudget(now, now + timedelta(seconds=5))
        assert await controller.greeting_text(mode, work) == (
            "Hello, welcome to the service. How can I help?"
        )
        body = controls["greeting_requests"][-1]
        rules = (
            modality_defaults.VOICE_PLATFORM_RULES
            if mode == "speech"
            else modality_defaults.TEXT_PLATFORM_RULES
        )
        assert body["instructions"] == rules + "\n\nSynthetic welcome prompt"
        assert body["input"] == (VOICE_GREETING_INPUT if mode == "speech" else TEXT_GREETING_INPUT)
        assert body["reasoning"] == {"effort": "low"}
        assert body["max_output_tokens"] == 256 and body["store"] is False
        assert not body.get("tools") and not body.get("stream")
        with pytest.raises(PermissionError, match="^mode_mismatch$"):
            await controller.greeting_text("text" if mode == "speech" else "speech", work)
        await controller.close()

    asyncio.run(scenario())


def test_greeting_request_is_cancellable_and_still_settles(text_execution_case, admission_case):
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1, mode="speech"))
        slot = await service.mark_ready(slot.session_id, slot.owner_epoch)
        controller = ConversationExecutor(
            policy=ConversationPolicy(
                slot=slot, snapshot=service.snapshot_for(slot), admission=service
            ),
            text_adapter=adapter,
            utc_now=clock.utc_now,
        )
        controls["greeting"], controls["greeting_block"] = "Late greeting", True
        now = clock.utc_now()
        task = asyncio.create_task(
            controller.greeting_text("speech", WorkBudget(now, now + timedelta(seconds=5)))
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await adapter.drained()
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.resources["model"].attempted == observed.resources["model"].settled == 1
        await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["model", "failed"])
def test_text_open_streams_the_greeting_and_keeps_it_as_the_first_assistant_turn(
    text_execution_case, outcome
):
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.greeting import TEXT_FALLBACK_GREETING

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    telemetry = Telemetry("caller-runtime", "local")

    async def scenario():
        controls["telemetry"] = telemetry
        controller = await setup()
        controls["greeting"] = "Welcome! How can I help?"
        if outcome == "failed":
            controls["greeting_status"] = 429
        await controller.open_text(emit, started=asyncio.get_running_loop().time())
        expected = "Welcome! How can I help?" if outcome == "model" else TEXT_FALLBACK_GREETING
        assert [m["type"] for m in messages] == ["text.delta", "text.completed"]
        assert messages[0]["text"] == expected
        assert controller.text_turns() == [{"role": "assistant", "content": expected}]
        # One request per turn: the open request makes only the greeting call.
        assert not requests and len(controls["greeting_requests"]) == 1
        with pytest.raises(PermissionError, match="^mode_mismatch$"):
            await controller.open_text(emit, started=asyncio.get_running_loop().time())
        await controller.close()

    try:
        asyncio.run(scenario())
        greeting = [e for e in telemetry.events if e["event"] == "test.greeting"]
        assert [(e["outcome"], e["reason_code"]) for e in greeting] == [
            ("success", "ready" if outcome == "model" else "fallback")
        ]
    finally:
        telemetry.close()


@pytest.mark.parametrize("reason", ["content_filter", "max_input_tokens"])
def test_readiness_fails_on_another_incomplete_reason_but_still_settles(
    text_execution_case, admission_case, reason
):
    from caller_runtime.services.conversation import ConversationExecutor
    from caller_runtime.services.conversation_policy import ConversationPolicy

    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        controller = ConversationExecutor(
            policy=ConversationPolicy(
                slot=slot, snapshot=service.snapshot_for(slot), admission=service
            ),
            text_adapter=adapter,
            utc_now=clock.utc_now,
        )
        controls["prepare"], controls["prepare_reason"] = True, reason
        with pytest.raises(PermissionError, match="^model_unavailable$"):
            await controller.prepare()
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.resources["model"].attempted == observed.resources["model"].settled == 1
        await controller.close()

    asyncio.run(scenario())


def test_aggregate_context_overflow_fails_before_model_without_trimming_security_context(
    text_execution_case, admission_case
):
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        controller = await setup()
        controller._history = [{"role": "user", "content": "synthetic-private-context-" * 12000}]
        assert controller.retained_bytes > 256 * 1024
        with pytest.raises(PermissionError, match="^conversation_context_limit$"):
            await controller.submit_text("text-overflow", "Synthetic new request")
        assert not requests and not messages
        await controller.close()
        assert controller.retained_bytes == 0 and adapter.disposed

    asyncio.run(scenario())


def test_configured_agent_registry_reuses_pinned_publication_and_reviewed_tools(
    runtime_revision_v2,
):
    from unittest.mock import Mock

    from agents import FunctionTool
    from agents.models.interface import Model

    from caller_runtime.domain.configuration import RevisionSnapshot
    from caller_runtime.services.conversation_agents import build_agents
    from caller_runtime.services.modality_defaults import TEXT_PLATFORM_RULES, VOICE_PLATFORM_RULES

    snapshot = RevisionSnapshot.validate(runtime_revision_v2(), etag='"agents-current"')
    model = Mock(spec=Model)
    selected = []

    async def invoke(context, arguments):
        return '{"name":"Synthetic approved result"}'

    def capabilities(agent):
        selected.append(agent["agentId"])
        if not agent["capabilityAssignments"]:
            return []
        return [
            FunctionTool(
                name="public_lookup",
                description="Synthetic public lookup",
                params_json_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                on_invoke_tool=invoke,
            )
        ]

    agents = build_agents(
        snapshot,
        model=model,
        mode="text",
        capabilities=capabilities,
        policy_tools=lambda _agent: [],
    )
    configured = snapshot.for_runtime()["agents"]
    assert set(agents) == {agent["agentId"] for agent in configured}
    for agent in configured:
        sdk = agents[agent["agentId"]]
        assert sdk.name == agent["name"]
        assert sdk.instructions == TEXT_PLATFORM_RULES + "\n\n" + agent["prompt"]
        assert sdk.model is model and sdk.handoffs == []
        assert sdk.model_settings.store is False and sdk.model_settings.parallel_tool_calls is False
        assert sdk.model_settings.retry.max_retries == 0
        assert len(sdk.tools) == (1 if agent["capabilityAssignments"] else 0)
    assert selected == [agent["agentId"] for agent in configured if agent["role"] == "ordinary"]
    speech = build_agents(
        snapshot, model=model, mode="speech", capabilities=capabilities, policy_tools=lambda _: []
    )
    for agent in configured:
        assert speech[agent["agentId"]].instructions == (
            VOICE_PLATFORM_RULES + "\n\n" + agent["prompt"]
        )


def note_graph(count, *, description="Synthetic routing description"):
    return {
        "agents": [
            {"agentId": "welcome", "name": "Welcome", "role": "welcome", "prompt": "Secret A"},
            {"agentId": "goodbye", "name": "Goodbye", "role": "goodbye", "prompt": "Secret B"},
            {
                "agentId": "entry-0",
                "name": "Entry",
                "role": "ordinary",
                "prompt": "Secret C",
                "communityId": "c-0",
            },
        ],
        "communities": [
            {
                "communityId": f"c-{index}",
                "name": f"Community {index}",
                "routingDescription": description,
                "enabled": index != 1,
                **({"routable": False} if index == 2 else {}),
            }
            for index in range(count)
        ],
        "communityConnections": [
            {"sourceCommunityId": "c-0", "targetCommunityId": "c-3"},
            {"sourceCommunityId": "c-0", "targetCommunityId": "c-1"},
        ],
    }


def note_for(graph, agent_id, community_id=None):
    from types import SimpleNamespace

    from caller_runtime.services.conversation_policy import context_note

    snapshot = SimpleNamespace(for_runtime=lambda: graph)
    slot = SimpleNamespace(active_agent_id=agent_id, active_community_id=community_id)
    return context_note(snapshot, slot)


def test_context_note_names_the_agent_and_reachable_communities_without_prompts():
    parts = note_for(note_graph(5, description="d" * 400), "welcome")
    note = "".join(parts)
    assert note.startswith("Welcome (welcome)\nCommunities you can route to:\n")
    # Disabled and non-routable communities are left out; descriptions stop at 160 characters.
    assert "Community 0: " + "d" * 160 + "\n" in note
    assert "Community 1" not in note and "Community 2" not in note
    assert "d" * 161 not in note and "Secret" not in note
    entry = "".join(note_for(note_graph(5), "entry-0", "c-0"))
    assert entry.splitlines()[0] == "Entry (ordinary)" and "Community 3: " in entry
    assert "Community 1" not in entry and "Community 4" not in entry
    assert "".join(note_for(note_graph(5), "goodbye")).endswith(
        "Communities you can route to: none"
    )


def test_context_note_is_capped_at_2000_bytes_in_parts_of_500():
    parts = note_for(note_graph(60, description="é" * 160), "welcome")
    note = "".join(parts)
    assert all(len(part.encode("utf-8")) <= 500 for part in parts)
    assert len(note.encode("utf-8")) <= 2000
    shown = note.count("Community ")
    assert note.rstrip().endswith(f"…and {58 - shown} more communities")


@pytest.mark.parametrize("corruption", ["hosted", "over-budget", "duplicate-name"])
def test_agent_registry_rejects_hosted_or_over_budget_tools_without_provider_work(
    runtime_revision_v2, corruption
):
    from unittest.mock import Mock

    from agents import FunctionTool, WebSearchTool
    from agents.models.interface import Model

    from caller_runtime.domain.configuration import RevisionSnapshot
    from caller_runtime.services.conversation_agents import build_agents

    snapshot = RevisionSnapshot.validate(runtime_revision_v2(), etag='"agents-current"')
    before = json.dumps(snapshot.for_runtime(), sort_keys=True)

    async def invoke(context, arguments):
        raise AssertionError("registry_validation_executed_tool")

    if corruption == "hosted":
        tools = [WebSearchTool()]
    else:
        tools = [
            FunctionTool(
                name="duplicate" if corruption == "duplicate-name" else f"lookup_{index}",
                description="Synthetic tool",
                params_json_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                on_invoke_tool=invoke,
            )
            for index in range(129 if corruption == "over-budget" else 2)
        ]
    with pytest.raises(PermissionError, match="unapproved_agent_tools"):
        build_agents(
            snapshot,
            model=Mock(spec=Model),
            mode="text",
            capabilities=lambda _agent: tools,
            policy_tools=lambda _agent: [],
        )
    assert json.dumps(snapshot.for_runtime(), sort_keys=True) == before


@pytest.fixture
def limits(conversation_clock):
    from caller_runtime.domain.conversation import SessionLimits

    now = conversation_clock.utc_now()
    return SessionLimits(
        started_at=now,
        admission_deadline_utc=now + timedelta(seconds=10),
        auth_expires_at=now + timedelta(seconds=900),
    )


def test_ready_session_accepts_work_and_continuous_media_after_startup_expiry(
    limits, conversation_clock
):
    from caller_runtime.domain.conversation import WorkBudget

    conversation_clock.advance(4)
    limits.mark_ready(conversation_clock.utc_now())
    conversation_clock.advance(56)
    now = conversation_clock.utc_now()
    limits.require_media(now)
    work = WorkBudget.accept(limits, now)
    assert work.work_accepted_at == now
    assert work.work_deadline_utc == now + timedelta(seconds=10)
    assert work.remaining(now + timedelta(seconds=9)) == 1
    with pytest.raises(PermissionError, match="work_deadline_exceeded"):
        work.require(now + timedelta(seconds=10))
    limits.require_media(now + timedelta(seconds=11))


def test_first_ready_after_original_startup_deadline_rejects(limits, conversation_clock):
    conversation_clock.advance(10)
    with pytest.raises(PermissionError, match="admission_deadline_exceeded"):
        limits.mark_ready(conversation_clock.utc_now())
    assert limits.ready_at is None


def test_accepted_work_cannot_reset_its_queue_or_child_step_budget(limits, conversation_clock):
    from dataclasses import FrozenInstanceError

    from caller_runtime.domain.conversation import WorkBudget

    limits.mark_ready(conversation_clock.utc_now())
    accepted = WorkBudget.accept(limits, conversation_clock.utc_now())
    conversation_clock.advance(9)
    assert accepted.remaining(conversation_clock.utc_now()) == 1
    with pytest.raises(FrozenInstanceError):
        accepted.work_deadline_utc += timedelta(seconds=10)
    conversation_clock.advance(1)
    with pytest.raises(PermissionError, match="work_deadline_exceeded"):
        accepted.require(conversation_clock.utc_now())
    newer = WorkBudget.accept(limits, conversation_clock.utc_now())
    assert newer.work_deadline_utc > accepted.work_deadline_utc


@pytest.mark.parametrize("expiry", ["idle", "lifetime", "authentication", "terminal"])
def test_actual_session_expiry_still_denies_after_ready(limits, conversation_clock, expiry):
    limits.mark_ready(conversation_clock.utc_now())
    if expiry == "idle":
        conversation_clock.advance(120)
    elif expiry == "lifetime":
        for _index in range(5):
            conversation_clock.advance(110)
            limits.user_activity(conversation_clock.utc_now())
        conversation_clock.advance(50)
    elif expiry == "authentication":
        limits.auth_expires_at = conversation_clock.utc_now() + timedelta(seconds=1)
        conversation_clock.advance(1)
    else:
        limits.end()
    with pytest.raises(PermissionError):
        limits.require_media(conversation_clock.utc_now())


def test_idle_refresh_does_not_extend_already_accepted_work(limits, conversation_clock):
    from caller_runtime.domain.conversation import WorkBudget

    limits.mark_ready(conversation_clock.utc_now())
    conversation_clock.advance(115)
    work = WorkBudget.accept(limits, conversation_clock.utc_now())
    assert work.work_deadline_utc == limits.started_at + timedelta(seconds=120)
    conversation_clock.advance(4)
    limits.user_activity(conversation_clock.utc_now())
    assert limits.idle_expires_at > work.work_deadline_utc
    conversation_clock.advance(1)
    with pytest.raises(PermissionError, match="work_deadline_exceeded"):
        work.require(conversation_clock.utc_now())


def test_cleanup_budget_is_separate_after_all_business_deadlines_expire(limits, conversation_clock):
    limits.mark_ready(conversation_clock.utc_now())
    conversation_clock.advance(1000)
    limits.end()
    now = conversation_clock.utc_now()
    with pytest.raises(PermissionError):
        limits.require_media(now)
    assert limits.cleanup_deadline(now) == now + timedelta(seconds=10)
