"""Frozen management startup settings read from `CALLER_*` environment variables.

Independent of the backend settings and free of live clients: local mode never discovers
credentials and needs no other setting. Live mode needs every management setting of the
application-settings contract; a missing or invalid one stops the start with one fixed reason code
and a message that never holds a value (the two secret settings arrive as Key Vault references that
the platform resolves). Content tracing is forbidden and validation errors hide their input.
`composition_fingerprint` ties a reviewed resource manifest to the settings it was reviewed under.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REASON_CODES = (
    "settings_missing",
    "settings_invalid",
    "review_missing",
    "fingerprint_mismatch",
    "identity_unavailable",
    "store_unavailable",
    "key_invalid",
)
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ORIGIN = re.compile(r"https://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?/?")
ROLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
DATABASE = re.compile(r"[A-Za-z0-9_-]{1,255}")
KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
SCOPE = re.compile(r"api://[0-9a-fA-F-]{36}/\.default")
FINGERPRINT = re.compile(r"[0-9a-f]{64}")
PRIVATE_KEY = re.compile(
    r"-----BEGIN (EC |RSA )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]+"
    r"-----END (EC |RSA )?PRIVATE KEY-----"
)


class StartupError(RuntimeError):
    """A start refusal carrying exactly one fixed reason code and never a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LiveSettings:
    azure_client_id: str
    review_reference: str
    reviewed_fingerprint: str
    cosmos_endpoint: str
    cosmos_database: str
    audit_retention_seconds: int
    tenant_id: str
    entra_client_id: str
    api_audience: str
    manager_role: str
    manager_scope: str
    test_role: str
    runtime_role: str
    management_origin: str
    public_origin: str
    runtime_endpoint: str
    runtime_audience: str
    permit_issuer: str
    permit_audience: str
    signing_key_id: str
    entra_client_secret: str = field(repr=False)
    signing_key_pem: str = field(repr=False)

    def non_secret(self) -> dict[str, str | int]:
        """Every setting that is not a secret, for the reviewed-composition fingerprint."""
        return {
            name: value
            for name, value in self.__dict__.items()
            if name not in {"entra_client_secret", "signing_key_pem"}
        }


def _text(value: str | None) -> str:
    if value is None or not value.strip():
        raise StartupError("settings_missing")
    return value.strip()


def _match(pattern: re.Pattern[str], value: str | None) -> str:
    text = _text(value)
    if pattern.fullmatch(text) is None:
        raise StartupError("settings_invalid")
    return text


def _number(value: str | None, low: int, high: int) -> int:
    text = _text(value)
    if re.fullmatch(r"[0-9]{1,9}", text) is None or not low <= int(text) <= high:
        raise StartupError("settings_invalid")
    return int(text)


def _secret(value: SecretStr | None) -> str:
    return _text(value.get_secret_value() if value is not None else None)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALLER_",
        frozen=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    mode: Literal["local", "live"] = "local"
    instance_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    trace_destination: Literal["memory"] = "memory"
    trace_include_content: bool = False
    # Live settings stay raw text so that a bad value gives a fixed reason code, never an echo.
    azure_client_id: str | None = Field(
        default=None, validation_alias=AliasChoices("AZURE_CLIENT_ID")
    )
    review_reference: str | None = None
    reviewed_fingerprint: str | None = None
    cosmos_endpoint: str | None = None
    cosmos_database: str | None = None
    audit_retention_seconds: str | None = None
    tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = Field(default=None, repr=False)
    api_audience: str | None = None
    manager_role: str | None = None
    manager_scope: str | None = None
    test_role: str | None = None
    runtime_role: str | None = None
    management_origin: str | None = None
    public_origin: str | None = None
    runtime_endpoint: str | None = None
    runtime_audience: str | None = None
    permit_issuer: str | None = None
    permit_audience: str | None = None
    signing_key_id: str | None = None
    signing_key_pem: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_privacy(self) -> Self:
        if self.trace_include_content:
            raise ValueError("trace_content_forbidden")
        return self

    def live(self) -> LiveSettings:
        """Validate every live setting; raises `StartupError` with one fixed reason code."""
        client = _match(GUID, self.azure_client_id)
        reference = (self.review_reference or "").strip()
        if not reference:
            raise StartupError("review_missing")
        if len(reference) > 128 or not reference.isprintable():
            raise StartupError("settings_invalid")
        reviewed = _text(self.reviewed_fingerprint)
        if FINGERPRINT.fullmatch(reviewed) is None:
            raise StartupError("settings_invalid")
        pem = _secret(self.signing_key_pem)
        if len(pem) > 8192 or PRIVATE_KEY.fullmatch(pem) is None:
            raise StartupError("key_invalid")
        return LiveSettings(
            azure_client_id=client,
            review_reference=reference,
            reviewed_fingerprint=reviewed,
            cosmos_endpoint=_match(ORIGIN, self.cosmos_endpoint),
            cosmos_database=_match(DATABASE, self.cosmos_database),
            audit_retention_seconds=_number(self.audit_retention_seconds, 1, 315_360_000),
            tenant_id=_match(GUID, self.tenant_id),
            entra_client_id=_match(GUID, self.entra_client_id),
            api_audience=_match(GUID, self.api_audience),
            manager_role=_match(ROLE, self.manager_role),
            manager_scope=_match(SCOPE, self.manager_scope),
            test_role=_match(ROLE, self.test_role),
            runtime_role=_match(ROLE, self.runtime_role),
            management_origin=_match(ORIGIN, self.management_origin),
            public_origin=_match(ORIGIN, self.public_origin),
            runtime_endpoint=_match(ORIGIN, self.runtime_endpoint),
            runtime_audience=_match(GUID, self.runtime_audience),
            permit_issuer=_match(ORIGIN, self.permit_issuer),
            permit_audience=_match(ORIGIN, self.permit_audience),
            signing_key_id=_match(KEY_ID, self.signing_key_id),
            entra_client_secret=_secret(self.entra_client_secret),
            signing_key_pem=pem,
        )

    def require_fingerprint(self, computed: str) -> None:
        """Refuse the start when the computed fingerprint differs from the reviewed one."""
        reviewed = self.live().reviewed_fingerprint
        if not hmac.compare_digest(reviewed.encode(), computed.encode()):
            raise StartupError("fingerprint_mismatch")

    def validate_startup(self) -> None:
        if self.mode == "live":
            self.live()

    def composition_fingerprint(self, manifest: Mapping[str, Any]) -> str:
        value = {
            "mode": self.mode,
            "trace_destination": self.trace_destination,
            "trace_include_content": self.trace_include_content,
            "manifest": dict(manifest),
        }
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
