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
        # A Live attempt that never reached the provider cannot carry a receipt; left for review.
        unbound = await held(5, "live", fence=True, bound=False)
        # Not yet expired: nothing qualifies, even with apply.
        assert await release_stuck_slots(repository, now=clock.utc_now(), apply=True) == []
        clock.advance(600 + 300)
        before = await read()
        assert await release_stuck_slots(repository, now=clock.utc_now(), apply=False) == [
            stuck,
            speech,
        ]
        assert (await read()).etag == before.etag
        assert await release_stuck_slots(repository, now=clock.utc_now(), apply=True) == [
            stuck,
            speech,
        ]
        remaining = (await read()).authority
        assert {slot.session_id for slot in remaining.slots} == {live, capability, unbound}
        assert await release_stuck_slots(repository, now=clock.utc_now(), apply=True) == []
        # The released managers no longer hold a slot, so a new start is not `manager_busy`.
        held_by = {slot.actor_key for slot in (await read()).authority.slots}
        for actor in ("manager-1", "manager-4"):
            assert permit(6, actor=actor).actor_key not in held_by

    asyncio.run(scenario())
