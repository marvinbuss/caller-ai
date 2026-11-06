"""Builds the live management app from validated settings, reviewed by a fingerprint (FR-047).

`management_request` turns the settings into the explicit `ManagementRequest` the composition
needs. `ReviewedFingerprintAuthority` grants a composition only when the fingerprint of the
reviewable settings equals `CALLER_REVIEWED_FINGERPRINT`: the owner records that value after
reviewing the non-secret settings, so a changed setting needs a new review. Values that exist only
after the apply (the apps' addresses and what derives from them) are covered through the names that
fix them and are left out of the fingerprint (research R-11). Secrets never enter it.
`LiveManagementApplication` composes inside the server's own event loop, at lifespan startup, so
the data-store clients live in the loop that uses them.
"""

import asyncio
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from msal import ConfidentialClientApplication  # type: ignore[import-untyped]
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from management_app.adapters.azure_clients import AzureClients
from management_app.adapters.entra_oidc import BoundedEntraHttp, EntraSigningKeys, MsalCodeFlow
from management_app.composition import (
    CompositionGrant,
    ManagedApplication,
    ManagementRequest,
    ManagementResources,
    ManagementResourcesFactory,
    ManagementTestRequest,
    ManagementTestResources,
)
from management_app.models.capabilities import ApprovedCapabilities
from management_app.security import EntraPolicy
from management_app.settings import LiveSettings, Settings, StartupError

REVIEW_EXCLUDED = frozenset(
    {
        "reviewed_fingerprint",
        "review_reference",
        "azure_client_id",
        "management_origin",
        "public_origin",
        "runtime_endpoint",
        "runtime_audience",
        "permit_issuer",
        "permit_audience",
    }
)
POLICY_FILE = Path(__file__).parent / "services" / "secret_detection_policy.json"


def reviewed_fingerprint(live: LiveSettings) -> str:
    """Fingerprint of the settings a reviewer can hold before the apply (research R-11)."""
    manifest = {
        name: value for name, value in live.non_secret().items() if name not in REVIEW_EXCLUDED
    }
    value = {"mode": "live", "manifest": manifest}
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def management_request(live: LiveSettings) -> ManagementRequest:
    """The explicit composition request; a value that cannot form one is `settings_invalid`."""
    prefix = f"api://{live.api_audience}/"
    if live.manager_scope.lower() != prefix.lower() + ".default":
        raise StartupError("settings_invalid")
    reference = live.review_reference
    try:
        return ManagementRequest(
            environment_ref=live.cosmos_database,
            configuration_ref="configuration",
            audit_ref="configuration-audit",
            identity_review_ref=reference,
            capability_review_ref=reference,
            audit_retention_seconds=live.audit_retention_seconds,
            policy=EntraPolicy(
                tenant_id=live.tenant_id,
                client_id=live.entra_client_id,
                api_audience=live.api_audience,
                origin=live.management_origin.rstrip("/"),
                manager_role=live.manager_role,
                manager_scope=".default",
            ),
            runtime_audience=live.api_audience,
            runtime_role=live.runtime_role,
            runtime_clients=(live.runtime_client_id,),
            tests=ManagementTestRequest(
                runtime_endpoint=live.runtime_endpoint.rstrip("/"),
                runtime_audience=live.runtime_audience,
                issuer=live.permit_issuer.rstrip("/"),
                permit_audience=live.permit_audience.rstrip("/"),
                test_role=live.test_role,
                permission_review_ref=reference,
                signing_review_ref=reference,
                runtime_review_ref=reference,
                capacity=5,
                retention_seconds=300,
                max_clock_skew_seconds=1,
            ),
        )
    except Exception:
        raise StartupError("settings_invalid") from None


