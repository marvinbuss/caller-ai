"""Strict shared browser-test authority and independently verified cleanup contracts."""

import asyncio
import copy
import json
from datetime import timedelta

import pytest

from tests.doubles import model_reply, model_stream


@pytest.fixture
def authority_store():
    from caller_runtime.adapters.admission_repository import TestAdmissionRepository
    from tests.doubles import ContainerDouble

    container = ContainerDouble()
    return TestAdmissionRepository(container=container), container


@pytest.fixture
def live_case(admission_case):
    from types import SimpleNamespace

    from caller_runtime.adapters.gpt_live import GptLiveAdapter
    from tests.doubles import live_closed, live_started

    service, repository, container, clock, permit, cache = admission_case

    class Socket:
        def __init__(self):
            self.events, self.sent = asyncio.Queue(), []
            self.closed = 0
            self.close_reply = True

        async def send(self, raw):
            event = json.loads(raw)
            self.sent.append(event)
            if event["type"] == "session.start":
                self.start = event
                await self.events.put(live_started(event, "sess-case-fixture"))
            elif event["type"] in {"session.input_audio.mute", "session.input_audio.unmute"}:
                await self.events.put(
                    json.dumps({"type": event["type"] + "d", "event_id": "event_mute"})
                )
            elif event["type"] == "session.close" and self.close_reply:
                await self.events.put(live_closed(self.start, "sess-case-fixture", 3))

        async def recv(self):
            return await self.events.get()

        async def close(self):
            self.closed += 1

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-live-token", expires_on=clock.utc_now().timestamp() + 900
            )

    socket = Socket()

    async def connect(url, **options):
        return socket

    adapter = GptLiveAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        voice="marin",
        socket_factory=connect,
    )

    async def opened():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1, mode="speech"))
        session = await adapter.start(slot, instructions="Synthetic voice instructions")
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        return slot, session

    return service, repository, clock, socket, opened


def test_live_pcm_mute_and_delegation_context_use_only_documented_session_events(live_case):
    import base64

    service, repository, clock, socket, opened = live_case

    async def scenario():
        slot, session = await opened()
        pcm = b"\x01\x00" * 480
        await session.send_audio(pcm)
        assert socket.sent[-1] == {
            "type": "session.input_audio.append",
            "audio": base64.b64encode(pcm).decode(),
        }
        await session.set_muted(True)
        assert (await session.next_event())["type"] == "session.input_audio.muted"
        await session.set_muted(False)
        assert (await session.next_event())["type"] == "session.input_audio.unmuted"
        await socket.events.put(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "offset_ms": 10,
                    "delegation": {"id": "item-case", "type": "delegation", "target": "client"},
                }
            )
        )
        event = await session.next_event()
        assert event["delegation"]["id"] == "item-case" and "task" not in event
        await session.append_context("item-case", "Synthetic reviewed facts")
        assert socket.sent[-1]["type"] == "session.commentary.append"
        assert socket.sent[-1]["delegation_id"] == "item-case"
        assert (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0].idle_expires_at == slot.idle_expires_at
        await session.close()

    asyncio.run(scenario())


def test_live_context_ack_and_invalid_input_never_mean_task_or_playback_success(live_case):
    service, repository, clock, socket, opened = live_case

    async def scenario():
        slot, session = await opened()
        before = len(socket.sent)
        with pytest.raises(PermissionError):
            await session.send_audio(b"\0")
        with pytest.raises(PermissionError):
            await session.append_context("unknown-delegation", "Synthetic facts")
        assert len(socket.sent) == before
        await session.set_muted(True)
        await session.next_event()
        with pytest.raises(PermissionError, match="input_muted"):
            await session.send_audio(b"\0\0" * 480)
        await socket.events.put(
            json.dumps(
                {
                    "type": "session.delegation.created",
                    "offset_ms": 10,
                    "delegation": {"id": "item-case", "type": "delegation", "target": "client"},
                }
            )
        )
        await session.next_event()
        with pytest.raises(PermissionError):
            await session.append_context("item-case", "x" * 501)
        await session.append_context("item-case", "Synthetic reviewed facts", quiet=True)
        assert socket.sent[-1]["type"] == "session.thinking.append"
        await socket.events.put(
            json.dumps({"type": "session.thinking.appended", "start_ms": 10, "end_ms": 20})
        )
        acknowledgement = await session.next_event()
        assert acknowledgement["type"] == "session.thinking.appended"
        assert not {"completed", "played", "success"}.intersection(acknowledgement)
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.settled_through == 0 and observed.idle_expires_at == slot.idle_expires_at
        await session.close()

    asyncio.run(scenario())


def test_live_late_generation_audio_is_fenced_without_relabelling(live_case):
    import base64

    service, repository, clock, socket, opened = live_case

    async def scenario():
        slot, session = await opened()
        await service.mark_terminal(slot.session_id, slot.owner_epoch)
        await socket.events.put(
            json.dumps(
                {
                    "type": "session.output_audio.delta",
                    "delta": base64.b64encode(b"\0\0" * 480).decode(),
                }
            )
        )
        with pytest.raises(PermissionError):
            await session.next_event()
        assert session.intent.generation == 1
        await session.close()

    asyncio.run(scenario())


def test_live_local_socket_disposal_without_session_closed_never_settles(live_case):
    service, repository, clock, socket, opened = live_case

    async def scenario():
        slot, session = await opened()
        socket.close_reply = False
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(session.close(), timeout=0.1)
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert socket.closed == 1 and observed.live_ever_attempted
        assert observed.closure_receipt is None and observed.settled_through == 0

    asyncio.run(scenario())


@pytest.fixture
def admission_case(authority_store, conversation_clock, runtime_revision_v2):
    from caller_runtime.domain.configuration import RevisionCache
    from caller_runtime.domain.conversation import StartPermit
    from caller_runtime.services.admission import TestAdmissionService

    repository, container = authority_store
    cache = RevisionCache(monotonic=conversation_clock.monotonic)
    cache.install(runtime_revision_v2(), etag='"admission-current"')
    service = TestAdmissionService(
        repository=repository,
        configuration=cache,
        owner_id="runtime-owner-fixture",
        utc_now=conversation_clock.utc_now,
        monotonic=conversation_clock.monotonic,
        max_clock_skew_seconds=0,
    )

    def permit(number, *, actor="manager-fixture", mode="text"):
        now = conversation_clock.utc_now()
        return StartPermit.model_validate_json(
            json.dumps(
                {
                    "iss": "https://management.example.invalid/test-permits",
                    "aud": "runtime-fixture",
                    "iat": int(now.timestamp()),
                    "exp": int(now.timestamp()) + 30,
                    "issuedAt": now.isoformat(),
                    "admissionStartedAt": now.isoformat(),
                    "admissionDeadlineUtc": (now + timedelta(seconds=10)).isoformat(),
                    "tenantId": "tenant-fixture",
                    "actorId": actor,
                    "mode": mode,
                    "managementSessionDigest": f"{number:064x}",
                    "requestId": f"request-{number}",
                    "nonce": f"{number:064x}",
                    "authExpiresAt": (now + timedelta(seconds=900)).isoformat(),
                }
            )
        )

    return service, repository, container, conversation_clock, permit, cache


