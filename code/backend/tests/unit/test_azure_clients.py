"""Azure clients for the live runtime: managed identity and Cosmos, no shared key (S-10)."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import caller_runtime.adapters.azure_clients as module
from caller_runtime.adapters.azure_clients import COSMOS_SCOPE, AzureClients
from caller_runtime.settings import Settings

from .test_live_settings import complete


class FakeCredential:
    def __init__(self, **keywords: Any) -> None:
        self.keywords, self.closed, self.fail = keywords, 0, False
        self.scopes: tuple[str, ...] = ()

    async def get_token(self, *scopes: str, **keywords: Any) -> Any:
        if self.fail:
            raise RuntimeError("synthetic-private-detail")
        self.scopes = scopes
        return SimpleNamespace(token="synthetic-token", expires_on=time.time() + 900)

    async def close(self) -> None:
        self.closed += 1


class FakeContainer:
    def __init__(self, name: str) -> None:
        self.name = name

    async def create_item(self, body: dict[str, Any], **keywords: Any) -> dict[str, Any]:
        return body

    async def read_item(self, item: str, partition_key: str, **keywords: Any) -> dict[str, Any]:
        return {"id": item}

    async def replace_item(
        self, item: str, body: dict[str, Any], **keywords: Any
    ) -> dict[str, Any]:
        return body


class FakeCosmos:
    def __init__(self, endpoint: str, **keywords: Any) -> None:
        self.endpoint, self.keywords, self.closed = endpoint, keywords, 0
        self.requested: list[tuple[str, str]] = []
        self._database = ""

    def get_database_client(self, database: str) -> "FakeCosmos":
        self._database = database
        return self

    def get_container_client(self, name: str) -> FakeContainer:
        self.requested.append((self._database, name))
        return FakeContainer(name)

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> AzureClients:
    for name, value in complete().items():
        monkeypatch.setenv(name, value)
    live = Settings().live()
    return AzureClients(live, credential_factory=FakeCredential, cosmos_factory=FakeCosmos)


def test_the_credential_is_the_managed_identity_named_by_azure_client_id(
    clients: AzureClients,
) -> None:
    credential = asyncio.run(clients.credential())
    assert credential.keywords == {"client_id": "00000000-0000-4000-8000-000000000020"}  # type: ignore[attr-defined]
    assert asyncio.run(credential.get_token("scope")).token == "synthetic-token"


def test_cosmos_uses_the_credential_and_never_a_key_or_connection_string(
    clients: AzureClients,
) -> None:
    cosmos = clients._cosmos
    assert cosmos.endpoint == "https://cosmos-int.documents.azure.com:443/"
    assert set(cosmos.keywords) == {"credential"}
    assert cosmos.keywords["credential"] is clients._credential


def test_containers_come_from_the_configured_database_and_only_the_runtime_names(
    clients: AzureClients,
) -> None:
    for name in ("call-sessions", "text-conversations"):
        assert asyncio.run(clients.container(name)).name == name  # type: ignore[attr-defined]
    assert clients._cosmos.requested == [
        ("caller", "call-sessions"),
        ("caller", "text-conversations"),
    ]
    for forbidden in ("configuration", "configuration-audit", "call-audit", "other"):
        with pytest.raises(ValueError, match="container_not_allowed"):
            asyncio.run(clients.container(forbidden))


def test_readiness_answers_within_its_limit_and_hides_failures(clients: AzureClients) -> None:
    started = time.perf_counter()
    assert asyncio.run(clients.ready()) is True
    assert time.perf_counter() - started < 0.25
    assert clients._credential.scopes == (COSMOS_SCOPE,)
    clients._credential.fail = True
    assert asyncio.run(clients.ready()) is False


def test_close_releases_both_clients_once(clients: AzureClients) -> None:
    asyncio.run(clients.close())
    asyncio.run(clients.close())
    assert clients._cosmos.closed == 1 and clients._credential.closed == 1


def test_the_real_clients_construct_with_their_async_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in complete().items():
        monkeypatch.setenv(name, value)
    live = Settings().live()

    async def construct() -> None:
        clients = AzureClients(live)
        await clients.container("call-sessions")
        await clients.close()

    asyncio.run(construct())


def test_no_shared_key_or_connection_string_exists_in_the_module() -> None:
    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in (
        "account_key",
        "masterkey",
        "connection_string",
        "sas_token",
        "from_connection",
    ):
        assert forbidden not in source
