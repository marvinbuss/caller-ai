"""Management imports and local startup must not discover Azure credentials."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from management_app.settings import Settings


def test_local_defaults_are_frozen() -> None:
    settings = Settings()
    assert settings.mode == "local"
    assert settings.trace_destination == "memory"
    assert settings.trace_include_content is False
    with pytest.raises(ValidationError):
        settings.mode = "live"


@pytest.mark.parametrize(
    "values",
    [
        {"mode": "invalid"},
        {"trace_include_content": True},
        {"trace_destination": "https://unapproved.example"},
    ],
)
def test_invalid_mode_and_unsafe_telemetry_fail(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)


def test_local_startup_does_not_construct_live_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from management_app.management_app import create_app

    def forbidden(*arguments: object, **keywords: object) -> None:
        raise AssertionError("Live dependency constructed during local startup")

    monkeypatch.setattr("azure.identity.aio.DefaultAzureCredential", forbidden)
    monkeypatch.setattr("azure.cosmos.aio.CosmosClient", forbidden)
    with TestClient(create_app(Settings())()) as client:
        assert client.get("/readyz").status_code == 200


def test_live_startup_has_no_local_fallback() -> None:
    from management_app.management_app import create_app

    with pytest.raises(RuntimeError, match="live_mode_unavailable"):
        with TestClient(create_app(Settings(mode="live"))()):
            pytest.fail("Live startup silently fell back to local mode")


def test_external_connections_are_blocked() -> None:
    import socket

    from pytest_socket import SocketConnectBlockedError

    with socket.socket() as connection:
        with pytest.raises(SocketConnectBlockedError):
            connection.connect(("203.0.113.10", 443))
