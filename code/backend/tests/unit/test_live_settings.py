"""Live settings: every runtime setting of the application-settings contract, fixed reason codes."""

import json

import pytest

from caller_runtime.settings import LiveSettings, Settings, StartupError

SENTINEL = "synthetic-value-that-must-never-appear"
KEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEsynthetic\n"  # pragma: allowlist secret
    "-----END PUBLIC KEY-----\n"
)


def complete() -> dict[str, str]:
    return {
        "CALLER_MODE": "live",
        "AZURE_CLIENT_ID": "00000000-0000-4000-8000-000000000020",
        "CALLER_REVIEW_REFERENCE": "review-2026-001",
        "CALLER_COSMOS_ENDPOINT": "https://cosmos-int.documents.azure.com:443/",
        "CALLER_COSMOS_DATABASE": "caller",
        "CALLER_TEXT_HISTORY_TTL_SECONDS": "86400",
        "CALLER_MANAGEMENT_ENDPOINT": "https://management.example.azurecontainerapps.io",
        "CALLER_MANAGEMENT_AUDIENCE": "00000000-0000-4000-8000-000000000005",
        "CALLER_TEXT_ENDPOINT": "https://model.openai.azure.com",
        "CALLER_TEXT_DEPLOYMENT": "gpt-text",
        "CALLER_LIVE_ENDPOINT": "https://model.services.ai.azure.com",
        "CALLER_LIVE_REGION": "swedencentral",
        "CALLER_LIVE_VOICE": "alloy",
        "CALLER_APPROVED_HOSTS": (
            "management.example.azurecontainerapps.io,model.openai.azure.com,"
            "model.services.ai.azure.com"
        ),
        "CALLER_MAX_INPUT_TOKENS": "4000",
        "CALLER_MAX_OUTPUT_TOKENS": "1000",
        "CALLER_AVAILABLE_LIVE_SESSIONS": "20",
        "CALLER_MANAGER_ISSUER": "https://management.example.azurecontainerapps.io",
        "CALLER_MANAGER_AUDIENCE": "https://runtime.example.azurecontainerapps.io",
        "CALLER_PERMIT_VERIFICATION_KEYS": json.dumps({"key-1": KEY_PEM}),
        "CALLER_MAX_CLOCK_SKEW_SECONDS": "1",
        "CALLER_TENANT_ID": "00000000-0000-4000-8000-000000000002",
        "CALLER_WORKLOAD_AUDIENCE": "00000000-0000-4000-8000-000000000008",
        "CALLER_WORKLOAD_ROLE": "Management.Caller",
        "CALLER_WORKLOAD_PRINCIPAL_ID": "00000000-0000-4000-8000-000000000031",
    }


TUNING = (
    "CALLER_TRANSCRIPTION_WAIT_SECONDS",
    "CALLER_REASONING_EFFORT",
    "CALLER_VOICE_DELEGATION_UPDATES",
)