def test_admission_one_actor_across_cookie_and_mode_and_single_use_nonce(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        original_permit = permit(1)
        admitted = await service.reserve(original_permit)
        with pytest.raises(AdmissionError, match="manager_busy"):
            await service.reserve(permit(2, mode="speech"))
        with pytest.raises(AdmissionError, match="permit_replayed"):
            await service.reserve(original_permit)
        authority = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority
        assert [slot.session_id for slot in authority.slots] == [admitted.session_id]
        assert original_permit.nonce_digest in authority.consumed_nonces
        assert permit(2, mode="speech").nonce_digest in authority.consumed_nonces

    asyncio.run(scenario())


def test_five_global_slots_remain_occupied_after_lifetime_or_lease_time(
    admission_case, runtime_revision_v2
):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        for number in range(1, 6):
            await service.reserve(permit(number, actor=f"manager-{number}"))
        clock.advance(1000)
        cache.install(runtime_revision_v2(2), etag='"new-current"')
        with pytest.raises(AdmissionError, match="capacity_unavailable"):
            await service.reserve(permit(6, actor="manager-six"))
        assert (
            len(
                (
                    await repository.read(deadline=asyncio.get_running_loop().time() + 1)
                ).authority.slots
            )
            == 5
        )

    asyncio.run(scenario())


def test_admission_never_initializes_missing_authority(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case
    with pytest.raises(AdmissionError, match="authority_unavailable"):
        asyncio.run(service.reserve(permit(1)))
    assert container.records == {} and all(call[0] == "read" for call in container.calls)


def test_admission_conflicts_are_four_attempts_without_replacing_session(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        container.conflict_on_replace = True
        with pytest.raises(AdmissionError, match="authority_conflict"):
            await service.reserve(permit(1))
        assert len([call for call in container.calls if call[0] == "replace"]) == 4
        assert container.records["browser-test-authority-v1"]["slots"] == []

    asyncio.run(scenario())


def test_intent_is_persisted_confirmed_and_terminal_fence_denies_further_attempts(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        intent = await service.begin_attempt(
            slot.session_id,
            slot.owner_epoch,
            kind="model",
            binding_hash="d" * 64,
            generation=1,
        )
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.resources["model"].attempt == intent and observed.attempted_through == 1
        assert container.calls[-1][0] == "read"
        await service.mark_terminal(slot.session_id, slot.owner_epoch)
        with pytest.raises(AdmissionError):
            await service.begin_attempt(
                slot.session_id,
                slot.owner_epoch,
                kind="capability",
                binding_hash="e" * 64,
                generation=1,
            )
        assert (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0].attempted_through == 1

    asyncio.run(scenario())


def test_text_admission_cannot_record_a_live_allocation(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        with pytest.raises(AdmissionError, match="mode_mismatch"):
            await service.begin_attempt(
                slot.session_id, slot.owner_epoch, kind="live", binding_hash="e" * 64, generation=1
            )
        assert (
            not (await repository.read(deadline=asyncio.get_running_loop().time() + 1))
            .authority.slots[0]
            .live_ever_attempted
        )

    asyncio.run(scenario())


def test_two_instances_contend_same_actor_from_the_same_etag(admission_case, monkeypatch):
    from caller_runtime.services.admission import AdmissionError, TestAdmissionService

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        second = TestAdmissionService(
            repository=repository,
            configuration=cache,
            owner_id="other-owner-fixture",
            utc_now=clock.utc_now,
            monotonic=clock.monotonic,
            max_clock_skew_seconds=0,
        )
        original = container.read_item
        release, reads = asyncio.Event(), [0]

        async def raced_read(**keywords):
            observed = await original(**keywords)
            if reads[0] < 2:
                reads[0] += 1
                if reads[0] == 2:
                    release.set()
                await release.wait()
            return observed

        monkeypatch.setattr(container, "read_item", raced_read)
        outcomes = await asyncio.gather(
            service.reserve(permit(1)), second.reserve(permit(2)), return_exceptions=True
        )
        assert sum(not isinstance(outcome, BaseException) for outcome in outcomes) == 1
        denied = next(outcome for outcome in outcomes if isinstance(outcome, AdmissionError))
        assert denied.code == "manager_busy"
        assert (
            len(
                (
                    await repository.read(deadline=asyncio.get_running_loop().time() + 1)
                ).authority.slots
            )
            == 1
        )

    asyncio.run(scenario())


def test_stop_during_intent_confirmation_prevents_dispatch_without_clearing_slot(
    admission_case, monkeypatch
):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        original = container.read_item
        confirming, release = asyncio.Event(), asyncio.Event()
        blocked = [False]
        dispatched = []

        async def pause_confirmation(**keywords):
            observed = await original(**keywords)
            if observed["slots"][0]["attemptedThrough"] == 1 and not blocked[0]:
                blocked[0] = True
                confirming.set()
                await release.wait()
            return observed

        async def dispatch():
            await service.begin_attempt(
                slot.session_id, slot.owner_epoch, kind="model", binding_hash="d" * 64, generation=1
            )
            dispatched.append(True)

        monkeypatch.setattr(container, "read_item", pause_confirmation)
        attempt = asyncio.create_task(dispatch())
        await confirming.wait()
        terminal = asyncio.create_task(service.mark_terminal(slot.session_id, slot.owner_epoch))
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(AdmissionError, match="terminal"):
            await attempt
        await terminal
        assert dispatched == []
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.terminal_fence and observed.attempted_through == 1

    asyncio.run(scenario())


def test_lost_reservation_reply_confirms_only_the_same_local_operation(admission_case, monkeypatch):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        original = container.replace_item
        failed = [False]

        async def lose_first_reply(**keywords):
            observed = await original(**keywords)
            if not failed[0]:
                failed[0] = True
                raise RuntimeError("synthetic-private-reservation-reply")
            return observed

        monkeypatch.setattr(container, "replace_item", lose_first_reply)
        assertion = permit(1)
        slot = await service.reserve(assertion)
        assert len([call for call in container.calls if call[0] == "replace"]) == 1
        assert (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0].session_id == slot.session_id
        with pytest.raises(AdmissionError, match="permit_replayed"):
            await service.reserve(assertion)

    asyncio.run(scenario())


def test_authoritative_ready_session_allows_media_and_new_work_at_sixty_seconds(admission_case):
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        clock.advance(4)
        ready = await service.mark_ready(slot.session_id, slot.owner_epoch)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        clock.advance(56)
        active = await service.check_media(
            slot.session_id, slot.owner_epoch, generation=ready.generation
        )
        assert (
            active.ready_at == ready.ready_at
            and active.admission_deadline_utc == slot.admission_deadline_utc
        )
        work = WorkBudget.accept(limits, clock.utc_now())
        intent = await service.begin_attempt(
            slot.session_id,
            slot.owner_epoch,
            kind="model",
            binding_hash="d" * 64,
            generation=ready.generation,
            work=work,
        )
        assert intent.recorded_at == clock.utc_now()
        clock.advance(10)
        with pytest.raises(AdmissionError, match="work_deadline_exceeded"):
            await service.begin_attempt(
                slot.session_id,
                slot.owner_epoch,
                kind="capability",
                binding_hash="e" * 64,
                generation=ready.generation,
                work=work,
            )

    asyncio.run(scenario())


def test_initial_readiness_is_rejected_after_startup_even_when_permit_is_not_expired(
    admission_case,
):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        clock.advance(10)
        with pytest.raises(AdmissionError, match="admission_deadline_exceeded"):
            await service.mark_ready(slot.session_id, slot.owner_epoch)
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.ready_at is None

    asyncio.run(scenario())


def test_output_authority_check_is_bounded_and_terminal_generation_is_denied(
    admission_case, monkeypatch
):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        deadlines = []
        original = repository.read

        async def measured(*, deadline):
            deadlines.append(deadline - asyncio.get_running_loop().time())
            return await original(deadline=deadline)

        monkeypatch.setattr(repository, "read", measured)
        await service.check_output(slot.session_id, slot.owner_epoch, generation=slot.generation)
        assert deadlines and max(deadlines) <= 1
        await service.mark_terminal(slot.session_id, slot.owner_epoch)
        with pytest.raises(AdmissionError):
            await service.check_output(
                slot.session_id, slot.owner_epoch, generation=slot.generation
            )

    asyncio.run(scenario())


def test_authority_watcher_checks_without_extending_any_session_deadline(admission_case):
    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        stopped = asyncio.Event()
        reads = len(container.calls)

        async def end_after_check(observed):
            assert observed.admission_deadline_utc == slot.admission_deadline_utc
            assert (
                observed.expires_at == slot.expires_at
                and observed.idle_expires_at == slot.idle_expires_at
            )
            stopped.set()

        await service.watch(
            slot.session_id, slot.owner_epoch, stopped=stopped, observed=end_after_check
        )
        assert len(container.calls) > reads

    asyncio.run(scenario())


def test_authority_watcher_callback_cannot_block_rechecks_indefinitely(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)

        async def stalled(observed):
            await asyncio.Event().wait()

        with pytest.raises(AdmissionError, match="authority_unavailable"):
            await asyncio.wait_for(
                service.watch(
                    slot.session_id, slot.owner_epoch, stopped=asyncio.Event(), observed=stalled
                ),
                timeout=0.5,
            )

    asyncio.run(scenario())


def test_confirmed_intent_fenced_before_actual_dispatch_never_invokes_operation(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        intent = await service.begin_attempt(
            slot.session_id, slot.owner_epoch, kind="model", binding_hash="d" * 64, generation=1
        )
        await service.mark_terminal(slot.session_id, slot.owner_epoch)
        effects = []

        async def external():
            effects.append(True)
            return "synthetic result"

        with pytest.raises(AdmissionError):
            await service.perform(intent, external)
        assert effects == []

    asyncio.run(scenario())


def test_cancelled_external_operation_never_becomes_settled_or_replayed(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        intent = await service.begin_attempt(
            slot.session_id, slot.owner_epoch, kind="model", binding_hash="d" * 64, generation=1
        )
        entered = asyncio.Event()
        effects = []

        async def external():
            effects.append(True)
            entered.set()
            await asyncio.Event().wait()

        request = asyncio.create_task(service.perform(intent, external))
        await asyncio.wait_for(entered.wait(), timeout=0.5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert (
            effects == [True] and observed.attempted_through == 1 and observed.settled_through == 0
        )
        with pytest.raises(AdmissionError, match="attempt_already_dispatched"):
            await service.perform(intent, external)
        assert effects == [True]

    asyncio.run(scenario())


def test_only_accepted_mode_matched_user_activity_refreshes_idle(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        clock.advance(60)
        unchanged = await service.check_media(slot.session_id, slot.owner_epoch, generation=1)
        assert unchanged.idle_expires_at == slot.idle_expires_at
        with pytest.raises(AdmissionError, match="mode_mismatch"):
            await service.user_activity(
                slot.session_id, slot.owner_epoch, generation=1, mode="speech"
            )
        updated = await service.user_activity(
            slot.session_id, slot.owner_epoch, generation=1, mode="text"
        )
        assert updated.last_user_at == clock.utc_now()
        assert updated.idle_expires_at == clock.utc_now() + timedelta(seconds=120)
        assert (
            updated.expires_at == slot.expires_at
            and updated.admission_deadline_utc == slot.admission_deadline_utc
        )
        clock.advance(120)
        with pytest.raises(AdmissionError):
            await service.user_activity(
                slot.session_id, slot.owner_epoch, generation=1, mode="text"
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.openai.com",
        "http://approved-fixture.openai.azure.com",
        "https://approved-fixture.openai.azure.com/override",
    ],
)
def test_foundry_text_binding_rejects_public_or_noncanonical_provider_before_credentials(
    admission_case, endpoint
):
    from caller_runtime.adapters.foundry_text import FoundryTextAdapter

    service, repository, container, clock, permit, cache = admission_case

    class Credentials:
        calls = 0

        async def get_token(self, scope):
            self.calls += 1
            raise AssertionError("binding_validation_requested_credentials")

    credentials = Credentials()
    with pytest.raises(ValueError, match="invalid_foundry_text_binding"):
        FoundryTextAdapter(
            endpoint=endpoint,
            deployment="reasoning-fixture",
            approved_hosts=frozenset({"approved-fixture.openai.azure.com", "api.openai.com"}),
            credential=credentials,
            admission=service,
            utc_now=clock.utc_now,
            max_input_tokens=16384,
            max_output_tokens=512,
        )
    assert credentials.calls == 0


def test_foundry_text_uses_explicit_chat_model_and_confirmed_intent_before_http(admission_case):
    from types import SimpleNamespace

    import httpx2
    from agents import ModelSettings
    from agents.models.interface import ModelTracing
    from agents.retry import ModelRetrySettings

    from caller_runtime.adapters.foundry_text import FoundryTextAdapter
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget

    service, repository, container, clock, permit, cache = admission_case
    requests, scopes = [], []

    class Credentials:
        async def get_token(self, scope):
            scopes.append(scope)
            return SimpleNamespace(
                token="synthetic-foundry-token", expires_on=clock.utc_now().timestamp() + 900
            )

    async def response(request):
        requests.append(request)
        body = json.loads(request.content)
        authority = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority
        assert authority.slots[0].resources["model"].attempt is not None
        # Reasoning deployments accept function tools only on the Responses API.
        assert request.url == "https://approved-fixture.openai.azure.com/openai/v1/responses"
        assert request.headers["authorization"] == "Bearer synthetic-foundry-token"
        assert body["model"] == "reasoning-fixture" and body["store"] is False
        assert body.get("parallel_tool_calls") in {False, None}
        assert body["max_output_tokens"] == 512 and "max_tokens" not in body
        return httpx2.Response(200, json=model_reply("Synthetic shared reply"))

    adapter = FoundryTextAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        deployment="reasoning-fixture",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        transport=httpx2.MockTransport(response),
        max_input_tokens=16384,
        max_output_tokens=512,
    )
    assert scopes == requests == []

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        model = adapter.model_for(slot, WorkBudget.accept(limits, clock.utc_now()))
        try:
            result = await model.get_response(
                system_instructions="Synthetic configured instructions",
                input="Synthetic accepted question",
                model_settings=ModelSettings(retry=ModelRetrySettings(max_retries=0)),
                tools=[],
                output_schema=None,
                handoffs=[],
                tracing=ModelTracing.DISABLED,
            )
            message = next(output for output in result.output if output.type == "message")
            assert message.content[0].text == "Synthetic shared reply"
            assert scopes == ["https://ai.azure.com/.default"] and len(requests) == 1
            observed = (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots[0]
            assert observed.attempted_through == observed.settled_through == 1
            await service.mark_terminal(slot.session_id, slot.owner_epoch)
            with pytest.raises(PermissionError):
                await model.get_response(
                    system_instructions="Synthetic configured instructions",
                    input="Synthetic late question",
                    model_settings=ModelSettings(),
                    tools=[],
                    output_schema=None,
                    handoffs=[],
                    tracing=ModelTracing.DISABLED,
                )
            assert len(requests) == 1
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_foundry_text_does_not_retry_or_follow_redirect_after_recorded_attempt(admission_case):
    from types import SimpleNamespace

    import httpx2
    from agents import ModelSettings
    from agents.models.interface import ModelTracing

    from caller_runtime.adapters.foundry_text import FoundryTextAdapter
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget

    service, repository, container, clock, permit, cache = admission_case
    requests = []

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-foundry-token", expires_on=clock.utc_now().timestamp() + 900
            )

    async def redirect(request):
        requests.append(request)
        return httpx2.Response(
            307, headers={"Location": "https://unapproved.example.invalid/secret"}
        )

    adapter = FoundryTextAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        deployment="reasoning-fixture",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        transport=httpx2.MockTransport(redirect),
        max_input_tokens=16384,
        max_output_tokens=512,
    )

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        model = adapter.model_for(slot, WorkBudget.accept(limits, clock.utc_now()))
        try:
            with pytest.raises(PermissionError, match="model_unavailable") as failure:
                await model.get_response(
                    system_instructions="Synthetic instructions",
                    input="Synthetic accepted question",
                    model_settings=ModelSettings(),
                    tools=[],
                    output_schema=None,
                    handoffs=[],
                    tracing=ModelTracing.DISABLED,
                )
            assert "unapproved" not in str(failure.value)
            assert len(requests) == 1
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "settled"), [(400, True), (429, True), (500, False), (504, False)]
)
@pytest.mark.parametrize("stream", [False, True])
def test_foundry_text_settles_only_a_definite_provider_refusal(
    admission_case, status, settled, stream
):
    from types import SimpleNamespace

    import httpx2
    from agents import ModelSettings
    from agents.models.interface import ModelTracing

    from caller_runtime.adapters.foundry_text import FoundryTextAdapter
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget

    service, repository, container, clock, permit, cache = admission_case
    requests = []

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-foundry-token", expires_on=clock.utc_now().timestamp() + 900
            )

    async def refuse(request):
        requests.append(request)
        return httpx2.Response(
            status,
            json={"error": {"code": "unsupported_parameter", "type": "invalid_request_error"}},
        )

    adapter = FoundryTextAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        deployment="reasoning-fixture",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        transport=httpx2.MockTransport(refuse),
        max_input_tokens=16384,
        max_output_tokens=512,
    )

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        model = adapter.model_for(slot, WorkBudget.accept(limits, clock.utc_now()))
        arguments = dict(
            system_instructions="Synthetic instructions",
            input="Synthetic accepted question",
            model_settings=ModelSettings(),
            tools=[],
            output_schema=None,
            handoffs=[],
            tracing=ModelTracing.DISABLED,
        )
        try:
            with pytest.raises(PermissionError, match="model_unavailable"):
                if stream:
                    async for _event in model.stream_response(**arguments):
                        pass
                else:
                    await model.get_response(**arguments)
            assert len(requests) == 1
            observed = (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots[0]
            assert observed.attempted_through == 1
            assert observed.settled_through == (1 if settled else 0)
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("complete", [True, False])
def test_foundry_text_stream_is_gated_and_early_close_never_proves_settlement(
    admission_case, complete
):
    from types import SimpleNamespace

    import httpx2
    from agents import ModelSettings
    from agents.models.interface import ModelTracing

    from caller_runtime.adapters.foundry_text import FoundryTextAdapter
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget

    service, repository, container, clock, permit, cache = admission_case
    requests = []

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-foundry-token", expires_on=clock.utc_now().timestamp() + 900
            )

    class Chunks(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for chunk in model_stream("Synthetic streamed reply"):
                yield chunk

    async def stream(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["stream"] is True and body["store"] is False
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.attempted_through == 1 and observed.settled_through == 0
        return httpx2.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Chunks())

    adapter = FoundryTextAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        deployment="reasoning-fixture",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        transport=httpx2.MockTransport(stream),
        max_input_tokens=16384,
        max_output_tokens=512,
    )

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        iterator = adapter.model_for(
            slot, WorkBudget.accept(limits, clock.utc_now())
        ).stream_response(
            system_instructions="Synthetic configured instructions",
            input="Synthetic accepted question",
            model_settings=ModelSettings(),
            tools=[],
            output_schema=None,
            handoffs=[],
            tracing=ModelTracing.DISABLED,
        )
        try:
            if complete:
                events = [event async for event in iterator]
                assert any(event.type == "response.completed" for event in events)
                assert (
                    "".join(
                        event.delta
                        for event in events
                        if event.type == "response.output_text.delta"
                    )
                    == "Synthetic streamed reply"
                )
            else:
                await anext(iterator)
                await iterator.aclose()
            observed = (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots[0]
            assert observed.attempted_through == 1 and observed.settled_through == int(complete)
            assert len(requests) == 1 and all(
                str(request.url).endswith("/openai/v1/responses") for request in requests
            )
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_foundry_text_caps_transport_body_before_sdk_json_parse(admission_case):
    from types import SimpleNamespace

    import httpx2
    from agents import ModelSettings
    from agents.models.interface import ModelTracing

    from caller_runtime.adapters.foundry_text import FoundryTextAdapter
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget

    service, repository, container, clock, permit, cache = admission_case
    chunks = []

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-foundry-token", expires_on=clock.utc_now().timestamp() + 900
            )

    class Huge(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for index in range(12):
                chunks.append(index)
                yield b" " * (64 * 1024)

    async def oversized(request):
        return httpx2.Response(200, headers={"Content-Type": "application/json"}, stream=Huge())

    adapter = FoundryTextAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        deployment="reasoning-fixture",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        transport=httpx2.MockTransport(oversized),
        max_input_tokens=16384,
        max_output_tokens=512,
    )

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        try:
            with pytest.raises(PermissionError, match="model_unavailable"):
                await adapter.model_for(
                    slot, WorkBudget.accept(limits, clock.utc_now())
                ).get_response(
                    system_instructions="Synthetic instructions",
                    input="Synthetic accepted question",
                    model_settings=ModelSettings(),
                    tools=[],
                    output_schema=None,
                    handoffs=[],
                    tracing=ModelTracing.DISABLED,
                )
            assert len(chunks) <= 5, "Provider response was buffered before applying its byte limit"
            observed = (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots[0]
            assert observed.attempted_through == 1 and observed.settled_through == 0
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "override",
    [
        {"extra_body": {"store": True}},
        {"extra_headers": {"Authorization": "synthetic-forbidden-override"}},
    ],
)
def test_foundry_text_request_overrides_reject_before_credentials_or_model_intent(
    admission_case, override
):
    from agents import ModelSettings
    from agents.models.interface import ModelTracing

    from caller_runtime.adapters.foundry_text import FoundryTextAdapter
    from caller_runtime.domain.conversation import SessionLimits, WorkBudget

    service, repository, container, clock, permit, cache = admission_case

    class Credentials:
        calls = 0

        async def get_token(self, scope):
            self.calls += 1
            raise AssertionError("unsafe_request_acquired_credentials")

    credentials = Credentials()
    adapter = FoundryTextAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        deployment="reasoning-fixture",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=credentials,
        admission=service,
        utc_now=clock.utc_now,
        max_input_tokens=16384,
        max_output_tokens=512,
    )

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        await service.mark_ready(slot.session_id, slot.owner_epoch)
        limits = SessionLimits(
            started_at=slot.started_at,
            admission_deadline_utc=slot.admission_deadline_utc,
            auth_expires_at=slot.auth_expires_at,
        )
        limits.mark_ready(clock.utc_now())
        try:
            with pytest.raises(PermissionError, match="unapproved_model_request"):
                await adapter.model_for(
                    slot, WorkBudget.accept(limits, clock.utc_now())
                ).get_response(
                    system_instructions="Synthetic instructions",
                    input="Synthetic accepted question",
                    model_settings=ModelSettings(**override),
                    tools=[],
                    output_schema=None,
                    handoffs=[],
                    tracing=ModelTracing.DISABLED,
                )
            assert credentials.calls == 0
            assert (
                await repository.read(deadline=asyncio.get_running_loop().time() + 1)
            ).authority.slots[0].attempted_through == 0
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.openai.com",
        "http://approved-fixture.openai.azure.com",
        "https://approved-fixture.openai.azure.com/override",
    ],
)
def test_gpt_live_binding_is_explicit_azure_only_and_credential_lazy(admission_case, endpoint):
    from caller_runtime.adapters.gpt_live import GptLiveAdapter

    service, repository, container, clock, permit, cache = admission_case

    class Credentials:
        calls = 0

        async def get_token(self, scope):
            self.calls += 1
            raise AssertionError("invalid_live_binding_requested_credentials")

    credential = Credentials()
    with pytest.raises(ValueError, match="invalid_live_binding"):
        GptLiveAdapter(
            endpoint=endpoint,
            approved_hosts=frozenset({"api.openai.com", "approved-fixture.openai.azure.com"}),
            credential=credential,
            admission=service,
            utc_now=clock.utc_now,
            voice="marin",
        )
    assert credential.calls == 0