class ReviewedFingerprintAuthority:
    """Grants a short-lived composition only for the reviewed settings."""

    def __init__(
        self,
        live: LiveSettings,
        request: ManagementRequest,
        *,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._live, self._request, self._utc_now = live, request, utc_now

    async def authorize(
        self, request: ManagementRequest, *, fingerprint: str, deadline: float
    ) -> CompositionGrant:
        computed = reviewed_fingerprint(self._live)
        if request != self._request or not hmac.compare_digest(
            computed.encode(), self._live.reviewed_fingerprint.encode()
        ):
            raise PermissionError("fingerprint_mismatch")
        return CompositionGrant(fingerprint, self._utc_now() + timedelta(seconds=60))


class NoSecretReferences:
    """The int journey approves no tool credential, so no reference resolves."""

    async def resolve(self, reference: str) -> str:
        raise PermissionError("no_secret_reference_approved")


def signing_material(live: LiveSettings) -> tuple[RSAPrivateKey, Mapping[str, Any]]:
    """Load the permit private key from the settings; anything but RSA is `key_invalid`."""
    try:
        key = serialization.load_pem_private_key(
            live.signing_key_pem.encode("ascii"), password=None
        )
    except Exception:
        raise StartupError("key_invalid") from None
    if not isinstance(key, RSAPrivateKey) or not 2048 <= key.key_size <= 8192:
        raise StartupError("key_invalid")
    return key, {live.signing_key_id: key.public_key()}


def build_resources(
    live: LiveSettings, clients: AzureClients, http: httpx.AsyncClient
) -> ManagementResourcesFactory:
    key, verification = signing_material(live)
    policy = json.loads(POLICY_FILE.read_text(encoding="utf-8"))

    async def close() -> None:
        await clients.close()
        await http.aclose()

    async def resources(
        request: ManagementRequest, *, fingerprint: str, deadline: float
    ) -> ManagementResources:
        def application() -> ConfidentialClientApplication:
            return ConfidentialClientApplication(
                client_id=live.entra_client_id,
                client_credential=live.entra_client_secret,
                authority=f"https://login.microsoftonline.com/{live.tenant_id}",
                instance_discovery=False,
                http_client=BoundedEntraHttp(policy=request.policy),
                exclude_scopes=["offline_access"],
            )

        return ManagementResources(
            fingerprint=fingerprint,
            configuration_container=clients.container("configuration"),
            audit_container=clients.container("configuration-audit"),
            signing_keys=EntraSigningKeys(policy=request.policy, client=http),
            oidc=MsalCodeFlow(policy=request.policy, application_factory=application),
            reviewed=ApprovedCapabilities(),
            secret_policy=policy,
            secret_aliases=frozenset(),
            destinations={},
            secrets=NoSecretReferences(),
            readiness=clients,
            close=close,
            tests=ManagementTestResources(
                key_id=live.signing_key_id,
                signing_key=key,
                verification_keys=dict(verification),
                credential=clients.credential,
            ),
        )

    return resources


async def build_live_management(
    settings: Settings,
    *,
    credential_factory: Callable[..., Any] | None = None,
    cosmos_factory: Callable[..., Any] | None = None,
    http: httpx.AsyncClient | None = None,
) -> ManagedApplication:
    """Compose the live management app, or raise `StartupError` with one fixed reason code."""
    from management_app.management_app import create_managed_app

    live = settings.live()
    request = management_request(live)
    settings.require_fingerprint(reviewed_fingerprint(live))
    factories: dict[str, Any] = {}
    if credential_factory is not None:
        factories["credential_factory"] = credential_factory
    if cosmos_factory is not None:
        factories["cosmos_factory"] = cosmos_factory
    clients = AzureClients(live, **factories)
    client = http or httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=2)
    try:
        resources = build_resources(live, clients, client)
        return await create_managed_app(
            settings,
            resources=resources,
            authority=ReviewedFingerprintAuthority(live, request),
            request=request,
        )
    except BaseException as error:
        await clients.close()
        await client.aclose()
        if isinstance(error, asyncio.CancelledError | StartupError):
            raise
        if isinstance(error, PermissionError):
            raise StartupError("fingerprint_mismatch") from None
        raise StartupError("store_unavailable") from None


class LiveManagementApplication:
    """ASGI application that composes at lifespan startup and then serves the composition."""

    def __init__(
        self,
        settings: Settings,
        *,
        build: Callable[[Settings], Awaitable[ManagedApplication]] = build_live_management,
    ) -> None:
        self._settings, self._build = settings, build
        self._inner: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(scope, receive, send)
        elif self._inner is None:
            await self._refuse(scope, receive, send)
        else:
            await self._inner(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return
        body = b'{"status":"not_ready"}'
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _lifespan(self, scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        if message["type"] != "lifespan.startup":
            return
        try:
            managed = await self._build(self._settings)
        except StartupError as error:
            await send({"type": "lifespan.startup.failed", "message": error.code})
            return
        except Exception:
            await send({"type": "lifespan.startup.failed", "message": "store_unavailable"})
            return
        inbound: asyncio.Queue[Message] = asyncio.Queue()
        outbound: asyncio.Queue[Message] = asyncio.Queue()
        task = asyncio.create_task(managed(scope, inbound.get, outbound.put))
        await inbound.put(message)
        reply = await self._next(task, outbound)
        if reply["type"] != "lifespan.startup.complete":
            await send(reply)
            await asyncio.gather(task, return_exceptions=True)
            return
        self._inner = managed
        await send(reply)
        message = await receive()
        self._inner = None
        await inbound.put(message)
        await send(await self._next(task, outbound))
        await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    async def _next(task: asyncio.Task[None], queue: asyncio.Queue[Message]) -> Message:
        waiting = asyncio.ensure_future(queue.get())
        await asyncio.wait({task, waiting}, return_when=asyncio.FIRST_COMPLETED)
        if waiting.done():
            return waiting.result()
        waiting.cancel()
        return {"type": "lifespan.startup.failed", "message": "store_unavailable"}
