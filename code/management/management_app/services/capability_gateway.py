"""Sole execution admission boundary; every attempt is ordered against revocation."""

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any, Protocol

from management_app.metrics import ManagementMetrics
from management_app.models.permissions import (
    CapabilityAdmission,
    CapabilityAttempt,
    PermissionAuthority,
    PermissionConflict,
)
from management_app.observability import Telemetry
from management_app.services.capability_policy import (
    AuthorizedCapability,
    CapabilityInputRejected,
    CapabilityPolicy,
)

ERROR_CODES = {
    403: "capability_not_allowed",
    422: "invalid_capability_input",
    503: "capability_unavailable",
    504: "capability_deadline_exceeded",
}


class GatewayError(RuntimeError):
    def __init__(self, status: int, *, revision_id: str | None = None) -> None:
        self.status = status
        self.revision_id = revision_id
        self.code = ERROR_CODES[status]
        super().__init__(self.code)


class RetryableCapabilityError(RuntimeError):
    """A public read may be retried only with a fresh admission and original deadline."""


class AuthorityRepository(Protocol):
    async def read_authority(self) -> dict[str, Any]: ...

    async def read_active(self) -> tuple[dict[str, Any], str] | None: ...

    async def read_revision(self, revision_id: str) -> dict[str, Any] | None: ...

    async def admit_capability(
        self, request: CapabilityAttempt, *, if_match: str, actor_id: str
    ) -> CapabilityAdmission: ...


class CapabilityTransport(Protocol):
    async def execute(
        self, selected: AuthorizedCapability, *, request_id: str, deadline: float
    ) -> dict[str, Any]: ...


class CapabilityGateway:
    def __init__(
        self,
        *,
        repository: AuthorityRepository,
        policy: CapabilityPolicy,
        transport: CapabilityTransport,
        telemetry: Telemetry,
        metrics: ManagementMetrics | None = None,
    ) -> None:
        self._repository, self._policy = repository, policy
        self._transport, self._telemetry = transport, telemetry
        self._metrics = metrics

    async def execute(self, request: CapabilityAttempt, *, actor_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        budget = max(0, min(10, (request.deadline_at - datetime.now(UTC)).total_seconds()))
        try:
            return await self._execute(request, actor_id=actor_id)
        finally:
            if self._metrics is not None:
                self._metrics.saturation(
                    "dependency_budget",
                    min(1, (time.perf_counter() - started) / budget) if budget else 1,
                )

    async def _execute(self, request: CapabilityAttempt, *, actor_id: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        remaining = min(10.0, (request.deadline_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            raise GatewayError(504)
        deadline = loop.time() + remaining
        dependency = "configuration_store"
        verified_revision: str | None = None
        with self._telemetry.span("capability.execute"):
            try:
                async with asyncio.timeout_at(deadline):
                    for _attempt in range(3):
                        dependency = "configuration_store"
                        document = await self._repository.read_authority()
                        authority = PermissionAuthority.model_validate_json(json.dumps(document))
                        active = await self._repository.read_active()
                        pinned = await self._repository.read_revision(request.pinned_revision_id)
                        if active is None or pinned is None:
                            raise GatewayError(403)
                        if pinned.get("id") == request.pinned_revision_id:
                            verified_revision = request.pinned_revision_id
                        selected = self._policy.authorize(
                            request, pinned=pinned, current=active[0], authority=authority
                        )
                        if loop.time() >= deadline:
                            raise GatewayError(504)
                        try:
                            await self._repository.admit_capability(
                                request, if_match=document["_etag"], actor_id=actor_id
                            )
                        except PermissionConflict:
                            continue
                        if loop.time() >= deadline:
                            raise GatewayError(504)
                        try:
                            dependency = (
                                "mcp" if selected.connection_id is not None else "public_tool"
                            )
                            result = await self._transport.execute(
                                selected, request_id=request.request_id, deadline=deadline
                            )
                        except RetryableCapabilityError:
                            continue
                        return {
                            "requestId": request.request_id,
                            "sessionId": request.session_id,
                            "agentId": request.agent_id,
                            "agentEpoch": request.agent_epoch,
                            "result": self._policy.filter_result(selected, result),
                        }
                raise GatewayError(503)
            except GatewayError as error:
                if self._metrics is not None and error.status in {503, 504}:
                    self._metrics.dependency_failure(
                        dependency, "deadline" if error.status == 504 else "unavailable"
                    )
                raise
            except CapabilityInputRejected:
                raise GatewayError(422, revision_id=verified_revision) from None
            except PermissionError:
                raise GatewayError(403, revision_id=verified_revision) from None
            except TimeoutError:
                if self._metrics is not None:
                    self._metrics.dependency_failure(dependency, "deadline")
                raise GatewayError(504) from None
            except Exception:
                if self._metrics is not None:
                    self._metrics.dependency_failure(dependency, "unavailable")
                raise GatewayError(503) from None
