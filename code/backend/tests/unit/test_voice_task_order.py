"""The voice endpoint reports the client's own refusal ahead of background task failures."""

import asyncio
import itertools

import pytest


def test_reader_refusal_outranks_background_failures_whatever_the_completion_order():
    from caller_runtime.api.voice import finished_in_priority
    from caller_runtime.services.admission import AdmissionError

    async def fail(error):
        raise error

    async def scenario():
        reader = asyncio.create_task(fail(PermissionError("session_expired")))
        watcher = asyncio.create_task(fail(AdmissionError("authority_unavailable")))
        background = asyncio.create_task(fail(RuntimeError("provider_unavailable")))
        completed = [reader, watcher, background]
        await asyncio.wait(completed)
        for order in itertools.permutations(completed):
            ranked = finished_in_priority(order, reader=reader, watcher=watcher)
            assert ranked[:2] == [reader, watcher]
            with pytest.raises(PermissionError, match="session_expired"):
                ranked[0].result()
        for task in (watcher, background):
            assert task.exception() is not None

    asyncio.run(scenario())


def test_finished_tasks_keep_only_the_ones_that_completed():
    from caller_runtime.api.voice import finished_in_priority

    async def scenario():
        async def quiet():
            return None

        reader = asyncio.create_task(quiet())
        watcher = asyncio.create_task(quiet())
        await asyncio.wait([watcher])
        assert finished_in_priority({watcher}, reader=reader, watcher=watcher) == [watcher]
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)

    asyncio.run(scenario())
