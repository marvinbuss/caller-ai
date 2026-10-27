"""Management-owned dependency boundary, independent of runtime private data."""

from dataclasses import dataclass
from typing import Protocol


class ReadinessPort(Protocol):
    async def ready(self) -> bool:
        """Return local dependency readiness without exposing dependency details."""
        ...


@dataclass(frozen=True, slots=True)
class LocalDependencies:
    """Deterministic foundation stand-in, not a configuration persistence store."""

    available: bool = True

    async def ready(self) -> bool:
        return self.available
