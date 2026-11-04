"""Fixed-origin authenticated ordinary configuration refresh, never permission authority."""

import asyncio
import json
import time
from collections.abc import Callable
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from caller_runtime.adapters.strict_json import unique_object
from caller_runtime.domain.configuration import RevisionCache
from caller_runtime.domain.ports import CredentialPort
from caller_runtime.metrics import RuntimeMetrics


class ConfigurationClient:
    def __init__(
        self,
        *,
        endpoint: str,
        audience: str,
        credential: CredentialPort,
        cache: RevisionCache,
        transport: httpx.AsyncBaseTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        metrics: RuntimeMetrics | None = None,
    ) -> None:
        try:
            parts = urlsplit(endpoint)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or parts.port not in {None, 443}
                or "\\" in endpoint
                or any(ord(character) < 33 for character in endpoint)
                or credential is None
                or not isinstance(cache, RevisionCache)
            ):
                raise ValueError("invalid_configuration_origin")
            self._audience = str(UUID(audience))
        except (ValueError, TypeError):
            raise ValueError("invalid_configuration_client") from None
        self._url = endpoint.rstrip("/") + "/v2/runtime/configuration"
        self._credential, self._transport, self._cache = credential, transport, cache
        self._monotonic, self._metrics = monotonic, metrics
        self._due = float("-inf")
        self._last_ok = False
        self._lock = asyncio.Lock()

    @property
    def cache(self) -> RevisionCache:
        return self._cache

    async def refresh(self, *, require_current: bool = False) -> bool:
        if type(require_current) is not bool:
            return False
        deadline = asyncio.get_running_loop().time() + 10
        try:
            async with asyncio.timeout_at(deadline):
                async with self._lock:
                    observed = self._monotonic()
                    if not require_current and observed < self._due:
                        return self._last_ok
                    self._due = observed + 30
                    self._last_ok = False
                    current = self._cache.current
                    remaining = deadline - asyncio.get_running_loop().time()
                    token = await self._credential.get_token(f"api://{self._audience}/.default")
                    if token.expires_on <= time.time() + remaining:
                        raise ValueError("configuration_token_not_current")
                    headers = {"Authorization": "Bearer " + token.token}
                    if current is not None:
                        headers["If-None-Match"] = current.etag
                    async with httpx.AsyncClient(
                        transport=self._transport,
                        verify=True,
                        trust_env=False,
                        follow_redirects=False,
                        timeout=httpx.Timeout(remaining),
                    ) as client:
                        async with client.stream("GET", self._url, headers=headers) as response:
                            etag = response.headers.get("etag", "")
                            if response.status_code == 304:
                                if current is None or etag != current.etag:
                                    raise ValueError("unsolicited_configuration_not_modified")
                                self._cache.revalidate(current, validated_at=observed)
                            elif response.status_code == 200:
                                content = bytearray()
                                async for chunk in response.aiter_bytes():
                                    content.extend(chunk)
                                    if len(content) > 512 * 1024:
                                        raise ValueError("oversized_configuration_response")
                                document = json.loads(content, object_pairs_hook=unique_object)
                                if not isinstance(document, dict):
                                    raise ValueError("invalid_configuration_response")
                                self._cache.install(document, etag=etag, validated_at=observed)
                            else:
                                raise ValueError("configuration_request_rejected")
                    self._last_ok = True
                    return True
        except Exception:
            if self._metrics is not None:
                self._metrics.dependency_failure("configuration", "unavailable")
            return False

    async def run(self, stop: asyncio.Event) -> None:
        """Explicitly owned loop only; constructor/import never schedules background work."""
        while not stop.is_set():
            await self.refresh()
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=max(0.01, self._due - self._monotonic())
                )
            except TimeoutError:
                pass
