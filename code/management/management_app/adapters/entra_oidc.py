"""Explicit bounded Entra metadata transport; never follow token-specified key URLs."""

import asyncio
import copy
import json
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from management_app.security import EntraPolicy, LoginFlow


class EntraSigningKeys:
    def __init__(self, *, policy: EntraPolicy, client: httpx.AsyncClient) -> None:
        self.url = f"https://login.microsoftonline.com/{policy.tenant_id}/discovery/v2.0/keys"
        self.client = client
        self._expires = 0.0
        self._document: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def signing_keys(self) -> dict[str, Any]:
        async with self._lock:
            if self._document is not None and time.monotonic() < self._expires:
                return copy.deepcopy(self._document)
            try:
                async with asyncio.timeout(2):
                    async with self.client.stream(
                        "GET", self.url, timeout=2, follow_redirects=False
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
                self._document = document
                self._expires = time.monotonic() + 300
                return copy.deepcopy(document)
            except Exception:
                raise RuntimeError("identity_authority_unavailable") from None


class MsalApplication(Protocol):
    def initiate_auth_code_flow(
        self, scopes: list[str], *, redirect_uri: str, state: str, response_mode: str
    ) -> dict[str, Any]: ...

    def acquire_token_by_auth_code_flow(
        self, auth_code_flow: dict[str, Any], auth_response: dict[str, str]
    ) -> dict[str, Any]: ...


class MsalCodeFlow:
    def __init__(
        self, *, policy: EntraPolicy, application_factory: Callable[[], MsalApplication]
    ) -> None:
        self.policy = policy
        self.factory = application_factory
        self._jobs: set[asyncio.Task[Any]] = set()

    async def _run(self, operation: Callable[[], Any]) -> Any:
        if len(self._jobs) >= 4:
            raise HTTPException(503, "identity_capacity_unavailable")
        job = asyncio.create_task(asyncio.to_thread(operation))
        self._jobs.add(job)

        def completed(task: asyncio.Task[Any]) -> None:
            self._jobs.discard(task)
            if not task.cancelled():
                task.exception()

        job.add_done_callback(completed)
        try:
            async with asyncio.timeout(5):
                return await asyncio.shield(job)
        except Exception:
            raise HTTPException(401, "invalid_identity_response") from None

    async def begin(self, state: str) -> LoginFlow:
        def prepare() -> LoginFlow:
            application = self.factory()
            flow = application.initiate_auth_code_flow(
                [f"api://{self.policy.api_audience}/{self.policy.manager_scope}"],
                redirect_uri=self.policy.origin + "/auth/callback",
                state=state,
                response_mode="form_post",
            )
            return LoginFlow(url=flow["auth_uri"], state=state, private=(application, flow))

        result: LoginFlow = await self._run(prepare)
        return result

    async def finish(self, flow: LoginFlow, response: dict[str, str]) -> tuple[str, str]:
        def exchange() -> tuple[str, str]:
            application, original = flow.private
            result = application.acquire_token_by_auth_code_flow(original, response)
            token = result.get("id_token")
            claims = result.get("id_token_claims")
            if (
                not isinstance(token, str)
                or not isinstance(claims, dict)
                or not isinstance(claims.get("nonce"), str)
            ):
                raise ValueError("invalid_identity_response")
            return token, claims["nonce"]

        result: tuple[str, str] = await self._run(exchange)
        return result


class BoundedEntraHttp:
    def __init__(self, *, policy: EntraPolicy) -> None:
        self.prefix = "/" + policy.tenant_id + "/"

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            target = urlsplit(url)
            if (
                target.scheme != "https"
                or target.netloc != "login.microsoftonline.com"
                or target.username
                or target.password
                or not target.path.startswith(self.prefix)
            ):
                raise ValueError("unapproved_identity_endpoint")
            kwargs.pop("timeout", None)
            if any(name in kwargs for name in ("follow_redirects", "verify", "proxy", "proxies")):
                raise ValueError("identity_transport_override_prohibited")
            with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
                with client.stream(
                    method, url, timeout=2, follow_redirects=False, **kwargs
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise ValueError("identity_redirect_prohibited")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 128 * 1024:
                            raise ValueError("identity_response_too_large")
                    return httpx.Response(
                        response.status_code,
                        content=bytes(data),
                        headers=response.headers,
                        request=response.request,
                    )
        except Exception:
            raise RuntimeError("identity_authority_unavailable") from None

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._request("POST", url, **kwargs)
