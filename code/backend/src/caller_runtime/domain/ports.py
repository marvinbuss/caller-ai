"""Runtime dependency boundary; implementations must not block the event loop."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from azure.core import MatchConditions
from azure.core.credentials import AccessToken


class CredentialPort(Protocol):
    async def get_token(self, *scopes: str, **keywords: Any) -> AccessToken: ...


class ContainerPort(Protocol):
    """The Cosmos container operations the runtime needs; callers build the real client."""

    async def create_item(self, body: dict[str, Any], **keywords: Any) -> Mapping[str, Any]: ...

    async def read_item(
        self, item: str, partition_key: str, **keywords: Any
    ) -> Mapping[str, Any]: ...

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        *,
        etag: str,
        match_condition: MatchConditions,
        **keywords: Any,
    ) -> Mapping[str, Any]: ...


class ReadinessPort(Protocol):
    async def ready(self) -> bool:
        """Return local dependency readiness without exposing dependency details."""
        ...


@dataclass(frozen=True, slots=True)
class LocalDependencies:
    """Deterministic foundation stand-in, not production storage or an SDK client."""

    available: bool = True

    async def ready(self) -> bool:
        return self.available
