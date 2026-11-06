"""Reviewed composition of the management application from explicitly supplied resources.

`compose_management` turns a reviewed `ManagementResources` bundle (stores, identity, workload
credential, capability transports) and an authority grant into the running app: configuration,
capability and runtime APIs, the console, the Reflex event guard and the permit signer. Nothing is
discovered from the environment; a missing or unreviewed resource raises before any router is
mounted, so a bare start serves only health.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import reflex as rx
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.types import ASGIApp, Receive, Scope, Send

from management_app.adapters.audit_repository import AuditContainer, CosmosAuditRepository
from management_app.adapters.backend_connection import TurnPermits
from management_app.adapters.configuration_repository import (
    ContainerPort,
    CosmosConfigurationRepository,
)
from management_app.adapters.mcp import McpTransport
from management_app.adapters.public_tools import (
    PublicReadTransport,
    Resolver,
    ReviewedTransport,
    SecretPort,
    resolve_public,
)
from management_app.adapters.test_session_client import TestSessionClient, WorkloadCredential
from management_app.adapters.text_relay import ActiveRevision
from management_app.api.capabilities import create_router as capability_router
from management_app.api.configuration import create_router as configuration_router
from management_app.api.runtime_configuration import create_router as runtime_router
from management_app.health import create_health_api
from management_app.metrics import ManagementMetrics
from management_app.models.capabilities import ApprovedCapabilities
from management_app.observability import Telemetry
from management_app.reflex_security import ReflexEventGuard
from management_app.security import (
    EntraPolicy,
    IdentityService,
    OidcFlow,
    RuntimeIdentity,
    SessionStore,
    SigningKeys,
    TestPermitSigner,
    TokenVerifier,
    create_auth_router,
)
from management_app.services.audit import AuditService
from management_app.services.bootstrap import bootstrap
from management_app.services.capability_gateway import CapabilityGateway
from management_app.services.capability_policy import CapabilityPolicy
from management_app.services.configuration_validation import ConfigurationValidator
from management_app.services.ports import ReadinessPort
from management_app.settings import Settings
from management_app.state.editor_draft import EditorServices
from management_app.state.test_console import ConsoleServices


class ManagementTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    runtime_endpoint: str = Field(min_length=1, max_length=512)
    runtime_audience: str
    issuer: str = Field(min_length=1, max_length=512)
    permit_audience: str = Field(min_length=1, max_length=512)
    test_role: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.]{0,127}$")
    permission_review_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    signing_review_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    runtime_review_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    capacity: int = Field(ge=5, le=5)
    retention_seconds: int = Field(ge=300, le=300)
    max_clock_skew_seconds: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def explicit_runtime(self) -> "ManagementTestRequest":
        endpoint = urlsplit(self.runtime_endpoint)
        UUID(self.runtime_audience)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.port not in {None, 443}
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
            or "\\" in self.runtime_endpoint
            or any(ord(character) < 33 for character in self.runtime_endpoint)
        ):
            raise ValueError("invalid_test_runtime_origin")
        return self


@dataclass(frozen=True, slots=True)
class ManagementTestResources:
    key_id: str
    signing_key: RSAPrivateKey = field(repr=False)
    verification_keys: Mapping[str, RSAPublicKey] = field(repr=False)
    credential: WorkloadCredential = field(repr=False)


class ManagementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    environment_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    configuration_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    audit_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    identity_review_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    capability_review_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    audit_retention_seconds: int = Field(strict=True, ge=3600, le=2147483647)
    policy: EntraPolicy
    runtime_audience: str
    runtime_role: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.]{0,127}$")
    runtime_clients: tuple[str, ...] = Field(min_length=1, max_length=32)
    tests: ManagementTestRequest | None = None

    @model_validator(mode="after")
    def distinct_boundaries(self) -> "ManagementRequest":
        UUID(self.runtime_audience)
        for client in self.runtime_clients:
            UUID(client)
        if (
            self.configuration_ref == self.audit_ref
            or self.runtime_role == self.policy.manager_role
        ):
            raise ValueError("distinct_management_resources_required")
        if self.tests is not None and (
            self.tests.test_role == self.policy.manager_role
            or self.tests.runtime_audience
            in {self.policy.client_id, self.policy.api_audience, self.runtime_audience}
        ):
            raise ValueError("distinct_test_authorities_required")
        return self


@dataclass(frozen=True, slots=True)
class CompositionGrant:
    fingerprint: str
    expires_at: datetime


class ManagementAuthority(Protocol):
    async def authorize(
        self, request: ManagementRequest, *, fingerprint: str, deadline: float
    ) -> CompositionGrant:
        """Verify actual approved resource/review scope; a reference is not approval."""
        ...


@dataclass(frozen=True, slots=True)
class ManagementResources:
    fingerprint: str
    configuration_container: ContainerPort
    audit_container: AuditContainer
    signing_keys: SigningKeys
    oidc: OidcFlow
    reviewed: ApprovedCapabilities
    secret_policy: dict[str, Any]
    secret_aliases: frozenset[str]
    destinations: Mapping[str, str]
    secrets: SecretPort
    readiness: ReadinessPort
    close: Callable[[], Awaitable[None]]
    resolver: Resolver = resolve_public
    network: httpx.AsyncBaseTransport | None = None
    tests: ManagementTestResources | None = field(default=None, repr=False)
    test_alert_sink: Callable[[dict[str, str]], None] | None = field(default=None, repr=False)


class ManagementResourcesFactory(Protocol):
    async def __call__(
        self, request: ManagementRequest, *, fingerprint: str, deadline: float
    ) -> ManagementResources: ...


class ApprovedSecrets:
    def __init__(self, source: SecretPort, aliases: frozenset[str]) -> None:
        self._source, self._aliases = source, aliases

    async def resolve(self, reference: str) -> str:
        if reference not in {"secret://" + alias for alias in self._aliases}:
            raise PermissionError("unapproved_management_credential_reference")
        return await self._source.resolve(reference)


@dataclass(frozen=True, slots=True)
class ManagedApplication:
    reflex_app: rx.App = field(repr=False)
    api: FastAPI = field(repr=False)
    asgi: ASGIApp = field(repr=False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.asgi(scope, receive, send)


async def require_grant(
    request: ManagementRequest, authority: ManagementAuthority, fingerprint: str, deadline: float
) -> None:
    try:
        async with asyncio.timeout_at(deadline):
            grant = await authority.authorize(request, fingerprint=fingerprint, deadline=deadline)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        raise TimeoutError("management_composition_deadline_exceeded") from None
    except PermissionError:
        raise PermissionError("management_composition_not_authorized") from None
    except Exception:
        raise RuntimeError("management_authority_unavailable") from None
    now = datetime.now(UTC)
    if (
        not isinstance(grant, CompositionGrant)
        or grant.fingerprint != fingerprint
        or not isinstance(grant.expires_at, datetime)
        or grant.expires_at.tzinfo is None
        or grant.expires_at.utcoffset() != timedelta(0)
        or not now < grant.expires_at <= now + timedelta(minutes=5)
    ):
        raise PermissionError("management_composition_not_authorized")


class ManagedStartup:
    def __init__(
        self,
        request: ManagementRequest,
        settings: Settings,
        authority: ManagementAuthority,
        resources: ManagementResources,
    ) -> None:
        self.request, self.authority, self.resources = request, authority, resources
        self.fingerprint = settings.composition_fingerprint(request.model_dump(mode="json"))
        self.closed = False
        self.test_relay: TestSessionClient | None = None

    async def require(self, settings: Settings, *, deadline: float | None = None) -> None:
        if (
            self.closed
            or settings.composition_fingerprint(self.request.model_dump(mode="json"))
            != self.fingerprint
        ):
            raise PermissionError("management_composition_binding_invalid")
        limit = deadline if deadline is not None else asyncio.get_running_loop().time() + 10
        await require_grant(self.request, self.authority, self.fingerprint, limit)

    async def shutdown(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                async with asyncio.timeout(10):
                    if self.test_relay is None:
                        await self.resources.close()
                    else:
                        await asyncio.gather(self.test_relay.close(), self.resources.close())
            except asyncio.CancelledError:
                raise
            except Exception:
                raise RuntimeError("management_resources_close_unavailable") from None


async def compose_management(
    settings: Settings,
    *,
    request: ManagementRequest | None,
    resources: ManagementResourcesFactory,
    authority: ManagementAuthority,
) -> ManagedApplication:
    from management_app.management_app import create_app, editor_asgi

    if not isinstance(request, ManagementRequest):
        raise ValueError("explicit_management_composition_request_required")
    fingerprint = settings.composition_fingerprint(request.model_dump(mode="json"))
    deadline = asyncio.get_running_loop().time() + 10
    async with asyncio.timeout_at(deadline):
        await require_grant(request, authority, fingerprint, deadline)
        try:
            resolved = await resources(request, fingerprint=fingerprint, deadline=deadline)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RuntimeError("management_resource_construction_unavailable") from None
        if not isinstance(resolved, ManagementResources):
            raise RuntimeError("invalid_management_resources")
        startup = ManagedStartup(request, settings, authority, resolved)
        telemetry: Telemetry | None = None
        metrics: ManagementMetrics | None = None
        try:
            if resolved.fingerprint != fingerprint or any(
                port is None
                for port in (
                    resolved.configuration_container,
                    resolved.audit_container,
                    resolved.signing_keys,
                    resolved.oidc,
                    resolved.secrets,
                    resolved.readiness,
                    resolved.close,
                )
            ):
                raise PermissionError("management_resource_binding_invalid")
            if (request.tests is None) != (resolved.tests is None) or (
                request.tests is not None
                and (
                    not isinstance(resolved.tests, ManagementTestResources)
                    or resolved.tests.credential is None
                )
            ):
                raise PermissionError("explicit_test_resources_required")
            await startup.require(settings, deadline=deadline)
            validator = ConfigurationValidator(
                policy=resolved.secret_policy,
                approved_secret_aliases=resolved.secret_aliases,
                approved_destination_refs=frozenset(resolved.destinations),
                reviewed=resolved.reviewed,
            )
            repository = CosmosConfigurationRepository(
                container=resolved.configuration_container, validator=validator
            )
            audit = AuditService(
                repository=CosmosAuditRepository(
                    container=resolved.audit_container,
                    retention_seconds=request.audit_retention_seconds,
                )
            )
            verifier = TokenVerifier(policy=request.policy, keys=resolved.signing_keys)
            identity = IdentityService(
                policy=request.policy,
                verifier=verifier,
                sessions=SessionStore(policy=request.policy),
            )
            runtime_identity = RuntimeIdentity(
                verifier=verifier,
                audience=request.runtime_audience,
                role=request.runtime_role,
                allowed_client_ids=frozenset(request.runtime_clients),
            )
            secrets = ApprovedSecrets(resolved.secrets, resolved.secret_aliases)
            transport = ReviewedTransport(
                direct=PublicReadTransport(
                    resolver=resolved.resolver, network=resolved.network, secrets=secrets
                ),
                mcp=McpTransport(
                    resolver=resolved.resolver, network=resolved.network, secrets=secrets
                ),
            )
            telemetry, metrics = (
                Telemetry("management", settings.mode, alert_sink=resolved.test_alert_sink),
                ManagementMetrics(mode=settings.mode),
            )
            signer: TestPermitSigner | None = None
            if request.tests is not None and resolved.tests is not None:
                signer = TestPermitSigner(
                    issuer=request.tests.issuer,
                    audience=request.tests.permit_audience,
                    key_id=resolved.tests.key_id,
                    signing_key=resolved.tests.signing_key,
                    verification_keys=resolved.tests.verification_keys,
                    utc_now=lambda: datetime.now(UTC),
                    max_clock_skew_seconds=request.tests.max_clock_skew_seconds,
                )

                async def active_revision() -> ActiveRevision:
                    publication = await repository.read_active()
                    if publication is None:
                        raise PermissionError("test_configuration_unavailable")
                    document, _etag = publication
                    welcome = next(
                        agent["agentId"]
                        for agent in document["agents"]
                        if agent["role"] == "welcome"
                    )
                    return ActiveRevision(document["id"], document["revisionNumber"], welcome)

                startup.test_relay = TestSessionClient(
                    endpoint=request.tests.runtime_endpoint,
                    audience=request.tests.runtime_audience,
                    approved_hosts=frozenset(
                        {str(urlsplit(request.tests.runtime_endpoint).hostname)}
                    ),
                    credential=resolved.tests.credential,
                    utc_now=lambda: datetime.now(UTC),
                    monotonic=time.monotonic,
                    max_clock_skew_seconds=request.tests.max_clock_skew_seconds,
                    telemetry=telemetry,
                    permits=TurnPermits(signer, lambda: datetime.now(UTC)),
                    active_revision=active_revision,
                )
            gateway = CapabilityGateway(
                repository=repository,
                policy=CapabilityPolicy(
                    reviewed=resolved.reviewed,
                    destinations=resolved.destinations,
                    detector=validator.detector,
                ),
                transport=transport,
                telemetry=telemetry,
                metrics=metrics,
            )
            await bootstrap(repository)
            await startup.require(settings, deadline=deadline)
            api = create_health_api(settings, dependencies=resolved.readiness, metrics=metrics)
            api.state.telemetry = telemetry
            api.state.management_startup = startup
            api.state.management_configuration = True
            api.include_router(
                configuration_router(
                    repository=repository,
                    identity=identity,
                    validator=validator,
                    audit=audit,
                    reader_role=request.tests.test_role if request.tests is not None else None,
                )
            )
            api.include_router(
                runtime_router(
                    repository=repository, identity=runtime_identity, telemetry=telemetry
                )
            )
            api.include_router(
                capability_router(
                    gateway=gateway,
                    repository=repository,
                    runtime_identity=runtime_identity,
                    manager_identity=identity,
                    telemetry=telemetry,
                    audit=audit,
                    audit_retention_seconds=request.audit_retention_seconds,
                )
            )
            api.include_router(create_auth_router(identity=identity, oidc=resolved.oidc))
            editor = EditorServices(
                api=api, guard=ReflexEventGuard(identity=identity), reviewed=resolved.reviewed
            )
            if request.tests is not None and signer is not None and startup.test_relay is not None:
                from management_app.api.test_sessions import create_router as test_router

                api.state.test_console_services = ConsoleServices(
                    guard=editor.guard, test_role=request.tests.test_role
                )
                api.include_router(
                    test_router(
                        identity=identity,
                        signer=signer,
                        test_role=request.tests.test_role,
                        telemetry=telemetry,
                        monotonic=time.monotonic,
                        max_clock_skew_seconds=request.tests.max_clock_skew_seconds,
                        relay=startup.test_relay,
                    )
                )
            api.state.management_identity = identity
            api.state.management_repository = repository
            application = create_app(settings, editor=editor)
            asgi = editor_asgi(
                application,
                additional_paths=frozenset(
                    {
                        "/v1/runtime/capabilities/execute",
                        "/v1/security/revocations",
                    }
                )
                | (
                    frozenset({"/v1/console/tickets"}) if request.tests is not None else frozenset()
                ),
            )
            return ManagedApplication(application, api, asgi)
        except BaseException as error:
            if telemetry is not None:
                telemetry.close()
            if metrics is not None:
                metrics.close()
            try:
                await startup.shutdown()
            except Exception:
                pass
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, PermissionError):
                raise PermissionError("management_composition_not_authorized") from None
            if isinstance(error, TimeoutError):
                raise TimeoutError("management_composition_deadline_exceeded") from None
            raise RuntimeError("management_composition_unavailable") from None