def load(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> Settings:
    for name in [*complete(), *TUNING]:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return Settings()


def test_a_complete_set_gives_the_frozen_live_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    live = load(monkeypatch, complete()).live()
    assert isinstance(live, LiveSettings)
    assert live.cosmos_database == "caller" and live.text_history_ttl_seconds == 86400
    assert live.live_region == "swedencentral" and live.available_live_sessions == 20
    assert len(live.approved_hosts) == 3 and set(live.permit_verification_keys) == {"key-1"}
    assert live.max_clock_skew_seconds == 1
    with pytest.raises(AttributeError):
        live.cosmos_database = "other"  # type: ignore[misc]


def test_live_startup_accepts_a_complete_set(monkeypatch: pytest.MonkeyPatch) -> None:
    load(monkeypatch, complete()).validate_startup()


def test_a_deployment_name_may_contain_dots(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {**complete(), "CALLER_TEXT_DEPLOYMENT": "gpt-6.1-sol"}
    assert load(monkeypatch, values).live().text_deployment == "gpt-6.1-sol"


def test_local_mode_needs_no_other_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch, {})
    settings.validate_startup()
    assert settings.mode == "local"


@pytest.mark.parametrize("name", [name for name in complete() if name != "CALLER_MODE"])
def test_each_missing_setting_stops_the_start_with_a_fixed_reason(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    values = complete()
    del values[name]
    expected = "review_missing" if name == "CALLER_REVIEW_REFERENCE" else "settings_missing"
    with pytest.raises(StartupError) as raised:
        load(monkeypatch, values).validate_startup()
    assert raised.value.code == expected and str(raised.value) == expected


def test_an_empty_review_reference_is_review_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {**complete(), "CALLER_REVIEW_REFERENCE": "  "}
    with pytest.raises(StartupError) as raised:
        load(monkeypatch, values).validate_startup()
    assert raised.value.code == "review_missing"


@pytest.mark.parametrize(
    "name,value",
    [
        ("AZURE_CLIENT_ID", "not-a-guid"),
        ("CALLER_COSMOS_ENDPOINT", "http://cosmos.example"),
        ("CALLER_COSMOS_DATABASE", "bad name!"),
        ("CALLER_TEXT_HISTORY_TTL_SECONDS", "0"),
        ("CALLER_TEXT_HISTORY_TTL_SECONDS", "abc"),
        ("CALLER_MANAGEMENT_ENDPOINT", "https://management.example/path"),
        ("CALLER_MANAGEMENT_AUDIENCE", "x"),
        ("CALLER_TEXT_ENDPOINT", "ftp://x"),
        ("CALLER_TEXT_DEPLOYMENT", "bad deployment"),
        ("CALLER_LIVE_ENDPOINT", "https://user@host"),
        ("CALLER_LIVE_REGION", "westeurope"),
        ("CALLER_LIVE_VOICE", "bad voice"),
        ("CALLER_APPROVED_HOSTS", "a.example," * 9 + "b.example"),
        ("CALLER_APPROVED_HOSTS", "not a host"),
        ("CALLER_MAX_INPUT_TOKENS", "-1"),
        ("CALLER_MAX_OUTPUT_TOKENS", "0"),
        ("CALLER_AVAILABLE_LIVE_SESSIONS", "4"),
        ("CALLER_AVAILABLE_LIVE_SESSIONS", "10001"),
        ("CALLER_MANAGER_ISSUER", "management.example"),
        ("CALLER_MANAGER_AUDIENCE", "https://runtime.example/x"),
        ("CALLER_PERMIT_VERIFICATION_KEYS", "not json"),
        ("CALLER_PERMIT_VERIFICATION_KEYS", "{}"),
        ("CALLER_PERMIT_VERIFICATION_KEYS", json.dumps({"k": "not a pem"})),
        ("CALLER_PERMIT_VERIFICATION_KEYS", json.dumps({f"k{n}": KEY_PEM for n in range(5)})),
        ("CALLER_MAX_CLOCK_SKEW_SECONDS", "2"),
        ("CALLER_MAX_CLOCK_SKEW_SECONDS", "-1"),
        ("CALLER_TENANT_ID", "tenant"),
        ("CALLER_WORKLOAD_AUDIENCE", "https://runtime.example"),
        ("CALLER_WORKLOAD_ROLE", "bad role"),
        ("CALLER_WORKLOAD_PRINCIPAL_ID", "principal"),
        ("CALLER_MAX_INPUT_TOKENS", "100"),
        ("CALLER_MAX_OUTPUT_TOKENS", "5000"),
        ("CALLER_TEXT_HISTORY_TTL_SECONDS", "2592001"),
    ],
)
def test_each_invalid_setting_stops_the_start_without_echoing_the_value(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    values = {**complete(), name: value}
    with pytest.raises(StartupError) as raised:
        load(monkeypatch, values).validate_startup()
    assert raised.value.code == "settings_invalid"
    assert value not in str(raised.value) and value not in repr(raised.value)


def test_an_invalid_value_never_appears_in_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {**complete(), "CALLER_COSMOS_ENDPOINT": SENTINEL}
    with pytest.raises(StartupError) as raised:
        load(monkeypatch, values).validate_startup()
    assert SENTINEL not in str(raised.value) and SENTINEL not in repr(raised.value)


def test_the_reason_codes_are_the_fixed_contract_set() -> None:
    from caller_runtime.settings import REASON_CODES

    assert set(REASON_CODES) == {
        "settings_missing",
        "settings_invalid",
        "review_missing",
        "fingerprint_mismatch",
        "identity_unavailable",
        "store_unavailable",
        "key_invalid",
    }


def test_the_tuning_settings_are_optional_with_reviewed_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from caller_runtime.settings import (
        REASONING_EFFORT,
        TRANSCRIPTION_WAIT_SECONDS,
        VOICE_DELEGATION_UPDATES,
    )

    live = load(monkeypatch, complete()).live()
    assert live.transcription_wait_seconds == TRANSCRIPTION_WAIT_SECONDS == 0.5
    assert live.reasoning_effort == REASONING_EFFORT == "low"
    assert live.voice_delegation_updates is VOICE_DELEGATION_UPDATES


@pytest.mark.parametrize(
    "name,value,field,expected",
    [
        ("CALLER_TRANSCRIPTION_WAIT_SECONDS", "0", "transcription_wait_seconds", 0.0),
        ("CALLER_TRANSCRIPTION_WAIT_SECONDS", "2", "transcription_wait_seconds", 2.0),
        ("CALLER_TRANSCRIPTION_WAIT_SECONDS", "1.25", "transcription_wait_seconds", 1.25),
        ("CALLER_REASONING_EFFORT", "minimal", "reasoning_effort", "minimal"),
        ("CALLER_REASONING_EFFORT", "medium", "reasoning_effort", "medium"),
        ("CALLER_VOICE_DELEGATION_UPDATES", "true", "voice_delegation_updates", True),
        ("CALLER_VOICE_DELEGATION_UPDATES", "false", "voice_delegation_updates", False),
    ],
)
def test_set_tuning_values_are_used(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, field: str, expected: object
) -> None:
    assert getattr(load(monkeypatch, {**complete(), name: value}).live(), field) == expected


@pytest.mark.parametrize(
    "name,value",
    [
        ("CALLER_TRANSCRIPTION_WAIT_SECONDS", "2.5"),
        ("CALLER_TRANSCRIPTION_WAIT_SECONDS", "-1"),
        ("CALLER_TRANSCRIPTION_WAIT_SECONDS", "abc"),
        ("CALLER_TRANSCRIPTION_WAIT_SECONDS", "nan"),
        ("CALLER_REASONING_EFFORT", "high"),
        ("CALLER_REASONING_EFFORT", "none"),
        ("CALLER_VOICE_DELEGATION_UPDATES", "yes"),
        ("CALLER_VOICE_DELEGATION_UPDATES", "TRUE"),
    ],
)
def test_an_invalid_tuning_value_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    with pytest.raises(StartupError) as raised:
        load(monkeypatch, {**complete(), name: value}).validate_startup()
    assert raised.value.code == "settings_invalid" and value not in str(raised.value)
