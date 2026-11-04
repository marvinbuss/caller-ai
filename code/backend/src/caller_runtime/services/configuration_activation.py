"""Pins one configuration revision per test session and owns the optional refresh loop.

`select_new_call` returns the current pinned snapshot for a new session, or raises
`configuration_unavailable` when no community is enabled. A running session keeps its snapshot, so a
later publication changes only new sessions. The refresh client exists only when the composition
supplies one; management stays the authority for current permissions.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from caller_runtime.adapters.configuration_client import ConfigurationClient
from caller_runtime.domain.configuration import RevisionCache, RevisionSnapshot


class ConfigurationActivation:
    def __init__(self, *, cache: RevisionCache, client: ConfigurationClient | None = None) -> None:
        if client is not None and client.cache is not cache:
            raise ValueError("configuration_cache_binding_mismatch")
        self._cache, self._client = cache, client

    def select_new_call(self) -> RevisionSnapshot:
        snapshot = self._cache.pin()
        if not any(community["enabled"] for community in snapshot.for_runtime()["communities"]):
            raise PermissionError("configuration_unavailable")
        return snapshot

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """Explicit lifespan only; default app composition constructs no configuration client."""
        if self._client is None:
            yield
            return
        stop = asyncio.Event()
        await self._client.refresh()
        work = asyncio.create_task(self._client.run(stop))
        try:
            yield
        finally:
            stop.set()
            work.cancel()
            try:
                await work
            except asyncio.CancelledError:
                pass
