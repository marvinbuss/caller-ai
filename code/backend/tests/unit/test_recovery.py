import asyncio
from datetime import timedelta

from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store


def test_recovery_releases_only_expired_fenced_slots_held_by_an_unsettled_model_or_live_attempt(
    admission_case,
):
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.recovery import release_stuck_slots

    service, repository, container, clock, permit, cache = admission_case

    async def held(number, kind, *, fence, bound=True):
        mode = "speech" if kind == "live" else "text"
        slot = await service.reserve(permit(number, actor=f"manager-{number}", mode=mode))
        slot = await service.mark_ready(slot.session_id, slot.owner_epoch)
        now = clock.utc_now()
        work = WorkBudget(now, now + timedelta(seconds=10))
        intent = await service.begin_attempt(
            slot.session_id,
            slot.owner_epoch,
            kind=kind,
            binding_hash="a" * 64,
            generation=slot.generation,
            work=work,
        )
        if kind == "live" and bound:

            async def opened():
                return None

            await service.perform(intent, opened, work=work)
            await service.bind_live_session(intent, provider_session_id=f"provider-{number}")
        if fence:
            await service.mark_terminal(slot.session_id, slot.owner_epoch)
        return slot.session_id

    async def read():
        return await repository.read(deadline=asyncio.get_running_loop().time() + 1)

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        stuck = await held(1, "model", fence=True)
        live = await held(2, "model", fence=False)
        capability = await held(3, "capability", fence=True)
        # A voice session that failed mid agent change: its Live closure was never confirmed.
        speech = await held(4, "live", fence=True)
        # A Live start that failed before GPT-Live confirmed a session cannot carry a receipt.
        unbound = await held(5, "live", fence=True, bound=False)
        # Not yet expired: nothing qualifies, even with apply.
        assert await release_stuck_slots(repository, now=clock.utc_now(), apply=True) == []
        clock.advance(600 + 300)
        before = await read()
        assert await release_stuck_slots(repository, now=clock.utc_now(), apply=False) == [
            stuck,
            speech,
            unbound,
        ]
        assert (await read()).etag == before.etag
        assert await release_stuck_slots(repository, now=clock.utc_now(), apply=True) == [
            stuck,
            speech,
            unbound,
        ]
        remaining = (await read()).authority
        assert {slot.session_id for slot in remaining.slots} == {live, capability}
        assert await release_stuck_slots(repository, now=clock.utc_now(), apply=True) == []
        # The released managers no longer hold a slot, so a new start is not `manager_busy`.
        held_by = {slot.actor_key for slot in (await read()).authority.slots}
        for actor in ("manager-1", "manager-4", "manager-5"):
            assert permit(6, actor=actor).actor_key not in held_by

    asyncio.run(scenario())


def test_the_failed_start_filter_releases_only_slots_left_by_a_failed_voice_start(
    admission_case,
):
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.recovery import release_stuck_slots

    service, repository, container, clock, permit, cache = admission_case

    async def held(number, kind, *, bound=True):
        mode = "speech" if kind == "live" else "text"
        slot = await service.reserve(permit(number, actor=f"manager-{number}", mode=mode))
        now = clock.utc_now()
        work = WorkBudget(now, now + timedelta(seconds=10))
        intent = await service.begin_attempt(
            slot.session_id,
            slot.owner_epoch,
            kind=kind,
            binding_hash="a" * 64,
            generation=slot.generation,
            work=work,
        )
        if kind == "live" and bound:

            async def opened():
                return None

            await service.perform(intent, opened, work=work)
            await service.bind_live_session(intent, provider_session_id=f"provider-{number}")
        await service.mark_terminal(slot.session_id, slot.owner_epoch)
        return slot.session_id

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        model = await held(1, "model")
        speech = await held(2, "live")
        failed = await held(3, "live", bound=False)
        clock.advance(600 + 300)
        assert await release_stuck_slots(
            repository, now=clock.utc_now(), apply=True, failed_starts_only=True
        ) == [failed]
        remaining = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority
        assert {slot.session_id for slot in remaining.slots} == {model, speech}

    asyncio.run(scenario())


def test_the_store_drops_a_slot_without_evidence_only_for_recovery_of_an_unbound_live_attempt(
    admission_case,
):
    import pytest

    from caller_runtime.adapters.admission_repository import AuthorityConflict
    from caller_runtime.domain.configuration import encoded
    from caller_runtime.domain.conversation import TestAuthority, WorkBudget

    service, repository, container, clock, permit, cache = admission_case

    async def started(number, *, bound):
        slot = await service.reserve(permit(number, actor=f"manager-{number}", mode="speech"))
        now = clock.utc_now()
        work = WorkBudget(now, now + timedelta(seconds=10))
        intent = await service.begin_attempt(
            slot.session_id,
            slot.owner_epoch,
            kind="live",
            binding_hash="a" * 64,
            generation=slot.generation,
            work=work,
        )
        if bound:

            async def opened():
                return None

            await service.perform(intent, opened, work=work)
            await service.bind_live_session(intent, provider_session_id=f"provider-{number}")
        await service.mark_terminal(slot.session_id, slot.owner_epoch)
        return slot.session_id

    async def scenario():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )
        unbound = await started(1, bound=False)
        bound = await started(2, bound=True)
        current = await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        for target, released in (
            (unbound, frozenset()),
            (bound, frozenset({bound})),
        ):
            document = current.authority.model_dump(mode="json", by_alias=True)
            document["slots"] = [s for s in document["slots"] if s["sessionId"] != target]
            with pytest.raises(AuthorityConflict):
                await repository.replace(
                    current,
                    TestAuthority.model_validate_json(encoded(document)),
                    deadline=asyncio.get_running_loop().time() + 1,
                    released=released,
                )
        document = current.authority.model_dump(mode="json", by_alias=True)
        document["slots"] = [s for s in document["slots"] if s["sessionId"] != unbound]
        await repository.replace(
            current,
            TestAuthority.model_validate_json(encoded(document)),
            deadline=asyncio.get_running_loop().time() + 1,
            released=frozenset({unbound}),
        )
        remaining = (
            await repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority
        assert [slot.session_id for slot in remaining.slots] == [bound]

    asyncio.run(scenario())