def test_gpt_live_connection_confirms_intent_and_exact_started_binding_before_audio(admission_case):
    from types import SimpleNamespace

    from caller_runtime.adapters.gpt_live import GptLiveAdapter
    from tests.doubles import live_closed, live_started

    service, repository, container, clock, permit, cache = admission_case
    scopes, connects, messages = [], [], []

    class Credentials:
        async def get_token(self, scope):
            scopes.append(scope)
            return SimpleNamespace(
                token="synthetic-live-token", expires_on=clock.utc_now().timestamp() + 900
            )

    class Socket:
        closed = 0

        def __init__(self):
            self.events = asyncio.Queue()

        async def send(self, raw):
            event = json.loads(raw)
            messages.append(event)
            if event["type"] == "session.start":
                self.start = event
                await self.events.put(live_started(event, "sess-live-fixture"))
            elif event["type"] == "session.close":
                await self.events.put(live_closed(self.start, "sess-live-fixture", 3))

        async def recv(self):
            return await self.events.get()

        async def close(self):
            self.closed += 1

    socket = Socket()

    async def connect(url, **options):
        connects.append((url, options))
        authority = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority
        assert authority.slots[0].live_ever_attempted
        assert authority.slots[0].resources["live"].attempt is not None
        assert url == "https://approved-fixture.openai.azure.com/openai/v1"
        assert options["token"] == "synthetic-live-token"
        assert options["max_size"] == 256 * 1024 and options["max_queue"] == 1
        assert options["proxy"] is None and options["compression"] is None
        assert options["ssl"].check_hostname
        return socket

    adapter = GptLiveAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        voice="marin",
        socket_factory=connect,
    )
    assert scopes == connects == messages == []

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1, mode="speech"))
        session = await adapter.start(slot, instructions="Synthetic approved speech instructions")
        assert session.provider_session_id == "sess-live-fixture"
        assert messages[0]["session"]["model"] == "gpt-live-1"
        assert messages[0]["session"]["delegation"] == {"type": "client"}
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.resources["live"].attempt.provider_session_id == session.provider_session_id
        receipt = await session.close()
        assert receipt.provider_session_id == "sess-live-fixture" and receipt.authenticated
        assert receipt.voice_seconds == 3 and receipt.owner_epoch == slot.owner_epoch
        assert socket.closed == 1 and len(connects) == 1
        assert scopes == ["https://ai.azure.com/.default"]
        settled = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert settled.resources["live"].settled == settled.resources["live"].attempted == 1
        assert settled.closure_receipt == receipt

    asyncio.run(scenario())


