"""The container entrypoint: local mode stays health-only, live mode refuses with one code."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from caller_runtime.entrypoint import create_app_from_environment
from caller_runtime.settings import StartupError

LIVE_NAMES = (
    "CALLER_REVIEW_REFERENCE",
    "CALLER_COSMOS_ENDPOINT",
    "CALLER_MANAGEMENT_ENDPOINT",
    "CALLER_TEXT_ENDPOINT",
    "CALLER_PERMIT_VERIFICATION_KEYS",
    "CALLER_TENANT_ID",
)


def test_local_mode_keeps_the_health_only_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CALLER_MODE", raising=False)
    with TestClient(create_app_from_environment()) as client:
        assert client.get("/healthz").json()["mode"] == "local"
        assert client.get("/readyz").status_code == 200
        assert client.post("/v1/conversations/text").status_code == 404


def test_live_mode_without_settings_refuses_with_one_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLER_MODE", "live")
    for name in LIVE_NAMES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(StartupError) as raised:
        create_app_from_environment()
    assert raised.value.code == "settings_missing"


def test_the_image_starts_the_factory_entrypoint() -> None:
    dockerfile = (Path(__file__).parents[2] / "Dockerfile").read_text(encoding="utf-8")
    assert "caller_runtime.entrypoint:create_app_from_environment" in dockerfile
    assert '"--factory"' in dockerfile
