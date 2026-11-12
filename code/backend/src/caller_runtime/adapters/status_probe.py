"""Reachability of the text and voice models without a billable call, for the status endpoint.

A check gets a token for the Foundry scope and opens a TLS connection to the model host, both
within 2 seconds; no model request is sent. Results are reused for 60 seconds and at most one
check per model runs at a time. The text and live adapters report the outcome of each real model
call through `record_call`, so the status also shows the last real call. Each check emits a
`status.probe` event with the model, the outcome and its duration; no host, token or key is logged.
"""

import asyncio
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from azure.core.credentials import AccessToken

from caller_runtime.observability import Telemetry

Model = Literal["text", "voice"]
SCOPE = "https://ai.azure.com/.default"
CACHE_SECONDS = 60
PROBE_SECONDS = 2


class ProbeCredential(Protocol):
    async def get_token(self, *scopes: str) -> AccessToken: ...


async def tls_connect(host: str) -> None:
    _reader, writer = await asyncio.open_connection(host, 443, ssl=ssl.create_default_context())
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


@dataclass(slots=True)
class _Result:
    reachable: bool
    checked_at: datetime
    observed: float


def instant(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class StatusProbe:
    def __init__(
        self,
        *,
        credential: Callable[[], ProbeCredential | None],
        hosts: dict[Model, str],
        telemetry: Callable[[], Telemetry | None],
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        connect: Callable[[str], Awaitable[None]] = tls_connect,
    ) -> None:
        self._credential, self._hosts, self._telemetry = credential, dict(hosts), telemetry
        self._utc_now, self._monotonic, self._connect = utc_now, monotonic, connect
        self._results: dict[Model, _Result] = {}
        self._locks: dict[Model, asyncio.Lock] = {}
        self._calls: dict[Model, tuple[str, datetime]] = {}

    def record_call(self, model: Model, success: bool) -> None:
        self._calls[model] = ("success" if success else "failure", self._utc_now())

    def last_call(self, model: Model) -> tuple[str | None, str | None]:
        outcome = self._calls.get(model)
        return (outcome[0], instant(outcome[1])) if outcome else (None, None)

    async def check(self, model: Model) -> tuple[bool, str | None]:
        cached = self._results.get(model)
        if cached is not None and self._monotonic() - cached.observed < CACHE_SECONDS:
            return cached.reachable, instant(cached.checked_at)
        lock = self._locks.setdefault(model, asyncio.Lock())
        async with lock:
            cached = self._results.get(model)
            if cached is not None and self._monotonic() - cached.observed < CACHE_SECONDS:
                return cached.reachable, instant(cached.checked_at)
            started = self._monotonic()
            reachable = False
            try:
                credential = self._credential()
                if credential is None:
                    raise PermissionError("status_credential_unavailable")
                async with asyncio.timeout(PROBE_SECONDS):
                    token = await credential.get_token(SCOPE)
                    if not token.token:
                        raise PermissionError("status_token_unavailable")
                    await self._connect(self._hosts[model])
                reachable = True
            except Exception:
                reachable = False
            result = _Result(reachable, self._utc_now(), self._monotonic())
            self._results[model] = result
            telemetry = self._telemetry()
            if telemetry is not None:
                telemetry.event(
                    "status.probe",
                    "success" if reachable else "failure",
                    measurement=f"{model}_probe_seconds",
                    value=max(0.0, result.observed - started),
                )
            return reachable, instant(result.checked_at)