def test_gpt_live_unknown_connect_never_retries_or_claims_zero_allocation(admission_case):
    from types import SimpleNamespace

    from caller_runtime.adapters.gpt_live import GptLiveAdapter

    service, repository, container, clock, permit, cache = admission_case
    attempts = []

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-live-token", expires_on=clock.utc_now().timestamp() + 900
            )

    async def unknown(url, **options):
        attempts.append(True)
        raise RuntimeError("synthetic-private-live-connect-details")

    adapter = GptLiveAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        voice="marin",
        socket_factory=unknown,
    )

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1, mode="speech"))
        with pytest.raises(PermissionError, match="live_unavailable") as failure:
            await adapter.start(slot, instructions="Synthetic instructions")
        assert "synthetic-private" not in str(failure.value)
        assert attempts == [True]
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.live_ever_attempted and observed.attempted_through == 1
        assert observed.settled_through == 0 and observed.closure_receipt is None

    asyncio.run(scenario())


def test_live_provider_binding_requires_dispatched_exact_intent_and_never_rebinds(admission_case):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1, mode="speech"))
        intent = await service.begin_attempt(
            slot.session_id, slot.owner_epoch, kind="live", binding_hash="d" * 64, generation=1
        )
        with pytest.raises(AdmissionError):
            await service.bind_live_session(intent, provider_session_id="sess-fixture")

        async def connected():
            return True

        await service.perform(intent, connected)
        bound = await service.bind_live_session(intent, provider_session_id="sess-fixture")
        assert bound.provider_session_id == "sess-fixture" and bound.attempt_id == intent.attempt_id
        assert (await service.bind_live_session(bound, provider_session_id="sess-fixture")) == bound
        with pytest.raises(AdmissionError):
            await service.bind_live_session(bound, provider_session_id="sess-foreign")
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.resources["live"].attempt == bound

    asyncio.run(scenario())


def test_gpt_live_wrong_started_configuration_retains_unknown_attempt_without_receipt(
    admission_case,
):
    from types import SimpleNamespace

    from caller_runtime.adapters.gpt_live import GptLiveAdapter

    service, repository, container, clock, permit, cache = admission_case

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(
                token="synthetic-live-token", expires_on=clock.utc_now().timestamp() + 900
            )

    class Socket:
        closed = 0
        configuration = None

        async def send(self, raw):
            self.configuration = json.loads(raw)["session"]

        async def recv(self):
            return json.dumps(
                {
                    "type": "session.started",
                    "session": {
                        "id": "sess-wrong-fixture",
                        **self.configuration,
                        "delegation": {"type": "responses"},
                    },
                }
            )

        async def close(self):
            self.closed += 1

    socket = Socket()

    async def connect(url, **options):
        return socket

    adapter = GptLiveAdapter(
        endpoint="https://approved-fixture.openai.azure.com",
        approved_hosts=frozenset({"approved-fixture.openai.azure.com"}),
        credential=Credentials(),
        admission=service,
        utc_now=clock.utc_now,
        voice="marin",
        socket_factory=connect,
    )

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1, mode="speech"))
        with pytest.raises(PermissionError, match="live_unavailable"):
            await adapter.start(slot, instructions="Synthetic voice instructions")
        observed = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert observed.live_ever_attempted and observed.settled_through == 0
        assert observed.resources["live"].attempt.provider_session_id is None
        assert observed.closure_receipt is None and socket.closed == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("attempted", [False, True])
