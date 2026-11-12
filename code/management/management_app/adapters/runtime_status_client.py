"""Reads `GET /v1/status` from the runtime with the management workload credential.

The body names hosts, the text deployment, the region, the voice model, the voice catalog and the
conversation limits; it is checked for shape and size before it reaches the browser. Any failure
raises, so the caller reports the runtime as unreachable rather than showing partial data.
"""

import json
import math
from typing import Any

import httpx

from management_app.adapters.test_session_client import WorkloadCredential

STATUS_PATH = "/v1/status"
MAX_STATUS_BYTES = 64 * 1024
STATUS_SECONDS = 5
BODY_KEYS = frozenset({"text", "voice", "supportedVoices", "defaults", "limits"})
TEXT_KEYS = frozenset(
    {"endpointHost", "deployment", "reachable", "checkedAt", "lastCallOutcome", "lastCallAt"}
)
VOICE_KEYS = frozenset(
    {"endpointHost", "region", "model", "reachable", "checkedAt", "lastCallOutcome", "lastCallAt"}
)
VOICE_ENTRY_KEYS = frozenset({"id", "name", "language", "accent", "presentation"})


def checked(body: Any) -> dict[str, Any]:
    """The runtime body if it has exactly the contract's shape; otherwise `ValueError`."""
    if (
        not isinstance(body, dict)
        or set(body) != BODY_KEYS
        or not isinstance(body["text"], dict)
        or set(body["text"]) != TEXT_KEYS
        or not isinstance(body["voice"], dict)
        or set(body["voice"]) != VOICE_KEYS
        or not isinstance(body["supportedVoices"], list)
        or not body["supportedVoices"]
        or any(
            not isinstance(entry, dict)
            or set(entry) != VOICE_ENTRY_KEYS
            or not isinstance(entry["id"], str)
            or not isinstance(entry["name"], str)
            or not isinstance(entry["language"], str)
            for entry in body["supportedVoices"]
        )
        or not isinstance(body["defaults"], dict)
        or not isinstance(body["limits"], dict)
        or any(
            not isinstance(body[model]["reachable"], bool)
            or "://" in str(body[model]["endpointHost"])
            or "/" in str(body[model]["endpointHost"])
            for model in ("text", "voice")
        )
    ):
        raise ValueError("invalid_runtime_status")
    return body


class RuntimeStatusClient:
    def __init__(
        self,
        *,
        origin: str,
        audience: str,
        credential: WorkloadCredential,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not origin or not audience or credential is None:
            raise ValueError("explicit_runtime_status_client_required")
        self._origin, self._audience = origin, audience
        self._credential, self._transport = credential, transport

    async def fetch(self) -> dict[str, Any]:
        token = await self._credential.get_token(f"api://{self._audience}/.default")
        if not token.token or not math.isfinite(token.expires_on):
            raise PermissionError("runtime_status_workload_unavailable")
        async with httpx.AsyncClient(
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(STATUS_SECONDS),
        ) as client:
            async with client.stream(
                "GET",
                "https://" + self._origin + STATUS_PATH,
                headers={"Authorization": "Bearer " + token.token, "Accept": "application/json"},
            ) as response:
                if response.status_code != 200:
                    raise PermissionError("runtime_status_unavailable")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_STATUS_BYTES:
                        raise ValueError("runtime_status_limit")
        return checked(json.loads(bytes(body)))
