"""Spike S-11: a grant is issued only for the reviewed fingerprint (research R-11)."""

import asyncio

import pytest

from management_app.composition import CompositionGrant
from management_app.live import (
    REVIEW_EXCLUDED,
    ReviewedFingerprintAuthority,
    management_request,
    reviewed_fingerprint,
)
from management_app.settings import Settings, StartupError
from tests.unit.test_live_settings import complete

SECRETS = ("CALLER_ENTRA_CLIENT_SECRET", "CALLER_SIGNING_KEY_PEM")


def live_for(monkeypatch: pytest.MonkeyPatch, changes: dict[str, str] | None = None):
    values = {**complete(), **(changes or {})}
    for name in complete():
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return Settings().live(reviewable=True)


def test_the_authority_grants_only_when_the_fingerprint_equals_the_reviewed_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = live_for(monkeypatch)
    reviewed = type(live)(**{**live.__dict__, "reviewed_fingerprint": reviewed_fingerprint(live)})
    request = management_request(reviewed)
    grant = asyncio.run(
        ReviewedFingerprintAuthority(reviewed, request).authorize(
            request, fingerprint="f" * 64, deadline=0
        )
    )
    assert isinstance(grant, CompositionGrant) and grant.fingerprint == "f" * 64
    wrong = type(live)(**{**live.__dict__, "reviewed_fingerprint": "0" * 64})
    with pytest.raises(PermissionError, match="fingerprint_mismatch"):
        asyncio.run(
            ReviewedFingerprintAuthority(wrong, request).authorize(
                request, fingerprint="f" * 64, deadline=0
            )
        )


def test_the_authority_refuses_a_request_it_did_not_review(monkeypatch: pytest.MonkeyPatch) -> None:
    live = live_for(monkeypatch)
    reviewed = type(live)(**{**live.__dict__, "reviewed_fingerprint": reviewed_fingerprint(live)})
    other = management_request(
        type(live)(
            **{**reviewed.__dict__, "runtime_client_id": "00000000-0000-4000-8000-000000000044"}
        )
    )
    with pytest.raises(PermissionError):
        asyncio.run(
            ReviewedFingerprintAuthority(reviewed, management_request(reviewed)).authorize(
                other, fingerprint="f" * 64, deadline=0
            )
        )


@pytest.mark.parametrize(
    "name,value",
    [
        ("CALLER_TENANT_ID", "00000000-0000-4000-8000-000000000099"),
        ("CALLER_ENTRA_CLIENT_ID", "00000000-0000-4000-8000-000000000098"),
        ("CALLER_API_AUDIENCE", "00000000-0000-4000-8000-000000000097"),
        ("CALLER_MANAGER_ROLE", "Caller.Other"),
        ("CALLER_TEST_ROLE", "Caller.OtherTester"),
        ("CALLER_RUNTIME_ROLE", "Caller.OtherRuntime"),
        ("CALLER_COSMOS_ENDPOINT", "https://other.documents.azure.com:443/"),
        ("CALLER_COSMOS_DATABASE", "other"),
        ("CALLER_AUDIT_RETENTION_SECONDS", "7776001"),
        ("CALLER_SIGNING_KEY_ID", "key-2"),
        ("CALLER_RUNTIME_CLIENT_ID", "00000000-0000-4000-8000-000000000044"),
    ],
)
def test_every_reviewable_value_changes_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    before = reviewed_fingerprint(live_for(monkeypatch))
    changes = {name: value}
    if name == "CALLER_API_AUDIENCE":
        changes["CALLER_MANAGER_SCOPE"] = f"api://{value}/.default"
    assert reviewed_fingerprint(live_for(monkeypatch, changes)) != before


@pytest.mark.parametrize(
    "name,value",
    [
        ("CALLER_MANAGEMENT_ORIGIN", "https://other-management.example.net"),
        ("CALLER_PUBLIC_ORIGIN", "https://other-management.example.net"),
        ("CALLER_RUNTIME_ENDPOINT", "https://other-runtime.example.net"),
        ("CALLER_RUNTIME_AUDIENCE", "00000000-0000-4000-8000-000000000066"),
        ("CALLER_PERMIT_ISSUER", "https://other-management.example.net"),
        ("CALLER_PERMIT_AUDIENCE", "https://other-runtime.example.net"),
        ("AZURE_CLIENT_ID", "00000000-0000-4000-8000-000000000077"),
        ("CALLER_REVIEW_REFERENCE", "review-2026-002"),
        ("CALLER_REVIEWED_FINGERPRINT", "c" * 64),
    ],
)
def test_values_that_exist_only_after_the_apply_stay_out_of_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    assert name in {f"CALLER_{field.upper()}" for field in REVIEW_EXCLUDED} | {"AZURE_CLIENT_ID"}
    before = reviewed_fingerprint(live_for(monkeypatch))
    changes = {name: value}
    if name in {"CALLER_MANAGEMENT_ORIGIN", "CALLER_PUBLIC_ORIGIN"}:
        changes = {"CALLER_MANAGEMENT_ORIGIN": value, "CALLER_PUBLIC_ORIGIN": value}
    assert reviewed_fingerprint(live_for(monkeypatch, changes)) == before


def test_secrets_never_enter_the_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    before = reviewed_fingerprint(live_for(monkeypatch))
    for name in SECRETS:
        assert reviewed_fingerprint(live_for(monkeypatch, {name: "changed-secret-value"})) == before


def test_a_scope_for_another_api_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    live = live_for(
        monkeypatch,
        {"CALLER_MANAGER_SCOPE": "api://00000000-0000-4000-8000-000000000099/.default"},
    )
    with pytest.raises(StartupError) as raised:
        management_request(live)
    assert raised.value.code == "settings_invalid"