def test_resource_cleanup_verifier_requires_independent_matching_disposal_and_settlement(
    admission_case, attempted
):
    from caller_runtime.domain.conversation import CleanupObservation
    from caller_runtime.services.cleanup import ResourceCleanupVerifier

    service, repository, container, clock, permit, cache = admission_case
    observations = []

    async def observe(slot, deadline):
        assert deadline > asyncio.get_running_loop().time()
        return observations[-1] if observations else None

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        if attempted:
            intent = await service.begin_attempt(
                slot.session_id, slot.owner_epoch, kind="model", binding_hash="1" * 64, generation=1
            )

            async def completed():
                return "synthetic-complete"

            await service.perform(intent, completed)
            await service.settle_attempt(intent, settlement_ref="model-observed")
        terminal = await service.mark_terminal(slot.session_id, slot.owner_epoch)
        evidence = cleanup_for(
            terminal, clock.utc_now(), kind="work_disposed" if attempted else "no_external_attempt"
        )
        verifier = ResourceCleanupVerifier(observe=observe, utc_now=clock.utc_now)
        deadline = asyncio.get_running_loop().time() + 1
        assert await verifier.verify(terminal, evidence, deadline) == "unknown"
        observation = CleanupObservation(
            sessionId=terminal.session_id,
            mode=terminal.mode,
            ownerId=terminal.owner_id,
            ownerEpoch=terminal.owner_epoch,
            observedAt=clock.utc_now(),
            disposalRef=evidence.disposal_ref,
            settlementRef=evidence.settlement_ref,
            resources=terminal.resources,
            closure=None,
            disposed=True,
        )
        observations.append(observation)
        assert await verifier.verify(terminal, evidence, deadline) == "verified"
        observations.append(observation.model_copy(update={"disposal_ref": "foreign-disposal"}))
        assert await verifier.verify(terminal, evidence, deadline) == "rejected"
        observations.clear()
        assert await verifier.verify(terminal, evidence, deadline) == "unknown"
        assert len((await repository.read(deadline=deadline)).authority.slots) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("persisted_before_cancel", [False, True])
def test_live_close_confirms_receipt_settlement_after_reader_cancellation(
    live_case, monkeypatch, persisted_before_cancel
):
    from caller_runtime.services.cleanup import CleanupObservations, ResourceCleanupVerifier
    from tests.doubles import live_closed

    service, repository, clock, socket, opened = live_case

    async def scenario():
        original, segment = await opened()
        observations = CleanupObservations(owner_id=service.owner_id, utc_now=clock.utc_now)

        async def disposed():
            return segment.disposed

        observations.register(original, disposed=disposed)
        monkeypatch.setattr(segment.adapter, "_settlement_observer", observations)
        entered, release = asyncio.Event(), asyncio.Event()
        original_settle = service.settle_attempt
        calls = []

        async def blocked_settle(intent, **keywords):
            calls.append(keywords["settlement_ref"])
            result = await original_settle(intent, **keywords) if persisted_before_cancel else None
            entered.set()
            await release.wait()
            return result if persisted_before_cancel else await original_settle(intent, **keywords)

        monkeypatch.setattr(service, "settle_attempt", blocked_settle)
        await socket.events.put(live_closed(socket.start, "sess-case-fixture", 3))
        reader = asyncio.create_task(segment.next_event())
        await asyncio.wait_for(entered.wait(), 1)
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader
        await service.mark_terminal(original.session_id, original.owner_epoch)
        release.set()
        receipt = await segment.close()
        deadline = asyncio.get_running_loop().time() + 1
        terminal = (await repository.read(deadline=deadline)).authority.slots[0]
        assert terminal.resources["live"].attempted == terminal.resources["live"].settled == 1
        assert terminal.closure_receipt == receipt
        assert len(calls) == 2 and calls[0] == calls[1]
        assert socket.closed == 1 and not any(
            event["type"] == "session.close" for event in socket.sent
        )
        observed = await observations.observe(terminal, deadline)
        assert observed is not None
        verifier = ResourceCleanupVerifier(observe=observations.observe, utc_now=clock.utc_now)
        assert (
            await verifier.verify(terminal, verifier.evidence(terminal, observed), deadline)
            == "verified"
        )

    asyncio.run(scenario())


def test_live_close_retries_a_failed_closure_settlement_until_cleanup_verifies(
    live_case, monkeypatch
):
    from caller_runtime.services.admission import AdmissionError
    from caller_runtime.services.cleanup import CleanupObservations, ResourceCleanupVerifier

    service, repository, clock, socket, opened = live_case

    async def scenario():
        original, segment = await opened()
        observations = CleanupObservations(owner_id=service.owner_id, utc_now=clock.utc_now)

        async def disposed():
            return segment.disposed

        observations.register(original, disposed=disposed)
        monkeypatch.setattr(segment.adapter, "_settlement_observer", observations)
        settle = service.settle_attempt
        calls = []

        async def unavailable_once(intent, **keywords):
            calls.append(keywords["settlement_ref"])
            if len(calls) == 1:
                raise AdmissionError("authority_unavailable")
            return await settle(intent, **keywords)

        monkeypatch.setattr(service, "settle_attempt", unavailable_once)
        await service.mark_terminal(original.session_id, original.owner_epoch)
        receipt = await segment.close()
        deadline = asyncio.get_running_loop().time() + 1
        terminal = (await repository.read(deadline=deadline)).authority.slots[0]
        assert len(calls) == 2 and calls[0] == calls[1]
        assert terminal.closure_receipt == receipt
        assert terminal.resources["live"].attempted == terminal.resources["live"].settled == 1
        observed = await observations.observe(terminal, deadline)
        assert observed is not None
        verifier = ResourceCleanupVerifier(observe=observations.observe, utc_now=clock.utc_now)
        assert (
            await verifier.verify(terminal, verifier.evidence(terminal, observed), deadline)
            == "verified"
        )

    asyncio.run(scenario())


def test_cleanup_observation_source_uses_actual_bound_live_close_and_owned_disposal(
    live_case, monkeypatch
):
    from caller_runtime.services.cleanup import CleanupObservations, ResourceCleanupVerifier

    service, repository, clock, socket, opened = live_case

    async def scenario():
        original, segment = await opened()
        observations = CleanupObservations(owner_id=service.owner_id, utc_now=clock.utc_now)

        async def disposed():
            return segment.disposed

        observations.register(original, disposed=disposed)
        monkeypatch.setattr(segment.adapter, "_settlement_observer", observations)
        terminal = await service.mark_terminal(original.session_id, original.owner_epoch)
        deadline = asyncio.get_running_loop().time() + 1
        assert await observations.observe(terminal, deadline) is None
        receipt = await segment.close()
        terminal = (await repository.read(deadline=deadline)).authority.slots[0]
        observed = await observations.observe(terminal, deadline)
        assert observed is not None and observed.closure == receipt
        verifier = ResourceCleanupVerifier(observe=observations.observe, utc_now=clock.utc_now)
        evidence = verifier.evidence(terminal, observed)
        assert evidence.kind == "speech_session_closed"
        assert await verifier.verify(terminal, evidence, deadline) == "verified"
        lost = CleanupObservations(owner_id=service.owner_id, utc_now=clock.utc_now)
        assert (
            await ResourceCleanupVerifier(observe=lost.observe, utc_now=clock.utc_now).verify(
                terminal, evidence, deadline
            )
            == "unknown"
        )

    asyncio.run(scenario())


def test_released_old_id_is_idempotent_even_when_five_new_local_slots_exist(admission_case):
    service, repository, container, clock, permit, cache = admission_case

    class Verifier:
        async def verify(self, slot, evidence, deadline):
            return "verified"

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        original = await service.reserve(permit(1))
        terminal = await service.mark_terminal(original.session_id, original.owner_epoch)
        evidence = cleanup_for(terminal, clock.utc_now())
        assert await recovery_service(admission_case, Verifier()).release(
            original.session_id, original.owner_epoch, evidence
        )
        for index in range(2, 7):
            await service.reserve(permit(index, actor="new-manager-" + str(index)))
        assert await service.release(original.session_id, original.owner_epoch, evidence)
        assert (
            len(
                (
                    await repository.read(deadline=asyncio.get_running_loop().time() + 1)
                ).authority.slots
            )
            == 5
        )

    asyncio.run(scenario())


@pytest.fixture
def signed_test_permit(admission_case):
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    service, repository, container, clock, permit, cache = admission_case
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    def sign(changes=None, headers=None, algorithm="RS256"):
        document = permit(1).model_dump(mode="json", by_alias=True) | (changes or {})
        return jwt.encode(
            document,
            key if algorithm == "RS256" else "synthetic-hmac-key-not-used-by-verifier-32",
            algorithm=algorithm,
            headers={"kid": "manager-fixture-key"} | (headers or {}),
        )

    return public, sign, clock, permit(1)


def test_fixed_rs256_permit_verifies_only_explicit_owner_metadata_and_injected_clock(
    signed_test_permit,
):
    from caller_runtime.adapters.identity import TestPermitVerifier

    public, sign, clock, expected = signed_test_permit
    verifier = TestPermitVerifier(
        issuer=expected.issuer,
        audience=expected.audience,
        keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        max_clock_skew_seconds=0,
    )
    raw = sign()
    actual = asyncio.run(verifier.verify(raw))
    assert actual == expected and actual.actor_key == expected.actor_key
    clock.advance(10)
    with pytest.raises(PermissionError, match="invalid_test_permit"):
        asyncio.run(verifier.verify(raw))


