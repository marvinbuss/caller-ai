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
    }


def load(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> Settings:
    for name in complete():
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
