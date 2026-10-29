"""Fixed-origin managed-identity gateway client; no direct tool/MCP execution."""

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from caller_runtime.adapters.azure_realtime import CredentialPort


class CapabilityClient:
    def __init__(
        self,
        *,
        endpoint: str,
        audience: str,
        credential: CredentialPort,
        transport: httpx.AsyncBaseTransport | None = None,
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
            ):
                raise ValueError("invalid_gateway_origin")
            self._audience = str(UUID(audience))
        except ValueError:
            raise ValueError("invalid_gateway_configuration") from None
        self._url = endpoint.rstrip("/") + "/v1/runtime/capabilities/execute"
        self._credential, self._transport = credential, transport

    async def execute(self, document: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        try:
            async with asyncio.timeout_at(deadline):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("capability_deadline_exceeded")
                token = await self._credential.get_token(f"api://{self._audience}/.default")
                if token.expires_on <= time.time() + remaining:
                    raise ValueError("gateway_token_not_current")
                content = json.dumps(document, allow_nan=False, separators=(",", ":")).encode(
                    "utf-8"
                )
                if len(content) > 64 * 1024:
                    raise ValueError("invalid_gateway_request")
                async with httpx.AsyncClient(
                    transport=self._transport,
                    verify=True,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(remaining),
                ) as client:
                    async with client.stream(
                        "POST",
                        self._url,
                        content=content,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer " + token.token,
                        },
                    ) as response:
                        if response.status_code != 200:
                            raise ValueError("gateway_request_rejected")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 64 * 1024:
                                raise ValueError("invalid_gateway_response")
                result = json.loads(body)
                if (
                    not isinstance(result, dict)
                    or set(result) != {"requestId", "sessionId", "agentId", "agentEpoch", "result"}
                    or not isinstance(result["result"], dict)
                    or any(
                        result[key] != document[key]
                        for key in ("requestId", "sessionId", "agentId", "agentEpoch")
                    )
                ):
                    raise ValueError("gateway_response_binding_mismatch")
                return result
        except Exception:
            raise RuntimeError("capability_gateway_unavailable") from None
