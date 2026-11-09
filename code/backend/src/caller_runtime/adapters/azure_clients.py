"""Azure client factories for the live runtime: managed identity and Cosmos DB, never a key.

The credential is the user-assigned managed identity named by `AZURE_CLIENT_ID`. The Cosmos client
authenticates with that credential only; no account key, connection string or SAS token exists in
this module. Construction performs no network call: the clients connect on first use, so a start
with an unreachable store fails in the composition's own checks with a fixed reason code.
"""

import asyncio
from collections.abc import Callable
from typing import Any, Final

from azure.cosmos.aio import CosmosClient
from azure.identity.aio import ManagedIdentityCredential

from caller_runtime.domain.ports import ContainerPort, CredentialPort
from caller_runtime.settings import LiveSettings

COSMOS_SCOPE: Final = "https://cosmos.azure.com/.default"
ALLOWED_CONTAINERS: Final = frozenset({"call-sessions", "text-conversations"})


class AzureClients:
    """Owns the credential and the Cosmos client; also answers the readiness probe."""

    def __init__(
        self,
        settings: LiveSettings,
        *,
        credential_factory: Callable[..., Any] = ManagedIdentityCredential,
        cosmos_factory: Callable[..., Any] = CosmosClient,
    ) -> None:
        self._settings = settings
        self._credential: Any = credential_factory(client_id=settings.azure_client_id)
        self._cosmos: Any = cosmos_factory(settings.cosmos_endpoint, credential=self._credential)
        self._closed = False

    async def credential(self) -> CredentialPort:
        credential: CredentialPort = self._credential
        return credential

    async def container(self, name: str) -> ContainerPort:
        if name not in ALLOWED_CONTAINERS:
            raise ValueError("container_not_allowed")
        database = self._cosmos.get_database_client(self._settings.cosmos_database)
        container: ContainerPort = database.get_container_client(name)
        return container

    async def ready(self) -> bool:
        """Report whether the identity can produce a store token; no detail leaves here."""
        try:
            token = await self._credential.get_token(COSMOS_SCOPE)
        except Exception:
            return False
        return bool(getattr(token, "token", None))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for client in (self._cosmos, self._credential):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    async with asyncio.timeout(10):
                        await close()
                except Exception:
                    continue
