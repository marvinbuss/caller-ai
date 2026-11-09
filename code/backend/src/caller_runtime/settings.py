"""Frozen startup settings read from `CALLER_*` environment variables.

Local mode never discovers live credentials and needs no other setting. Live mode needs every
setting of the application-settings contract; a missing or invalid one stops the start with one
fixed reason code and a message that never holds a value. Settings that would export content
(`trace_include_content`) or enable unreviewed provider tracing fail validation instead of being
ignored, and errors never echo their input.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal, Self
from uuid import uuid4

from pydantic import AliasChoices, Field, model_validator
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
LIVE_REGIONS = ("canadacentral", "centralus", "eastus2", "swedencentral")
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ORIGIN = re.compile(r"https://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?/?")
LABEL = r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
HOST = re.compile(rf"{LABEL}(\.{LABEL})+")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
# Azure model deployment names may contain dots, as in `gpt-6.1-sol`.
DEPLOYMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
DATABASE = re.compile(r"[A-Za-z0-9_-]{1,255}")
KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
PUBLIC_KEY = re.compile(r"-----BEGIN PUBLIC KEY-----\s+[A-Za-z0-9+/=\s]+-----END PUBLIC KEY-----")


class StartupError(RuntimeError):
    """A start refusal carrying exactly one fixed reason code and never a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LiveSettings:
    azure_client_id: str
    review_reference: str
    cosmos_endpoint: str
    cosmos_database: str
    text_history_ttl_seconds: int
    management_endpoint: str
    management_audience: str
    text_endpoint: str
    text_deployment: str
    live_endpoint: str
    live_region: str
    live_voice: str
    approved_hosts: tuple[str, ...]
    max_input_tokens: int
    max_output_tokens: int
    available_live_sessions: int
    manager_issuer: str
    manager_audience: str
    permit_verification_keys: dict[str, str]
    max_clock_skew_seconds: int
    tenant_id: str
    workload_audience: str
    workload_role: str
    workload_principal_id: str


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


def _keys(value: str | None) -> dict[str, str]:
    text = _text(value)
    try:
        document = json.loads(text)
    except ValueError:
        raise StartupError("settings_invalid") from None
    if (
        not isinstance(document, dict)
        or not 1 <= len(document) <= 4
        or any(
            not isinstance(key, str)
            or KEY_ID.fullmatch(key) is None
            or not isinstance(pem, str)
            or len(pem) > 4096
            or PUBLIC_KEY.fullmatch(pem.strip()) is None
            for key, pem in document.items()
        )
    ):
        raise StartupError("settings_invalid")
    return dict(document)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALLER_",
        frozen=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    mode: Literal["local", "live"] = "local"
    instance_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    agents_sdk_tracing_enabled: bool = False
    realtime_tracing_enabled: bool = False
    trace_destination: Literal["memory"] = "memory"
    trace_include_content: bool = False
    # Live settings stay raw text so that a bad value gives a fixed reason code, never an echo.
    azure_client_id: str | None = Field(
        default=None, validation_alias=AliasChoices("AZURE_CLIENT_ID")
    )
    review_reference: str | None = None
    cosmos_endpoint: str | None = None
    cosmos_database: str | None = None
    text_history_ttl_seconds: str | None = None
    management_endpoint: str | None = None
    management_audience: str | None = None
    text_endpoint: str | None = None
    text_deployment: str | None = None
    live_endpoint: str | None = None
    live_region: str | None = None
    live_voice: str | None = None
    approved_hosts: str | None = None
    max_input_tokens: str | None = None
    max_output_tokens: str | None = None
    available_live_sessions: str | None = None
    manager_issuer: str | None = None
    manager_audience: str | None = None
    permit_verification_keys: str | None = None
    max_clock_skew_seconds: str | None = None
    tenant_id: str | None = None
    workload_audience: str | None = None
    workload_role: str | None = None
    workload_principal_id: str | None = None

    @model_validator(mode="after")
    def validate_privacy(self) -> Self:
        if self.trace_include_content:
            raise ValueError("trace_content_forbidden")
        if self.realtime_tracing_enabled:
            raise ValueError("realtime_tracing_destination_unsupported")
        return self

    def live(self) -> LiveSettings:
        """Validate every live setting; raises `StartupError` with one fixed reason code."""
        client = _match(GUID, self.azure_client_id)
        reference = (self.review_reference or "").strip()
        if not reference:
            raise StartupError("review_missing")
        if IDENTIFIER.fullmatch(reference) is None:
            raise StartupError("settings_invalid")
        hosts = tuple(item.strip() for item in _text(self.approved_hosts).split(","))
        if not 1 <= len(hosts) <= 8 or any(HOST.fullmatch(host) is None for host in hosts):
            raise StartupError("settings_invalid")
        region = _text(self.live_region)
        if region not in LIVE_REGIONS:
            raise StartupError("settings_invalid")
        return LiveSettings(
            azure_client_id=client,
            review_reference=reference,
            cosmos_endpoint=_match(ORIGIN, self.cosmos_endpoint),
            cosmos_database=_match(DATABASE, self.cosmos_database),
            text_history_ttl_seconds=_number(self.text_history_ttl_seconds, 1, 2_592_000),
            management_endpoint=_match(ORIGIN, self.management_endpoint),
            management_audience=_match(GUID, self.management_audience),
            text_endpoint=_match(ORIGIN, self.text_endpoint),
            text_deployment=_match(DEPLOYMENT, self.text_deployment),
            live_endpoint=_match(ORIGIN, self.live_endpoint),
            live_region=region,
            live_voice=_match(NAME, self.live_voice),
            approved_hosts=hosts,
            max_input_tokens=_number(self.max_input_tokens, 1024, 131_072),
            max_output_tokens=_number(self.max_output_tokens, 1, 4096),
            available_live_sessions=_number(self.available_live_sessions, 5, 10_000),
            manager_issuer=_match(ORIGIN, self.manager_issuer),
            manager_audience=_match(ORIGIN, self.manager_audience),
            permit_verification_keys=_keys(self.permit_verification_keys),
            max_clock_skew_seconds=_number(self.max_clock_skew_seconds, 0, 1),
            tenant_id=_match(GUID, self.tenant_id),
            workload_audience=_match(GUID, self.workload_audience),
            workload_role=_match(NAME, self.workload_role),
            workload_principal_id=_match(GUID, self.workload_principal_id),
        )

    def validate_startup(self) -> None:
        if self.mode == "live":
            self.live()
