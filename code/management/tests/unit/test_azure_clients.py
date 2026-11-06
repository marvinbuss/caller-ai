"""Azure clients for the live management app: managed identity and Cosmos, no shared key (S-10)."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import management_app.adapters.azure_clients as module
from management_app.adapters.azure_clients import COSMOS_SCOPE, AzureClients
from management_app.settings import Settings
from tests.unit.test_live_settings import complete


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


class FakeCosmos:
    def __init__(self, endpoint: str, **keywords: Any) -> None:
        self.endpoint, self.keywords, self.closed = endpoint, keywords, 0
        self.requested: list[tuple[str, str]] = []
        self._database = ""

    def get_database_client(self, database: str) -> "FakeCosmos":
        self._database = database
        return self

    def get_container_client(self, name: str) -> str:
        self.requested.append((self._database, name))
        return name

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> AzureClients:
    for name, value in complete().items():
        monkeypatch.setenv(name, value)
    return AzureClients(
        Settings().live(), credential_factory=FakeCredential, cosmos_factory=FakeCosmos
    )


def test_the_credential_is_the_managed_identity_named_by_azure_client_id(
    clients: AzureClients,
) -> None:
    assert clients.credential.keywords == {"client_id": "00000000-0000-4000-8000-000000000030"}
    assert asyncio.run(clients.credential.get_token("scope")).token == "synthetic-token"


def test_the_cosmos_client_uses_the_credential_and_never_a_key(clients: AzureClients) -> None:
    cosmos = clients._cosmos
    assert cosmos.endpoint == "https://cosmos-int.documents.azure.com:443/"
    assert set(cosmos.keywords) == {"credential"} and cosmos.keywords["credential"] is (
        clients.credential
    )
    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("connection_string", "account_key", "masterkey", "sas_token"):
        assert forbidden not in source


@pytest.mark.parametrize("name", ["configuration", "configuration-audit", "call-audit"])
def test_the_three_management_containers_are_served(clients: AzureClients, name: str) -> None:
    assert clients.container(name) == name
    assert clients._cosmos.requested == [("caller", name)]


@pytest.mark.parametrize("name", ["call-sessions", "text-conversations", "", "other"])
def test_no_other_container_is_served(clients: AzureClients, name: str) -> None:
    with pytest.raises(ValueError, match="container_not_allowed"):
        clients.container(name)


def test_readiness_asks_for_a_store_token_and_hides_a_failure(clients: AzureClients) -> None:
    assert asyncio.run(clients.ready()) is True
    assert clients.credential.scopes == (COSMOS_SCOPE,)
    clients.credential.fail = True
    assert asyncio.run(clients.ready()) is False


def test_close_releases_both_clients_once(clients: AzureClients) -> None:
    asyncio.run(clients.close())
    asyncio.run(clients.close())
    assert clients._cosmos.closed == 1 and clients.credential.closed == 1
