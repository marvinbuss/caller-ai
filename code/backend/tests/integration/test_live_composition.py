"""Live composition: settings become the reviewed runtime, failures give fixed reason codes."""

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from caller_runtime.composition import ConversationRuntime, RuntimeOptions
from caller_runtime.live import build_live_runtime, runtime_options
from caller_runtime.main import create_app
from caller_runtime.observability import Telemetry
from caller_runtime.services.admission import AdmissionError
from caller_runtime.settings import Settings, StartupError

PUBLIC_PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)
MANAGEMENT = "management.example.azurecontainerapps.io"


def environment() -> dict[str, str]:
    return {
        "CALLER_MODE": "live",
        "AZURE_CLIENT_ID": "00000000-0000-4000-8000-000000000020",
        "CALLER_REVIEW_REFERENCE": "review-2026-001",
        "CALLER_COSMOS_ENDPOINT": "https://cosmos-int.documents.azure.com:443/",
        "CALLER_COSMOS_DATABASE": "caller",
        "CALLER_TEXT_HISTORY_TTL_SECONDS": "86400",
        "CALLER_MANAGEMENT_ENDPOINT": f"https://{MANAGEMENT}",
        "CALLER_MANAGEMENT_AUDIENCE": "00000000-0000-4000-8000-000000000005",
        "CALLER_TEXT_ENDPOINT": "https://model.openai.azure.com",
        "CALLER_TEXT_DEPLOYMENT": "gpt-text",
        "CALLER_LIVE_ENDPOINT": "https://model.services.ai.azure.com",
        "CALLER_LIVE_REGION": "swedencentral",
        "CALLER_LIVE_VOICE": "alloy",
        "CALLER_APPROVED_HOSTS": f"{MANAGEMENT},model.openai.azure.com,model.services.ai.azure.com",
        "CALLER_MAX_INPUT_TOKENS": "4000",
        "CALLER_MAX_OUTPUT_TOKENS": "1000",
        "CALLER_AVAILABLE_LIVE_SESSIONS": "20",
        "CALLER_MANAGER_ISSUER": f"https://{MANAGEMENT}",
        "CALLER_MANAGER_AUDIENCE": "https://runtime.example.azurecontainerapps.io",
        "CALLER_PERMIT_VERIFICATION_KEYS": json.dumps({"key-1": PUBLIC_PEM}),
        "CALLER_MAX_CLOCK_SKEW_SECONDS": "1",
        "CALLER_TENANT_ID": "00000000-0000-4000-8000-000000000002",
        "CALLER_WORKLOAD_AUDIENCE": "00000000-0000-4000-8000-000000000008",
        "CALLER_WORKLOAD_ROLE": "Management.Caller",
        "CALLER_WORKLOAD_PRINCIPAL_ID": "00000000-0000-4000-8000-000000000031",
    }


def load(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> Settings:
    for name in environment():
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return Settings()


class Credential:
    def __init__(self, **keywords: Any) -> None:
        self.fail = False

    async def get_token(self, *scopes: str, **keywords: Any) -> Any:
        if self.fail:
            raise RuntimeError("synthetic-private-detail")
        return SimpleNamespace(token="synthetic-token", expires_on=time.time() + 900)

    async def close(self) -> None:
        return None


class FailingContainer:
    async def read_item(self, *arguments: Any, **keywords: Any) -> Any:
        raise RuntimeError("synthetic-private-store-detail")

    create_item = replace_item = read_item


class Cosmos:
    def __init__(self, endpoint: str, **keywords: Any) -> None:
        pass

    def get_database_client(self, database: str) -> "Cosmos":
        return self

    def get_container_client(self, name: str) -> FailingContainer:
        return FailingContainer()

    async def close(self) -> None:
        return None


def test_a_complete_settings_set_builds_the_reviewed_runtime(monkeypatch) -> None:
    settings = load(monkeypatch, environment())
    composition = build_live_runtime(settings, credential_factory=Credential, cosmos_factory=Cosmos)
    runtime = composition.runtime
    assert isinstance(runtime, ConversationRuntime)
    options = runtime.options
    assert isinstance(options, RuntimeOptions)
    assert options.review_reference == "review-2026-001"
    assert options.region_reviewed is options.lifecycle_reviewed is True
    assert options.retention_reviewed is options.identity_reviewed is True
    assert options.private_store_reviewed is True
    assert options.approved_hosts == {
        MANAGEMENT,
        "model.openai.azure.com",
        "model.services.ai.azure.com",
    }
    assert options.text_deployment == "gpt-text" and options.available_live_sessions == 20
    assert options.max_clock_skew_seconds == 1 and options.text_history_ttl_seconds == 86400
    assert runtime.owner_id == settings.instance_id
    assert set(runtime._permits._keys) == {"key-1"}
    assert callable(runtime._workload_identity.require_runtime)
    assert asyncio.run(composition.dependencies.ready()) is True


def test_an_empty_review_reference_stops_the_start_with_review_missing(monkeypatch) -> None:
    settings = load(monkeypatch, {**environment(), "CALLER_REVIEW_REFERENCE": " "})
    with pytest.raises(StartupError) as raised:
        build_live_runtime(settings, credential_factory=Credential, cosmos_factory=Cosmos)
    assert raised.value.code == "review_missing"


def test_the_review_flags_are_never_set_without_a_reference(monkeypatch) -> None:
    live = load(monkeypatch, environment()).live()
    with pytest.raises(StartupError) as raised:
        runtime_options(type(live)(**{**live.__dict__, "review_reference": " "}))
    assert raised.value.code == "review_missing"


@pytest.mark.parametrize("name", [name for name in environment() if name != "CALLER_MODE"])
def test_each_missing_group_stops_the_start_with_the_matching_reason(monkeypatch, name) -> None:
    values = environment()
    del values[name]
    settings = load(monkeypatch, values)
    expected = "review_missing" if name == "CALLER_REVIEW_REFERENCE" else "settings_missing"
    with pytest.raises(StartupError) as raised:
        build_live_runtime(settings, credential_factory=Credential, cosmos_factory=Cosmos)
    assert raised.value.code == expected


def test_a_data_store_that_cannot_be_read_keeps_the_runtime_from_starting(monkeypatch) -> None:
    composition = build_live_runtime(
        load(monkeypatch, environment()), credential_factory=Credential, cosmos_factory=Cosmos
    )

    async def start() -> None:
        async with composition.runtime.running(Telemetry("caller-runtime", "live")):
            pytest.fail("the runtime must not start without its store")

    with pytest.raises(AdmissionError, match="test_runtime_unavailable") as raised:
        asyncio.run(start())
    assert "synthetic-private-store-detail" not in str(raised.value)


def test_readiness_reports_not_ready_when_the_identity_is_unavailable(monkeypatch) -> None:
    settings = load(monkeypatch, environment())
    composition = build_live_runtime(settings, credential_factory=Credential, cosmos_factory=Cosmos)
    composition.dependencies._credential.fail = True
    with TestClient(create_app(settings, dependencies=composition.dependencies)) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["reason_codes"] == ["dependency_unavailable"]
    assert "synthetic-private-detail" not in response.text