@pytest.mark.parametrize(
    "fault",
    [
        "algorithm",
        "key-url",
        "unknown-key",
        "issuer",
        "audience",
        "missing-session",
        "future",
        "oversized",
        "signature",
    ],
)
def test_fixed_rs256_permit_rejects_untrusted_signature_binding_or_shape(signed_test_permit, fault):
    from caller_runtime.adapters.identity import TestPermitVerifier

    public, sign, clock, expected = signed_test_permit
    verifier = TestPermitVerifier(
        issuer=expected.issuer,
        audience=expected.audience,
        keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        max_clock_skew_seconds=0,
    )
    changes, headers, algorithm = {}, {}, "RS256"
    if fault == "algorithm":
        algorithm = "HS256"
    elif fault == "key-url":
        headers["jku"] = "https://untrusted.fixture.example/keys"
    elif fault == "unknown-key":
        headers["kid"] = "foreign-key"
    elif fault in {"issuer", "audience"}:
        changes["iss" if fault == "issuer" else "aud"] = "foreign-binding"
    elif fault == "missing-session":
        changes["managementSessionDigest"] = ""
    elif fault == "future":
        changes["iat"] = expected.issued_epoch + 5
        changes["issuedAt"] = (expected.issued_at + timedelta(seconds=5)).isoformat()
    raw = "x" * 6145 if fault == "oversized" else sign(changes, headers, algorithm)
    if fault == "signature":
        header, payload, signature = raw.split(".")
        raw = ".".join((header, payload, ("A" if signature[0] != "A" else "B") + signature[1:]))
    with pytest.raises(PermissionError, match="invalid_test_permit") as failure:
        asyncio.run(verifier.verify(raw))
    assert "foreign-binding" not in str(failure.value)


def test_admission_retains_exact_pin_when_publication_refreshes_during_reservation(
    admission_case, runtime_revision_v2, monkeypatch
):
    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        expected = cache.pin()
        replace = repository.replace
        refreshed = []

        async def change_publication(current, candidate, *, deadline):
            result = await replace(current, candidate, deadline=deadline)
            if not refreshed:
                cache.install(runtime_revision_v2(2), etag='"published-second"')
                refreshed.append(True)
            return result

        monkeypatch.setattr(repository, "replace", change_publication)
        slot = await service.reserve(permit(1))
        assert cache.pin().revision_id != slot.revision_id
        pinned = service.snapshot_for(slot)
        assert pinned is expected and pinned.revision_id == slot.revision_id
        assert pinned.graph_hash == slot.graph_hash and pinned.policy_hash == slot.policy_hash
        with pytest.raises(AdmissionError, match="stale_owner"):
            recovery_service(admission_case, None).snapshot_for(slot)

    asyncio.run(scenario())


def cleanup_for(slot, now, kind="no_external_attempt"):
    from caller_runtime.domain.conversation import CleanupEvidence

    return CleanupEvidence.model_validate_json(
        json.dumps(
            {
                "kind": kind,
                "sessionId": slot.session_id,
                "mode": slot.mode,
                "ownerId": slot.owner_id,
                "ownerEpoch": slot.owner_epoch,
                "attemptWatermark": slot.attempted_through,
                "observedAt": now.isoformat(),
                "disposalRef": "disposal-fixture",
                "settlementRef": "settled-fixture" if kind != "no_external_attempt" else None,
            }
        )
    )


def recovery_service(admission_case, verifier):
    from caller_runtime.services.admission import TestAdmissionService

    _service, repository, _container, clock, _permit, cache = admission_case
    return TestAdmissionService(
        repository=repository,
        configuration=cache,
        owner_id="independent-cleanup-owner",
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        max_clock_skew_seconds=0,
        cleanup_verifier=verifier,
    )


def test_zero_attempt_terminal_start_releases_only_after_independent_disposal_verification(
    admission_case,
):
    class Verifier:
        calls = []

        async def verify(self, slot, evidence, deadline):
            self.calls.append((slot.session_id, evidence.kind, deadline))
            return "verified"

    service, repository, container, clock, permit, cache = admission_case
    verifier = Verifier()

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        terminal = await service.mark_terminal(slot.session_id, slot.owner_epoch)
        recovery = recovery_service(admission_case, verifier)
        assert await recovery.release(
            slot.session_id, slot.owner_epoch, cleanup_for(terminal, clock.utc_now())
        )
        assert (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots == []
        assert len(verifier.calls) == 1
        assert (await service.reserve(permit(2))).session_id != slot.session_id

    asyncio.run(scenario())


def test_unknown_cleanup_or_cancelled_work_retains_capacity_without_live_allocation(admission_case):
    class Unknown:
        async def verify(self, slot, evidence, deadline):
            return "unknown"

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        terminal = await service.mark_terminal(slot.session_id, slot.owner_epoch)
        recovery = recovery_service(admission_case, Unknown())
        assert not await recovery.release(
            slot.session_id, slot.owner_epoch, cleanup_for(terminal, clock.utc_now())
        )
        retained = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots[0]
        assert (
            retained.terminal_fence
            and retained.cleanup_evidence is None
            and not retained.live_ever_attempted
        )

    asyncio.run(scenario())


def test_text_work_requires_definitive_settlement_and_no_live_receipt_before_restart(
    admission_case,
):
    class Verified:
        async def verify(self, slot, evidence, deadline):
            return "verified"

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        attempt = await service.begin_attempt(
            slot.session_id, slot.owner_epoch, kind="model", binding_hash="d" * 64, generation=1
        )
        terminal = await service.mark_terminal(slot.session_id, slot.owner_epoch)
        recovery = recovery_service(admission_case, Verified())
        evidence = cleanup_for(terminal, clock.utc_now(), "work_disposed")
        assert not await recovery.release(slot.session_id, slot.owner_epoch, evidence)
        settled = await service.settle_attempt(attempt, settlement_ref="settled-fixture")
        assert settled.settled_through == settled.attempted_through == 1
        assert not settled.live_ever_attempted and settled.closure_receipt is None
        assert await recovery.release(
            slot.session_id,
            slot.owner_epoch,
            cleanup_for(settled, clock.utc_now(), "work_disposed"),
        )
        assert await recovery.release(
            slot.session_id,
            slot.owner_epoch,
            cleanup_for(settled, clock.utc_now(), "work_disposed"),
        )
        assert (
            len(
                (
                    await repository.read(deadline=asyncio.get_running_loop().time() + 1)
                ).authority.slots
            )
            == 0
        )
        assert (await service.reserve(permit(2))).session_id != slot.session_id

    asyncio.run(scenario())


def test_cleanup_after_expired_login_and_lifetime_uses_separate_budget(admission_case):
    class Verified:
        async def verify(self, slot, evidence, deadline):
            assert 0 < deadline - asyncio.get_running_loop().time() <= 10
            return "verified"

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        clock.advance(1000)
        terminal = await service.mark_terminal(slot.session_id, slot.owner_epoch)
        recovery = recovery_service(admission_case, Verified())
        assert await recovery.release(
            slot.session_id, slot.owner_epoch, cleanup_for(terminal, clock.utc_now())
        )

    asyncio.run(scenario())


def test_restart_recovery_can_only_fence_cleanup_and_never_adopt_business_session(admission_case):
    class Verified:
        async def verify(self, slot, evidence, deadline):
            return "verified"

    from caller_runtime.services.admission import AdmissionError

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        slot = await service.reserve(permit(1))
        recovery = recovery_service(admission_case, Verified())
        with pytest.raises(AdmissionError, match="stale_owner"):
            await recovery.begin_attempt(
                slot.session_id, slot.owner_epoch, kind="model", binding_hash="d" * 64, generation=1
            )
        terminal = await recovery.fence_for_cleanup(slot.session_id, slot.owner_epoch)
        assert terminal.owner_id == slot.owner_id and terminal.terminal_fence
        assert await recovery.release(
            slot.session_id, slot.owner_epoch, cleanup_for(terminal, clock.utc_now())
        )

    asyncio.run(scenario())


def test_local_tracking_is_pruned_only_after_verified_authority_removal(admission_case):
    class Verified:
        async def verify(self, slot, evidence, deadline):
            return "verified"

    service, repository, container, clock, permit, cache = admission_case

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        for number in range(1, 13):
            slot = await service.reserve(permit(number))
            terminal = await service.mark_terminal(slot.session_id, slot.owner_epoch)
            recovery = recovery_service(admission_case, Verified())
            assert await recovery.release(
                slot.session_id, slot.owner_epoch, cleanup_for(terminal, clock.utc_now())
            )
        await service.reserve(permit(13))
        assert len(service._locks) == len(service._terminal) == len(service._startup) == 1

    asyncio.run(scenario())


def test_authority_read_missing_is_strong_and_never_implicitly_initializes(authority_store):
    from caller_runtime.adapters.admission_repository import AuthorityUnavailable

    repository, container = authority_store
    assert container.calls == []

    async def scenario():
        with pytest.raises(AuthorityUnavailable, match="test_authority_unavailable"):
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)

    asyncio.run(scenario())
    assert len(container.calls) == 1 and container.calls[0][0] == "read"
    assert container.calls[0][1] == "browser-test-authority-v1"
    assert container.calls[0][2]["partition_key"] == "browser-test-authority-v1"
    assert container.calls[0][2]["initial_headers"] == {"x-ms-consistency-level": "Strong"}
    assert container.records == {}


