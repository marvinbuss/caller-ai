"""GA-only Azure realtime transport boundary; qualification and route activation are external."""

import asyncio
import copy
import re
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from agents.realtime.model import RealtimeModel, RealtimeModelConfig, RealtimeModelListener
from agents.realtime.model_inputs import RealtimeModelSendEvent
from azure.core.credentials import AccessToken

SCOPE = "https://ai.azure.com/.default"


class CredentialPort(Protocol):
    async def get_token(self, *scopes: str, **keywords: Any) -> AccessToken: ...


class TransportPort(Protocol):
    async def connect(self, options: RealtimeModelConfig) -> None: ...
    def add_listener(self, listener: RealtimeModelListener) -> None: ...
    def remove_listener(self, listener: RealtimeModelListener) -> None: ...
    async def send_event(self, event: RealtimeModelSendEvent) -> None: ...
    async def send_event_if(
        self, event: RealtimeModelSendEvent, send_if: Callable[[], bool]
    ) -> bool: ...
    async def close(self) -> None: ...


class AzureRealtimeModel(RealtimeModel):
    """Explicit endpoint/credential/transport; no public-OpenAI or preview fallback."""

    def __init__(
        self,
        *,
        endpoint: str,
        deployment: str,
        credential: CredentialPort,
        transport_factory: Callable[[], TransportPort],
        connection_budget: float = 10,
    ) -> None:
        try:
            parsed = urlsplit(endpoint)
            port = parsed.port
        except ValueError:
            raise ValueError("unapproved_azure_realtime_endpoint") from None
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or port not in {None, 443}
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9-]{0,62}\.openai\.azure\.com", parsed.hostname or ""
            )
        ):
            raise ValueError("unapproved_azure_realtime_endpoint")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", deployment):
            raise ValueError("invalid_azure_realtime_deployment")
        if not 0 < connection_budget <= 10:
            raise ValueError("invalid_connection_budget")
        self._url = f"wss://{parsed.hostname}/openai/v1/realtime?{urlencode({'model': deployment})}"
        self._credential = credential
        self._factory = transport_factory
        self._budget = connection_budget
        self._transport: TransportPort | None = None
        self._listeners: list[RealtimeModelListener] = []

    async def _discard(self, transport: TransportPort, deadline: float) -> None:
        for listener in self._listeners:
            transport.remove_listener(listener)
        try:
            async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                await transport.close()
        except Exception:
            pass

    async def connect(self, options: RealtimeModelConfig) -> None:
        if any(key in options for key in ("url", "headers", "api_key", "call_id")):
            raise ValueError("azure_connection_override_forbidden")
        if self._transport is not None:
            raise RuntimeError("azure_realtime_already_connected")
        deadline = time.monotonic() + self._budget
        reserve = min(0.25, self._budget / 5)
        for _ in range(2):
            remaining = deadline - time.monotonic() - reserve
            if remaining <= 0:
                break
            transport: TransportPort | None = None
            try:
                token = await asyncio.wait_for(self._credential.get_token(SCOPE), timeout=remaining)
                if token.expires_on <= time.time() + 60:
                    raise RuntimeError("azure_token_not_current")
                transport = self._factory()
                for listener in self._listeners:
                    transport.add_listener(listener)
                configured = copy.deepcopy(options)
                configured["url"] = self._url
                configured["headers"] = {"Authorization": f"Bearer {token.token}"}
                configured["initial_model_settings"] = {
                    **configured.get("initial_model_settings", {}),
                    "tracing": None,
                }
                remaining = deadline - time.monotonic() - reserve
                if remaining <= 0:
                    raise TimeoutError("azure_connection_deadline")
                await asyncio.wait_for(transport.connect(configured), timeout=remaining)
                self._transport = transport
                return
            except asyncio.CancelledError:
                if transport is not None:
                    await self._discard(transport, deadline)
                raise
            except Exception:
                if transport is not None:
                    await self._discard(transport, deadline)
        raise RuntimeError("azure_realtime_connection_failed") from None

    def add_listener(self, listener: RealtimeModelListener) -> None:
        self._listeners.append(listener)
        if self._transport is not None:
            self._transport.add_listener(listener)

    def remove_listener(self, listener: RealtimeModelListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)
        if self._transport is not None:
            self._transport.remove_listener(listener)

    async def send_event(self, event: RealtimeModelSendEvent) -> None:
        if self._transport is None:
            raise RuntimeError("azure_realtime_not_connected")
        await asyncio.wait_for(self._transport.send_event(event), timeout=self._budget)

    async def send_event_if(
        self, event: RealtimeModelSendEvent, send_if: Callable[[], bool]
    ) -> bool:
        if self._transport is None:
            return False
        return await asyncio.wait_for(
            self._transport.send_event_if(event, send_if), timeout=self._budget
        )

    async def close(self) -> None:
        if self._transport is not None:
            transport, self._transport = self._transport, None
            await self._discard(transport, time.monotonic() + min(self._budget, 0.25))
