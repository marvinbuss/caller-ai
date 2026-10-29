"""Pin ordinary revisions at local admission; current gateway authority remains independent."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from caller_runtime.adapters.configuration_client import ConfigurationClient
from caller_runtime.domain.call_lifecycle import AgentPort, CallLifecycle, PhonePort, RepositoryPort
from caller_runtime.domain.configuration import RevisionCache, RevisionSnapshot


@dataclass(frozen=True, slots=True)
class AdmittedConfiguration:
    snapshot: RevisionSnapshot
    lifecycle: CallLifecycle = field(repr=False)


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

    async def admit(
        self,
        *,
        session_id: str,
        provider_call_ref: str,
        event_id: str,
        repository: RepositoryPort,
        agents: AgentPort,
        phone: PhonePort,
    ) -> AdmittedConfiguration | None:
        snapshot = self.select_new_call()
        async with asyncio.timeout(10):
            lifecycle = CallLifecycle(
                repository=repository,
                agents=agents,
                phone=phone,
                configuration=snapshot.for_runtime(),
            )
            admitted = await lifecycle.admit(
                session_id=session_id,
                provider_call_ref=provider_call_ref,
                event_id=event_id,
                revision_id=snapshot.revision_id,
            )
        return AdmittedConfiguration(snapshot, lifecycle) if admitted else None

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
