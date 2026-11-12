import asyncio
from datetime import timedelta

from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store


def test_recovery_releases_only_expired_fenced_slots_held_by_an_unsettled_model_request(
    admission_case,
):
    from caller_runtime.domain.conversation import WorkBudget
    from caller_runtime.recovery import release_stuck_model_slots

    service, repository, container, clock, permit, cache = admission_case

    async def held(number, kind, *, fence):
        slot = await service.reserve(permit(number, actor=f"manager-{number}"))
        slot = await service.mark_ready(slot.session_id, slot.owner_epoch)
        now = clock.utc_now()
        await service.begin_attempt(
            slot.session_id,
            slot.owner_epoch,
            kind=kind,
            binding_hash="a" * 64,
            generation=slot.generation,
            work=WorkBudget(now, now + timedelta(seconds=10)),
        )
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
        # Not yet expired: nothing qualifies, even with apply.
        assert await release_stuck_model_slots(repository, now=clock.utc_now(), apply=True) == []
        clock.advance(600 + 300)
        before = await read()
        assert await release_stuck_model_slots(repository, now=clock.utc_now(), apply=False) == [
            stuck
        ]
        assert (await read()).etag == before.etag
        assert await release_stuck_model_slots(repository, now=clock.utc_now(), apply=True) == [
            stuck
        ]
        remaining = (await read()).authority
        assert {slot.session_id for slot in remaining.slots} == {live, capability}
        assert await release_stuck_model_slots(repository, now=clock.utc_now(), apply=True) == []
        # The released manager no longer holds a slot, so a new start is not `manager_busy`.
        assert permit(4, actor="manager-1").actor_key not in {
            slot.actor_key for slot in (await read()).authority.slots
        }

    asyncio.run(scenario())