def test_authority_explicit_initialization_is_idempotent_and_detached(
    authority_store, conversation_clock
):
    from caller_runtime.adapters.admission_repository import TestAdmissionRepository

    repository, container = authority_store

    async def scenario():
        deadline = asyncio.get_running_loop().time() + 1
        first = await repository.initialize(now=conversation_clock.utc_now(), deadline=deadline)
        first.authority.consumed_nonces["f" * 64] = conversation_clock.utc_now() + timedelta(
            seconds=30
        )
        second = await repository.initialize(now=conversation_clock.utc_now(), deadline=deadline)
        assert first.etag == second.etag and second.authority.slots == []
        assert second.authority.consumed_nonces == {}
        assert container.records["browser-test-authority-v1"]["ttl"] == -1
        assert len(container.records) == 1
        reconstructed = TestAdmissionRepository(container=container)
        assert (await reconstructed.read(deadline=deadline)).authority == second.authority
        for operation, _, keywords in container.calls:
            if operation == "create":
                assert keywords["retry_write"] == 0
                assert keywords["enable_automatic_id_generation"] is False

    asyncio.run(scenario())


def test_authority_conditional_replace_never_overwrites_another_instance(
    authority_store, conversation_clock
):
    from caller_runtime.adapters.admission_repository import (
        AuthorityConflict,
        TestAdmissionRepository,
    )
    from caller_runtime.domain.conversation import TestAuthority

    repository, container = authority_store

    async def scenario():
        deadline = asyncio.get_running_loop().time() + 1
        current = await repository.initialize(now=conversation_clock.utc_now(), deadline=deadline)
        other = TestAdmissionRepository(container=container)
        stale = await other.read(deadline=deadline)
        first = current.authority.model_dump(mode="json", by_alias=True)
        first["consumedNonces"]["a" * 64] = (
            conversation_clock.utc_now() + timedelta(seconds=30)
        ).isoformat()
        saved = await repository.replace(
            current, TestAuthority.model_validate_json(json.dumps(first)), deadline=deadline
        )
        second = stale.authority.model_dump(mode="json", by_alias=True)
        second["consumedNonces"]["b" * 64] = first["consumedNonces"]["a" * 64]
        with pytest.raises(AuthorityConflict, match="test_authority_conflict"):
            await other.replace(
                stale, TestAuthority.model_validate_json(json.dumps(second)), deadline=deadline
            )
        assert (await repository.read(deadline=deadline)).authority == saved.authority
        writes = [call for call in container.calls if call[0] == "replace"]
        assert all(call[2]["retry_write"] == 0 for call in writes)

    asyncio.run(scenario())


def test_corrupt_authority_never_resets_or_reflects_store_content(
    authority_store, conversation_clock
):
    from caller_runtime.adapters.admission_repository import AuthorityUnavailable

    repository, container = authority_store

    async def scenario():
        deadline = asyncio.get_running_loop().time() + 1
        await repository.initialize(now=conversation_clock.utc_now(), deadline=deadline)
        container.records["browser-test-authority-v1"]["transcript"] = (
            "synthetic-private-corrupt-store"
        )
        before = copy.deepcopy(container.records)
        with pytest.raises(AuthorityUnavailable) as failure:
            await repository.read(deadline=deadline)
        assert "synthetic-private" not in str(failure.value)
        with pytest.raises(AuthorityUnavailable):
            await repository.initialize(now=conversation_clock.utc_now(), deadline=deadline)
        assert container.records == before

    asyncio.run(scenario())


def test_occupied_slot_cannot_be_removed_without_terminal_cleanup_evidence(
    authority_store, slot_document, conversation_clock
):
    from caller_runtime.adapters.admission_repository import AuthorityConflict
    from caller_runtime.domain.conversation import TestAuthority

    repository, container = authority_store

    async def scenario():
        deadline = asyncio.get_running_loop().time() + 1
        current = await repository.initialize(now=conversation_clock.utc_now(), deadline=deadline)
        document = current.authority.model_dump(mode="json", by_alias=True)
        document["slots"] = [slot_document]
        occupied = await repository.replace(
            current, TestAuthority.model_validate_json(json.dumps(document)), deadline=deadline
        )
        document["slots"] = []
        with pytest.raises(AuthorityConflict):
            await repository.replace(
                occupied, TestAuthority.model_validate_json(json.dumps(document)), deadline=deadline
            )
        assert len((await repository.read(deadline=deadline)).authority.slots) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["lost", "cancelled"])
def test_authority_lost_or_cancelled_replace_is_reread_without_replaying_write(
    authority_store, conversation_clock, monkeypatch, outcome
):
    from caller_runtime.adapters.admission_repository import (
        AuthorityUnavailable,
        TestAdmissionRepository,
    )
    from caller_runtime.domain.conversation import TestAuthority

    repository, container = authority_store

    async def scenario():
        deadline = asyncio.get_running_loop().time() + 1
        current = await repository.initialize(now=conversation_clock.utc_now(), deadline=deadline)
        document = current.authority.model_dump(mode="json", by_alias=True)
        document["consumedNonces"]["a" * 64] = (
            conversation_clock.utc_now() + timedelta(seconds=30)
        ).isoformat()
        mutate = container.replace_item

        async def ambiguous(**keywords):
            await mutate(**keywords)
            if outcome == "cancelled":
                raise asyncio.CancelledError
            raise RuntimeError("synthetic-private-lost-write-details")

        monkeypatch.setattr(container, "replace_item", ambiguous)
        with pytest.raises(
            asyncio.CancelledError if outcome == "cancelled" else AuthorityUnavailable
        ):
            await repository.replace(
                current, TestAuthority.model_validate_json(json.dumps(document)), deadline=deadline
            )
        reconstructed = TestAdmissionRepository(container=container)
        observed = await reconstructed.read(deadline=deadline)
        assert "a" * 64 in observed.authority.consumed_nonces
        assert len([call for call in container.calls if call[0] == "replace"]) == 1

    asyncio.run(scenario())


def test_authority_unexpired_nonce_history_cannot_be_removed(authority_store, conversation_clock):
    from caller_runtime.adapters.admission_repository import AuthorityConflict
    from caller_runtime.domain.conversation import TestAuthority

    repository, container = authority_store

    async def scenario():
        deadline = asyncio.get_running_loop().time() + 1
        current = await repository.initialize(now=conversation_clock.utc_now(), deadline=deadline)
        document = current.authority.model_dump(mode="json", by_alias=True)
        document["consumedNonces"]["a" * 64] = (
            conversation_clock.utc_now() + timedelta(seconds=30)
        ).isoformat()
        current = await repository.replace(
            current, TestAuthority.model_validate_json(json.dumps(document)), deadline=deadline
        )
        document["consumedNonces"] = {}
        with pytest.raises(AuthorityConflict):
            await repository.replace(
                current, TestAuthority.model_validate_json(json.dumps(document)), deadline=deadline
            )

    asyncio.run(scenario())


def test_terminal_fence_rejects_new_external_attempt_history(
    authority_store, slot_document, conversation_clock
):
    from caller_runtime.adapters.admission_repository import AuthorityConflict
    from caller_runtime.domain.conversation import TestAuthority

    repository, container = authority_store

    async def scenario():
        deadline = asyncio.get_running_loop().time() + 1
        current = await repository.initialize(now=conversation_clock.utc_now(), deadline=deadline)
        slot = slot_document | {"phase": "ending", "terminalFence": True}
        document = current.authority.model_dump(mode="json", by_alias=True)
        document["slots"] = [slot]
        current = await repository.replace(
            current, TestAuthority.model_validate_json(json.dumps(document)), deadline=deadline
        )
        slot.update(
            attemptedThrough=1,
            resources={
                "model": {
                    "attempted": 1,
                    "settled": 0,
                    "attempt": {
                        "number": 1,
                        "kind": "model",
                        "sessionId": slot["sessionId"],
                        "attemptId": "attempt-late",
                        "ownerId": slot["ownerId"],
                        "ownerEpoch": 1,
                        "generation": 1,
                        "bindingHash": "d" * 64,
                        "provenanceRef": "source-fixture",
                        "recordedAt": conversation_clock.utc_now().isoformat(),
                    },
                },
                "capability": {},
                "live": {},
            },
        )
        with pytest.raises(AuthorityConflict):
            await repository.replace(
                current, TestAuthority.model_validate_json(json.dumps(document)), deadline=deadline
            )
        assert (await repository.read(deadline=deadline)).authority.slots[0].attempted_through == 0

    asyncio.run(scenario())


@pytest.fixture
def slot_document(conversation_clock):
    now = conversation_clock.utc_now()
    return {
        "sessionId": "test-fixture",
        "actorKey": "a" * 64,
        "requestId": "request-fixture",
        "mode": "text",
        "ownerId": "owner-fixture",
        "ownerEpoch": 1,
        "revisionId": "revision-fixture",
        "graphHash": "b" * 64,
        "policyHash": "c" * 64,
        "startedAt": now.isoformat(),
        "admissionDeadlineUtc": (now + timedelta(seconds=10)).isoformat(),
        "authExpiresAt": (now + timedelta(seconds=900)).isoformat(),
        "expiresAt": (now + timedelta(seconds=600)).isoformat(),
        "idleExpiresAt": (now + timedelta(seconds=120)).isoformat(),
        "activeAgentId": "welcome",
        "activeCommunityId": None,
    }


