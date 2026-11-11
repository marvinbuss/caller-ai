"""Fast phase-aware conversation deadlines; no real-minute sleeps or model calls."""

import asyncio
import json
from datetime import timedelta

import pytest

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
        await policy.enter_routing(work)
        await policy.route("alpha", work)
        await policy.transfer_community("beta", work)
        assert policy.active_agent_id == "routing" and policy.active_community_id == "alpha"
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
            text = "x" * 65537 if controls["oversized"] else "Synthetic shared streamed reply"
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
        assert requests[0]["instructions"] == "Synthetic prompt for b-entry"
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
        assert requests[1]["instructions"] == "Synthetic prompt for b-peer"
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
            assert requests[0]["instructions"] == "Synthetic welcome prompt"
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
        assert [request["instructions"] for request in requests] == [
            "Synthetic welcome prompt",
            "Synthetic routing prompt",
            "Synthetic prompt for b-entry",
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
            assert segments.current.intent.generation == policy.slot.generation > slot.generation
            assert (
                policy.slot.actor_key == slot.actor_key
                and policy.slot.expires_at == slot.expires_at
            )
            assert policy.slot.revision_id == slot.revision_id
            with pytest.raises(PermissionError):
                await segment.next_event()
            assert messages[0]["type"] == "generation.changed"
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
        assert controller.retained_bytes == 0 and adapter.disposed
        slots = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots
        if completed:
            assert not slots and await controller.close()
        else:
            assert slots[0].terminal_fence and slots[0].resources["model"].attempted == 1
            assert slots[0].resources["model"].settled == 0
            assert processing.done() and not await controller.close()

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


def test_failed_work_purges_pending_input_and_never_dispatches_expired_queue(
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
        assert messages[-1]["type"] == "error"
        assert controller.retained_bytes == 0 and adapter.disposed
        assert not await controller.process_next(emit=emit)
        assert (
            (await repository.read(deadline=asyncio.get_running_loop().time() + 1))
            .authority.slots[0]
            .terminal_fence
        )

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
    with pytest.raises(PermissionError, match="stale_generation"):
        flow.input(struct.pack(">BII", 1, 6, 8) + b"\0" * 960)
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
        await controller.process_next(emit=emit)
        assert len(requests) == 1 + int(clear)
        assert requests[0]["instructions"] == "Synthetic welcome prompt"
        assert "Synthetic correlated fragment text" in json.dumps(requests[0]["input"])
        if clear:
            assert requests[1]["instructions"] == "Synthetic welcome prompt"
            assert max(controls["timeouts"][1].values()) <= 6
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
                        "generation": 1,
                    }
                )
            )
        assert (
            wire.command(
                json.dumps(
                    {
                        "type": "audio.played",
                        "sequence": 1,
                        "sessionId": slot.session_id,
                        "audioSequence": 1,
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
        snapshot, model=model, capabilities=capabilities, policy_tools=lambda _agent: []
    )
    configured = snapshot.for_runtime()["agents"]
    assert set(agents) == {agent["agentId"] for agent in configured}
    for agent in configured:
        sdk = agents[agent["agentId"]]
        assert sdk.name == agent["name"] and sdk.instructions == agent["prompt"]
        assert sdk.model is model and sdk.handoffs == []
        assert sdk.model_settings.store is False and sdk.model_settings.parallel_tool_calls is False
        assert sdk.model_settings.retry.max_retries == 0
        assert len(sdk.tools) == (1 if agent["capabilityAssignments"] else 0)
    assert selected == [agent["agentId"] for agent in configured if agent["role"] == "ordinary"]


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
