"""Runtime mode, privacy policy and credential-free startup requirements."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from caller_runtime.settings import Settings


def test_local_defaults_and_startup_only_settings() -> None:
    settings = Settings()
    assert settings.mode == "local"
    assert settings.agents_sdk_tracing_enabled is False
    assert settings.realtime_tracing_enabled is False
    assert settings.trace_destination == "memory"
    with pytest.raises(ValidationError):
        settings.mode = "live"


@pytest.mark.parametrize(
    "values",
    [
        {"mode": "invalid"},
        {"trace_include_content": True},
        {"agents_sdk_tracing_enabled": True, "trace_destination": "https://unapproved.example"},
        {"realtime_tracing_enabled": True},
    ],
)
def test_invalid_or_unsafe_configuration_fails_closed(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)


def test_explicit_sdk_memory_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLER_AGENTS_SDK_TRACING_ENABLED", "true")
    settings = Settings()
    assert settings.agents_sdk_tracing_enabled is True
    assert settings.trace_destination == "memory"
    assert settings.trace_include_content is False


def test_local_startup_does_not_construct_live_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from caller_runtime.main import create_app

    def forbidden(*arguments: object, **keywords: object) -> None:
        raise AssertionError("Live dependency constructed during local startup")

    monkeypatch.setattr("azure.identity.aio.DefaultAzureCredential", forbidden)
    monkeypatch.setattr("azure.cosmos.aio.CosmosClient", forbidden)
    monkeypatch.setattr("openai.AsyncOpenAI", forbidden)
    with TestClient(create_app(Settings())) as client:
        assert client.get("/readyz").status_code == 200


def test_live_startup_has_no_local_fallback() -> None:
    from caller_runtime.main import create_app

    with pytest.raises(RuntimeError, match="settings_missing"):
        with TestClient(create_app(Settings(mode="live"))):
            pytest.fail("Live startup silently fell back to local mode")


def test_external_connections_are_blocked() -> None:
    import socket

    from pytest_socket import SocketConnectBlockedError

    with socket.socket() as connection:
        with pytest.raises(SocketConnectBlockedError):
            connection.connect(("203.0.113.10", 443))
