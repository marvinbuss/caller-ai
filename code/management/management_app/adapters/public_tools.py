"""Explicit bounded public-read transport with validated DNS pinned beneath TLS."""

import asyncio
import ipaddress
import json
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx

from management_app.services.capability_policy import AuthorizedCapability, public_destination

Resolver = Callable[[str], Awaitable[Sequence[str]]]
BODY_LIMIT = 64 * 1024


class SecretPort(Protocol):
    async def resolve(self, reference: str) -> str: ...


async def resolve_public(host: str) -> Sequence[str]:
    results = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(result[4][0]) for result in results))


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, deadline: float) -> None:
        self._stream, self._deadline = stream, deadline

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async with asyncio.timeout_at(self._deadline):
            async for chunk in self._stream:
                size += len(chunk)
                if size > BODY_LIMIT:
                    raise RuntimeError("capability_response_too_large")
                yield chunk

    async def aclose(self) -> None:
        async with asyncio.timeout_at(self._deadline):
            await self._stream.aclose()


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        destination: str,
        resolver: Resolver,
        network: httpx.AsyncBaseTransport,
        deadline: float,
    ) -> None:
        self._destination = httpx.URL(public_destination(destination))
        self._resolver, self._network, self._deadline = resolver, network, deadline

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url != self._destination or request.method not in {"POST", "GET", "DELETE"}:
            raise RuntimeError("capability_destination_mismatch")
        async with asyncio.timeout_at(self._deadline):
            addresses = await self._resolver(request.url.host)
            if (
                not addresses
                or len(addresses) > 32
                or any(not ipaddress.ip_address(address).is_global for address in addresses)
            ):
                raise RuntimeError("unsafe_capability_destination")
            request.headers["Host"] = self._destination.netloc.decode("ascii")
            request.extensions["sni_hostname"] = self._destination.host
            request.url = request.url.copy_with(host=addresses[0])
            response = await self._network.handle_async_request(request)
            if 300 <= response.status_code < 400:
                await response.aclose()
                raise RuntimeError("capability_redirect_prohibited")
            if not isinstance(response.stream, httpx.AsyncByteStream):
                raise RuntimeError("invalid_capability_response_stream")
            response.stream = BoundedStream(response.stream, self._deadline)
            return response

    async def aclose(self) -> None:
        await self._network.aclose()


def decode_result(content: bytes) -> dict[str, Any]:
    if len(content) > BODY_LIMIT:
        raise ValueError("invalid_capability_result")
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("invalid_capability_result")
    return result


class PublicReadTransport:
    def __init__(
        self,
        *,
        resolver: Resolver = resolve_public,
        network: httpx.AsyncBaseTransport | None = None,
        secrets: SecretPort | None = None,
    ) -> None:
        self._resolver, self._network, self._secrets = resolver, network, secrets

    @asynccontextmanager
    async def pinned_client(
        self, selected: AuthorizedCapability, deadline: float
    ) -> AsyncIterator[httpx.AsyncClient]:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("capability_deadline_exceeded")
        headers = {}
        if selected.credential_ref is not None:
            if self._secrets is None:
                raise RuntimeError("capability_credential_unavailable")
            credential = await self._secrets.resolve(selected.credential_ref)
            if (
                not isinstance(credential, str)
                or not 1 <= len(credential) <= 16384
                or any(character in credential for character in "\r\n")
            ):
                raise RuntimeError("capability_credential_unavailable")
            headers["Authorization"] = "Bearer " + credential
        network = (
            self._network
            if self._network is not None
            else httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)
        )
        transport = PinnedTransport(
            destination=selected.destination,
            resolver=self._resolver,
            network=network,
            deadline=deadline,
        )
        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(remaining),
            headers=headers,
        ) as client:
            yield client

    async def execute(
        self, selected: AuthorizedCapability, *, request_id: str, deadline: float
    ) -> dict[str, Any]:
        if selected.connection_id is not None:
            raise RuntimeError("mcp_transport_required")
        try:
            async with asyncio.timeout_at(deadline):
                async with self.pinned_client(selected, deadline) as client:
                    response = await client.post(
                        selected.destination,
                        json={"actionId": selected.action_id, "inputs": selected.inputs},
                        headers={"X-Request-ID": request_id},
                    )
                    response.raise_for_status()
                    return decode_result(response.content)
        except TimeoutError:
            raise
        except Exception:
            raise RuntimeError("capability_transport_unavailable") from None


class ReviewedTransport:
    def __init__(self, *, direct: PublicReadTransport, mcp: PublicReadTransport) -> None:
        self._direct, self._mcp = direct, mcp

    async def execute(
        self, selected: AuthorizedCapability, *, request_id: str, deadline: float
    ) -> dict[str, Any]:
        transport = self._mcp if selected.connection_id is not None else self._direct
        return await transport.execute(selected, request_id=request_id, deadline=deadline)
