"""Verifies the management workload token: signature, tenant, audience, caller, role (FR-052).

The token is the managed identity token of the management app for the runtime's own app
registration. Keys come only from the tenant's fixed discovery URL (no redirects, 64 KiB cap,
at most 32 keys, cached for five minutes), never from a URL in a token. Every failure raises the
same `unverified_workload` so a caller learns nothing about why.
"""

import asyncio
import copy
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jwt
from starlette.requests import HTTPConnection

MAX_TOKEN_BYTES = 8192
KEY_CACHE_SECONDS = 300


class EntraSigningKeys:
    """Fetches and caches the tenant's signing keys from the fixed discovery address."""

    def __init__(self, *, tenant_id: str, client: httpx.AsyncClient) -> None:
        self._url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        self._client = client
        self._expires = 0.0
        self._document: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def __call__(self) -> dict[str, Any]:
        async with self._lock:
            if self._document is not None and time.monotonic() < self._expires:
                return copy.deepcopy(self._document)
            async with asyncio.timeout(2):
                async with self._client.stream(
                    "GET", self._url, timeout=2, follow_redirects=False
                ) as response:
                    if response.status_code != 200:
                        raise ValueError("invalid_key_response")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 64 * 1024:
                            raise ValueError("oversized_key_response")
            document = json.loads(data)
            if (
                not isinstance(document, dict)
                or not isinstance(document.get("keys"), list)
                or not 1 <= len(document["keys"]) <= 32
            ):
                raise ValueError("invalid_key_response")
            self._document, self._expires = document, time.monotonic() + KEY_CACHE_SECONDS
            return copy.deepcopy(document)


class EntraWorkloadIdentity:
    """Implements `WorkloadIdentity`: returns the verified caller's object ID."""

    def __init__(
        self,
        *,
        tenant_id: str,
        audience: str,
        role: str,
        principal_id: str,
        signing_keys: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        if not all((tenant_id, audience, role, principal_id)) or signing_keys is None:
            raise ValueError("invalid_workload_identity_configuration")
        self._tenant = tenant_id.lower()
        self._audiences = [audience, f"api://{audience}"]
        self._role = role
        self._principal = principal_id.lower()
        self._issuers = (
            f"https://login.microsoftonline.com/{self._tenant}/v2.0",
            f"https://sts.windows.net/{self._tenant}/",
        )
        self._signing_keys = signing_keys

    async def require_runtime(self, request: HTTPConnection) -> str:
        try:
            values = request.headers.getlist("authorization")
            if len(values) != 1 or not values[0].startswith("Bearer "):
                raise ValueError("missing_token")
            token = values[0].removeprefix("Bearer ")
            if not token or token.strip() != token or len(token.encode()) > MAX_TOKEN_BYTES:
                raise ValueError("invalid_token")
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError("invalid_header")
            key = next(
                (
                    item
                    for item in (await self._signing_keys())["keys"]
                    if isinstance(item, dict) and item.get("kid") == header["kid"]
                ),
                None,
            )
            if key is None:
                raise ValueError("unknown_key")
            claims: dict[str, Any] = jwt.decode(
                token,
                key=jwt.PyJWK(key).key,
                algorithms=["RS256"],
                audience=self._audiences,
                options={"require": ["exp", "iss", "aud"]},
            )
            roles = claims.get("roles")
            if (
                claims.get("iss") not in self._issuers
                or str(claims.get("tid", "")).lower() != self._tenant
                or str(claims.get("oid", "")).lower() != self._principal
                or not isinstance(roles, list)
                or self._role not in roles
            ):
                raise ValueError("invalid_claims")
            return self._principal
        except Exception:
            raise PermissionError("unverified_workload") from None
