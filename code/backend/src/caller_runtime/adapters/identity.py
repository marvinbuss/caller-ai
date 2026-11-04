"""Fixed-key manager assertions; no key discovery, algorithm negotiation or allocation."""

import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.utils import base64url_decode

from caller_runtime.adapters.strict_json import unique_object
from caller_runtime.domain.conversation import StartPermit, utc


class TestPermitVerifier:
    __test__ = False

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        keys: Mapping[str, bytes],
        utc_now: Callable[[], datetime],
        max_clock_skew_seconds: float,
    ) -> None:
        try:
            if (
                not issuer
                or len(issuer) > 512
                or not audience
                or len(audience) > 512
                or not 1 <= len(keys) <= 4
                or utc_now is None
                or type(max_clock_skew_seconds) not in (int, float)
                or not math.isfinite(max_clock_skew_seconds)
                or not 0 <= max_clock_skew_seconds <= 1
            ):
                raise ValueError("invalid_binding")
            self._keys: dict[str, RSAPublicKey] = {}
            for name, encoded in keys.items():
                if (
                    not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", name)
                    or not isinstance(encoded, bytes)
                    or len(encoded) > 16384
                ):
                    raise ValueError("invalid_verification_key")
                key = serialization.load_pem_public_key(encoded)
                if not isinstance(key, RSAPublicKey) or not 2048 <= key.key_size <= 8192:
                    raise ValueError("invalid_verification_key")
                self._keys[name] = key
        except Exception:
            raise ValueError("invalid_test_permit_configuration") from None
        self._issuer, self._audience, self._utc_now = issuer, audience, utc_now
        self._skew = timedelta(seconds=max_clock_skew_seconds)
        self._last_observed: datetime | None = None

    async def verify(self, raw: str) -> StartPermit:
        try:
            if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > 6144:
                raise ValueError("invalid_permit")
            segments = raw.split(".")
            if len(segments) != 3:
                raise ValueError("invalid_permit")
            header = json.loads(base64url_decode(segments[0]), object_pairs_hook=unique_object)
            payload = json.loads(base64url_decode(segments[1]), object_pairs_hook=unique_object)
            if (
                not isinstance(header, dict)
                or set(header) != {"alg", "typ", "kid"}
                or header["alg"] != "RS256"
                or header["typ"] != "JWT"
                or not isinstance(header["kid"], str)
                or header["kid"] not in self._keys
                or not isinstance(payload, dict)
            ):
                raise ValueError("invalid_permit")
            claims: dict[str, Any] = jwt.decode(
                raw,
                key=self._keys[header["kid"]],
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "require": ["iss", "aud", "iat", "exp"],
                },
            )
            if claims != payload:
                raise ValueError("invalid_permit")
            permit = StartPermit.model_validate_json(json.dumps(claims, allow_nan=False))
            now = utc(self._utc_now())
            if self._last_observed is not None and now < self._last_observed:
                raise ValueError("clock_unavailable")
            self._last_observed = now
            if (
                permit.issued_at > now + self._skew
                or now + self._skew < permit.admission_started_at
                or now + self._skew >= min(permit.admission_deadline_utc, permit.auth_expires_at)
                or (now + self._skew).timestamp() >= permit.expires_epoch
            ):
                raise ValueError("expired_permit")
            return permit
        except Exception:
            raise PermissionError("invalid_test_permit") from None
