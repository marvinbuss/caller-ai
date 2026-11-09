"""The one place that composes a request to the backend conversation endpoints.

Text and voice both go through `compose_request`, so the console's traffic is exactly what any
other client sends: the workload token in `Authorization`, one signed start permit and an
optional empty `settings` object. The permit for a text turn is minted on the server for that
turn only and is never shown to the browser.
"""

import hashlib
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from management_app.models.test_session import StartPermit, utc
from management_app.security import TestPermitSigner

TEXT_PATH = "/v1/conversations/text"
VOICE_PATH = "/v1/conversations/voice"
VOICE_SUBPROTOCOL = "caller.voice.v1"


def correlation_for(request_id: str) -> str:
    """One opaque id per console session, sent to the runtime and recorded in both logs."""
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class BackendRequest:
    """A fully composed request: an HTTP POST for text or a WebSocket upgrade for voice."""

    kind: Literal["text", "voice"]
    url: str
    headers: dict[str, str] = field(repr=False)
    subprotocols: tuple[str, ...]
    # The JSON body of the text POST, or the first WebSocket message of a voice session.
    message: dict[str, Any] = field(repr=False)


def compose_request(
    kind: Literal["text", "voice"],
    *,
    origin: str,
    token: str,
    signed_permit: str,
    request_id: str,
    text: str | None = None,
    conversation_id: str | None = None,
    settings: Mapping[str, Any] | None = None,
    correlation_id: str | None = None,
) -> BackendRequest:
    if kind not in {"text", "voice"} or not token or not signed_permit or not request_id:
        raise ValueError("invalid_backend_request")
    # Reserved extension point: omitted or empty today. The backend rejects anything else too, but
    # the console never sends it, so a console bug cannot become an authority change.
    if settings is not None and len(settings) != 0:
        raise ValueError("settings_not_supported")
    extra: dict[str, Any] = {} if settings is None else {"settings": {}}
    headers = {"Authorization": "Bearer " + token}
    if correlation_id is not None:
        headers["X-Correlation-ID"] = correlation_id
    if kind == "voice":
        if text is not None or conversation_id is not None:
            raise ValueError("invalid_backend_request")
        return BackendRequest(
            kind="voice",
            url="wss://" + origin + VOICE_PATH,
            headers=headers,
            subprotocols=(VOICE_SUBPROTOCOL,),
            message={
                "type": "authenticate",
                "permit": signed_permit,
                "requestId": request_id,
            }
            | extra,
        )
    if text is None:
        raise ValueError("invalid_backend_request")
    body: dict[str, Any] = {"permit": signed_permit, "requestId": request_id, "text": text}
    if conversation_id is not None:
        body["conversationId"] = conversation_id
    return BackendRequest(
        kind="text",
        url="https://" + origin + TEXT_PATH,
        headers=headers | {"Content-Type": "application/json", "Accept": "text/event-stream"},
        subprotocols=(),
        message=body | extra,
    )


class TurnPermits:
    """Mints a fresh single-use start permit for each text turn of an open console session."""

    def __init__(self, signer: TestPermitSigner, utc_now: Callable[[], datetime]) -> None:
        self._signer, self._utc_now = signer, utc_now

    def mint(self, opened: StartPermit) -> tuple[StartPermit, str]:
        now = utc(self._utc_now())
        claims = opened.model_copy(
            update={
                "issued_epoch": int(now.timestamp()),
                "expires_epoch": min(
                    int(now.timestamp()) + 30, int(opened.auth_expires_at.timestamp())
                ),
                "issued_at": now,
                "admission_started_at": now,
                "admission_deadline_utc": now + timedelta(seconds=10),
                "request_id": "turn-" + secrets.token_hex(8),
                "nonce": secrets.token_hex(32),
            }
        )
        return claims, self._signer.sign(claims)
