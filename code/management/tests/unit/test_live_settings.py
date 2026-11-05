"""Management live settings: every management setting of the contract, fixed reason codes."""

import pytest

from management_app.settings import REASON_CODES, LiveSettings, Settings, StartupError

SENTINEL = "synthetic-value-that-must-never-appear"
PRIVATE_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"  # pragma: allowlist secret
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgsynthetic\n"
    "-----END PRIVATE KEY-----\n"
)
FINGERPRINT = "a" * 64
CLIENT = "00000000-0000-4000-8000-000000000005"


def complete() -> dict[str, str]:
    return {
        "CALLER_MODE": "live",
        "AZURE_CLIENT_ID": "00000000-0000-4000-8000-000000000030",
        "CALLER_REVIEW_REFERENCE": "review-2026-001",
        "CALLER_REVIEWED_FINGERPRINT": FINGERPRINT,
        "CALLER_COSMOS_ENDPOINT": "https://cosmos-int.documents.azure.com:443/",
        "CALLER_COSMOS_DATABASE": "caller",
        "CALLER_AUDIT_RETENTION_SECONDS": "7776000",
        "CALLER_TENANT_ID": "00000000-0000-4000-8000-000000000002",
        "CALLER_ENTRA_CLIENT_ID": CLIENT,
        "CALLER_ENTRA_CLIENT_SECRET": "synthetic-sign-in-credential",  # pragma: allowlist secret
        "CALLER_API_AUDIENCE": CLIENT,
        "CALLER_MANAGER_ROLE": "Caller.Editor",
        "CALLER_MANAGER_SCOPE": f"api://{CLIENT}/.default",
        "CALLER_TEST_ROLE": "Caller.Tester",
        "CALLER_RUNTIME_ROLE": "Caller.Runtime",
        "CALLER_MANAGEMENT_ORIGIN": "https://management.example.azurecontainerapps.io",
        "CALLER_RUNTIME_ENDPOINT": "https://runtime.example.azurecontainerapps.io",
        "CALLER_RUNTIME_AUDIENCE": "https://runtime.example.azurecontainerapps.io",
        "CALLER_PERMIT_ISSUER": "https://management.example.azurecontainerapps.io",
        "CALLER_PERMIT_AUDIENCE": "https://runtime.example.azurecontainerapps.io",
        "CALLER_SIGNING_KEY_ID": "key-1",
        "CALLER_SIGNING_KEY_PEM": PRIVATE_PEM,
        "CALLER_PUBLIC_ORIGIN": "https://runtime.example.azurecontainerapps.io",
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
    assert live.manager_role == "Caller.Editor" and live.audit_retention_seconds == 7776000
    assert live.signing_key_id == "key-1" and live.entra_client_id == CLIENT
    with pytest.raises(AttributeError):
        live.cosmos_database = "other"  # type: ignore[misc]


def test_secrets_never_enter_repr_or_the_fingerprint_input(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch, complete())
    live = settings.live()
    for text in (repr(settings), repr(live), str(settings.entra_client_secret)):
        assert "synthetic-sign-in-credential" not in text and "synthetic\n" not in text
    assert "entra_client_secret" not in live.non_secret()
    assert "signing_key_pem" not in live.non_secret()


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


@pytest.mark.parametrize(
    "name,value",
    [
        ("AZURE_CLIENT_ID", "x"),
        ("CALLER_REVIEWED_FINGERPRINT", "ABC"),
        ("CALLER_COSMOS_ENDPOINT", "http://cosmos"),
        ("CALLER_COSMOS_DATABASE", "bad name"),
        ("CALLER_AUDIT_RETENTION_SECONDS", "0"),
        ("CALLER_TENANT_ID", "tenant"),
        ("CALLER_ENTRA_CLIENT_ID", "client"),
        ("CALLER_API_AUDIENCE", "api"),
        ("CALLER_MANAGER_ROLE", "bad role"),
        ("CALLER_MANAGER_SCOPE", "api://x/.default"),
        ("CALLER_TEST_ROLE", ""),
        ("CALLER_RUNTIME_ROLE", "bad role!"),
        ("CALLER_MANAGEMENT_ORIGIN", "management.example"),
        ("CALLER_RUNTIME_ENDPOINT", "https://runtime/x"),
        ("CALLER_RUNTIME_AUDIENCE", "runtime"),
        ("CALLER_PERMIT_ISSUER", "http://issuer"),
        ("CALLER_PERMIT_AUDIENCE", "audience"),
        ("CALLER_SIGNING_KEY_ID", "bad key id"),
    ],
)
def test_each_invalid_setting_stops_the_start_without_echoing_the_value(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    values = {**complete(), name: value}
    expected = "settings_missing" if value == "" else "settings_invalid"
    with pytest.raises(StartupError) as raised:
        load(monkeypatch, values).validate_startup()
    assert raised.value.code == expected
    assert value not in str(raised.value) or value == ""


def test_an_unusable_signing_key_is_key_invalid_and_never_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {**complete(), "CALLER_SIGNING_KEY_PEM": SENTINEL}
    with pytest.raises(StartupError) as raised:
        load(monkeypatch, values).validate_startup()
    assert raised.value.code == "key_invalid"
    assert SENTINEL not in str(raised.value) and SENTINEL not in repr(raised.value)


def test_a_reviewed_fingerprint_that_differs_stops_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load(monkeypatch, complete())
    settings.require_fingerprint(FINGERPRINT)
    with pytest.raises(StartupError) as raised:
        settings.require_fingerprint("b" * 64)
    assert raised.value.code == "fingerprint_mismatch"


def test_an_empty_review_reference_is_review_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {**complete(), "CALLER_REVIEW_REFERENCE": " "}
    with pytest.raises(StartupError) as raised:
        load(monkeypatch, values).validate_startup()
    assert raised.value.code == "review_missing"


def test_the_reason_codes_are_the_fixed_contract_set() -> None:
    assert set(REASON_CODES) == {
        "settings_missing",
        "settings_invalid",
        "review_missing",
        "fingerprint_mismatch",
        "identity_unavailable",
        "store_unavailable",
        "key_invalid",
    }