@pytest.mark.parametrize("corruption", ["future-generation", "foreign-session", "foreign-epoch"])
def test_persisted_attempt_is_bound_to_slot_owner_and_nonfuture_generation(
    slot_document, conversation_clock, corruption
):
    from pydantic import ValidationError

    from caller_runtime.domain.conversation import TestSlot

    attempt = {
        "number": 1,
        "kind": "model",
        "sessionId": slot_document["sessionId"],
        "attemptId": "attempt-fixture",
        "ownerId": slot_document["ownerId"],
        "ownerEpoch": 1,
        "generation": 1,
        "bindingHash": "d" * 64,
        "provenanceRef": "source-fixture",
        "recordedAt": conversation_clock.utc_now().isoformat(),
    }
    slot_document.update(
        attemptedThrough=1,
        resources={
            "model": {"attempted": 1, "settled": 0, "attempt": attempt},
            "capability": {},
            "live": {},
        },
    )
    assert TestSlot.model_validate_json(json.dumps(slot_document)).attempted_through == 1
    if corruption == "future-generation":
        attempt["generation"] = 2
    elif corruption == "foreign-session":
        attempt["sessionId"] = "foreign-session"
    else:
        attempt["ownerEpoch"] = 2
    with pytest.raises(ValidationError):
        TestSlot.model_validate_json(json.dumps(slot_document))


def test_slot_is_strict_metadata_and_initial_attempt_history_is_zero(slot_document):
    from caller_runtime.domain.conversation import TestSlot

    slot = TestSlot.model_validate_json(json.dumps(slot_document))
    assert slot.mode == "text" and slot.owner_epoch == 1
    assert slot.attempted_through == slot.settled_through == 0
    assert slot.live_ever_attempted is False and not slot.terminal_fence
    assert slot.expires_at - slot.started_at == timedelta(seconds=600)
    assert slot.idle_expires_at - slot.started_at == timedelta(seconds=120)
    assert set(slot.resources) == {"model", "capability", "live"}


def test_permit_metadata_preserves_original_startup_deadline_and_session_binding(
    conversation_clock,
):
    from caller_runtime.domain.conversation import StartPermit

    now = conversation_clock.utc_now()
    payload = {
        "iss": "https://management.example.invalid/test-permits",
        "aud": "runtime-fixture",
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + 30,
        "issuedAt": now.isoformat(),
        "admissionStartedAt": now.isoformat(),
        "admissionDeadlineUtc": (now + timedelta(seconds=10)).isoformat(),
        "tenantId": "tenant-fixture",
        "actorId": "manager-fixture",
        "mode": "text",
        "managementSessionDigest": "d" * 64,
        "requestId": "request-fixture",
        "nonce": "e" * 64,
        "authExpiresAt": (now + timedelta(seconds=900)).isoformat(),
    }
    permit = StartPermit.model_validate_json(json.dumps(payload))
    assert permit.management_session_digest == "d" * 64
    assert permit.admission_deadline_utc - permit.admission_started_at == timedelta(seconds=10)
    assert len(permit.actor_key) == 64
    other_cookie_mode = StartPermit.model_validate_json(
        json.dumps(payload | {"managementSessionDigest": "f" * 64, "mode": "speech"})
    )
    assert other_cookie_mode.actor_key == permit.actor_key
    other_actor = StartPermit.model_validate_json(
        json.dumps(payload | {"actorId": "other-manager"})
    )
    assert other_actor.actor_key != permit.actor_key
    payload["exp"] += 1
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StartPermit.model_validate_json(json.dumps(payload))
    payload["exp"] -= 1
    payload["admissionDeadlineUtc"] = (now + timedelta(seconds=11)).isoformat()
    with pytest.raises(ValidationError):
        StartPermit.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "field", ["audio", "transcript", "prompt", "toolArguments", "providerBody", "permit"]
)
def test_slot_rejects_all_content_bearing_fields_without_reflection(slot_document, field):
    from pydantic import ValidationError

    from caller_runtime.domain.conversation import TestSlot

    slot_document[field] = "synthetic-private-admission-content"
    with pytest.raises(ValidationError) as failure:
        TestSlot.model_validate_json(json.dumps(slot_document))
    assert "synthetic-private-admission-content" not in str(failure.value)


@pytest.mark.parametrize("corruption", ["lifetime", "idle", "owner-epoch", "future-ready"])
def test_slot_deadline_and_owner_invariants_reject(slot_document, corruption):
    from pydantic import ValidationError

    from caller_runtime.domain.conversation import TestSlot

    if corruption == "lifetime":
        slot_document["expiresAt"] = slot_document["authExpiresAt"]
    elif corruption == "idle":
        slot_document["idleExpiresAt"] = slot_document["authExpiresAt"]
    elif corruption == "owner-epoch":
        slot_document["ownerEpoch"] = 0
    else:
        slot_document.update(phase="active", readyAt=slot_document["authExpiresAt"])
    with pytest.raises(ValidationError):
        TestSlot.model_validate_json(json.dumps(slot_document))


@pytest.mark.parametrize("kind", ["no_external_attempt", "work_disposed"])
def test_text_cleanup_evidence_never_requires_or_fabricates_live_receipt(kind, conversation_clock):
    from caller_runtime.domain.conversation import CleanupEvidence

    evidence = CleanupEvidence.model_validate_json(
        json.dumps(
            {
                "kind": kind,
                "sessionId": "test-fixture",
                "mode": "text",
                "ownerId": "owner-fixture",
                "ownerEpoch": 1,
                "attemptWatermark": 0,
                "observedAt": conversation_clock.utc_now().isoformat(),
                "disposalRef": "disposal-fixture",
                "settlementRef": "settled-fixture" if kind == "work_disposed" else None,
                "closure": None,
            }
        )
    )
    assert evidence.closure is None and evidence.mode == "text"


def test_speech_evidence_cannot_be_substituted_for_text_or_zero_attempt(conversation_clock):
    from pydantic import ValidationError

    from caller_runtime.domain.conversation import CleanupEvidence

    with pytest.raises(ValidationError):
        CleanupEvidence.model_validate_json(
            json.dumps(
                {
                    "kind": "speech_session_closed",
                    "sessionId": "test-fixture",
                    "mode": "text",
                    "ownerId": "owner-fixture",
                    "ownerEpoch": 1,
                    "attemptWatermark": 0,
                    "observedAt": conversation_clock.utc_now().isoformat(),
                    "disposalRef": "disposal-fixture",
                    "settlementRef": "settled-fixture",
                    "closure": None,
                }
            )
        )


@pytest.mark.parametrize(
    "corruption", ["active-evidence", "foreign-owner", "wrong-mode", "future-watermark"]
)
def test_persisted_cleanup_evidence_must_match_terminal_slot_history(
    slot_document, conversation_clock, corruption
):
    from pydantic import ValidationError

    from caller_runtime.domain.conversation import TestSlot

    slot_document.update(phase="ending", terminalFence=True)
    evidence = {
        "kind": "no_external_attempt",
        "sessionId": slot_document["sessionId"],
        "mode": "text",
        "ownerId": slot_document["ownerId"],
        "ownerEpoch": 1,
        "attemptWatermark": 0,
        "observedAt": conversation_clock.utc_now().isoformat(),
        "disposalRef": "disposal-fixture",
    }
    slot_document["cleanupEvidence"] = evidence
    assert TestSlot.model_validate_json(json.dumps(slot_document)).cleanup_evidence is not None
    if corruption == "active-evidence":
        slot_document.update(phase="starting", terminalFence=False)
    elif corruption == "foreign-owner":
        evidence["ownerId"] = "foreign-owner"
    elif corruption == "wrong-mode":
        evidence["mode"] = "speech"
    else:
        evidence.update(kind="work_disposed", settlementRef="settled-fixture", attemptWatermark=1)
    with pytest.raises(ValidationError):
        TestSlot.model_validate_json(json.dumps(slot_document))


def test_authority_requires_fixed_private_partition_and_bounded_slots(
    slot_document, conversation_clock
):
    from pydantic import ValidationError

    from caller_runtime.domain.conversation import TestAuthority

    document = {
        "id": "browser-test-authority-v1",
        "sessionId": "browser-test-authority-v1",
        "schemaVersion": "1.0.0",
        "utcHighWater": conversation_clock.utc_now().isoformat(),
        "slots": [],
        "consumedNonces": {},
    }
    authority = TestAuthority.model_validate_json(json.dumps(document))
    assert authority.id == authority.session_id == "browser-test-authority-v1"
    document["slots"] = [
        dict(slot_document, sessionId=f"test-{index}", actorKey=f"{index:064x}")
        for index in range(6)
    ]
    with pytest.raises(ValidationError):
        TestAuthority.model_validate_json(json.dumps(document))
    document["slots"] = []
    document["consumedNonces"] = {
        f"{index:064x}": slot_document["authExpiresAt"] for index in range(101)
    }
    with pytest.raises(ValidationError):
        TestAuthority.model_validate_json(json.dumps(document))
