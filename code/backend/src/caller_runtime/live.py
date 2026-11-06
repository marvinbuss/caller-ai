"""Builds the live runtime from validated settings; nothing here discovers a resource or a key.

`build_live_runtime` turns the settings into the reviewed `RuntimeOptions`, the workload identity
verifier, the permit verification keys, the Azure clients and the `ConversationRuntime`. The five
`*_reviewed` flags are set only because the settings carry a non-empty review reference; an empty
one stops the start with `review_missing` before anything is built (FR-047).
"""

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from caller_runtime.adapters.azure_clients import AzureClients
from caller_runtime.adapters.workload_identity import EntraSigningKeys, EntraWorkloadIdentity
from caller_runtime.composition import ConversationRuntime, RuntimeOptions
from caller_runtime.observability import Telemetry
from caller_runtime.settings import LiveSettings, Settings, StartupError

LIVE_MODEL_VERSION = "2026-09-10"
TEXT_FEATURES = frozenset({"streaming", "function_tools", "storage_disabled", "structured_output"})


def runtime_options(live: LiveSettings) -> RuntimeOptions:
    """Return the review record; raises `StartupError` when the settings fail the review."""
    if not live.review_reference.strip():
        raise StartupError("review_missing")
    try:
        return RuntimeOptions(
            review_reference=live.review_reference,
            management_endpoint=live.management_endpoint,
            management_audience=live.management_audience,
            text_endpoint=live.text_endpoint,
            text_deployment=live.text_deployment,
            live_endpoint=live.live_endpoint,
            approved_hosts=frozenset(live.approved_hosts),
            manager_issuer=live.manager_issuer,
            manager_audience=live.manager_audience,
            live_region=live.live_region,  # type: ignore[arg-type]
            live_model_version=LIVE_MODEL_VERSION,  # type: ignore[arg-type]
            available_live_sessions=live.available_live_sessions,
            text_features=TEXT_FEATURES,
            region_reviewed=True,
            lifecycle_reviewed=True,
            retention_reviewed=True,
            identity_reviewed=True,
            private_store_reviewed=True,
            volatile_retention_seconds=300,
            max_input_tokens=live.max_input_tokens,
            max_output_tokens=live.max_output_tokens,
            voice=live.live_voice,
            max_clock_skew_seconds=float(live.max_clock_skew_seconds),
            text_history_ttl_seconds=live.text_history_ttl_seconds,
        )
    except Exception:
        raise StartupError("settings_invalid") from None


class LiveConversationRuntime(ConversationRuntime):
    """The reviewed runtime that also owns its Azure clients and releases them at shutdown."""

    def __init__(self, *, clients: AzureClients, http: httpx.AsyncClient, **keywords: Any) -> None:
        super().__init__(**keywords)
        self._clients, self._http = clients, http

    @asynccontextmanager
    async def running(self, telemetry: Telemetry) -> AsyncIterator[ConversationRuntime]:
        try:
            async with super().running(telemetry) as runtime:
                yield runtime
        finally:
            await self._clients.close()
            await self._http.aclose()


@dataclass(frozen=True)
class LiveComposition:
    runtime: ConversationRuntime
    dependencies: AzureClients


def build_live_runtime(
    settings: Settings,
    *,
    credential_factory: Callable[..., Any] | None = None,
    cosmos_factory: Callable[..., Any] | None = None,
    http: httpx.AsyncClient | None = None,
    configuration_transport: httpx.AsyncBaseTransport | None = None,
    text_transport: Any = None,
    utc_now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> LiveComposition:
    live = settings.live()
    options = runtime_options(live)
    factories: dict[str, Any] = {}
    if credential_factory is not None:
        factories["credential_factory"] = credential_factory
    if cosmos_factory is not None:
        factories["cosmos_factory"] = cosmos_factory
    clients = AzureClients(live, **factories)
    client = http or httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=2)
    identity = EntraWorkloadIdentity(
        tenant_id=live.tenant_id,
        audience=live.workload_audience,
        role=live.workload_role,
        principal_id=live.workload_principal_id,
        signing_keys=EntraSigningKeys(tenant_id=live.tenant_id, client=client),
    )
    runtime = LiveConversationRuntime(
        clients=clients,
        http=client,
        options=options,
        owner_id=settings.instance_id,
        container_factory=clients.container,
        credential_factory=clients.credential,
        workload_identity=identity,
        verification_keys={
            name: pem.encode("ascii") for name, pem in live.permit_verification_keys.items()
        },
        utc_now=utc_now or (lambda: datetime.now(UTC)),
        monotonic=monotonic,
        configuration_transport=configuration_transport,
        text_transport=text_transport,
    )
    return LiveComposition(runtime=runtime, dependencies=clients)
