"""Runtime dependency boundary; implementations must not block the event loop."""

from dataclasses import dataclass
from typing import Protocol


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
