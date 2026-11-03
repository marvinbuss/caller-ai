"""Guarded management composition over explicit reviewed resources; no implicit cloud access."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import httpx
import reflex as rx
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.types import ASGIApp, Receive, Scope, Send

from management_app.adapters.audit_repository import AuditContainer, CosmosAuditRepository
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
from management_app.api.capabilities import create_router as capability_router
from management_app.api.configuration import create_router as configuration_router
from management_app.api.runtime_configuration import create_router as runtime_router
from management_app.health import create_health_api
from management_app.metrics import ManagementMetrics
from management_app.models.capabilities import ApprovedCapabilities
from management_app.observability import Telemetry
from management_app.security import (
    EntraPolicy,
    IdentityService,
    OidcFlow,
    ReflexEventGuard,
    RuntimeIdentity,
    SessionStore,
    SigningKeys,
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
from management_app.state.configuration_editor import EditorServices


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

    @model_validator(mode="after")
    def distinct_boundaries(self) -> "ManagementRequest":
        UUID(self.runtime_audience)
        for client in self.runtime_clients:
            UUID(client)
        if self.configuration_ref == self.audit_ref or self.runtime_audience in {
            self.policy.client_id,
            self.policy.api_audience,
        }:
            raise ValueError("distinct_management_resources_required")
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
                    await self.resources.close()
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
                Telemetry("management", settings.mode),
                ManagementMetrics(mode=settings.mode),
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
                    repository=repository, identity=identity, validator=validator, audit=audit
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
