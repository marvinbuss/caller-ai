"""Explicit reviewed browser-test composition; construction never discovers resources."""

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Final, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import httpx2
from fastapi import APIRouter
from pydantic import Field, model_validator

from caller_runtime.adapters.admission_repository import TestAdmissionRepository
from caller_runtime.adapters.capability_client import ConversationCapabilityClient
from caller_runtime.adapters.configuration_client import ConfigurationClient
from caller_runtime.adapters.foundry_text import FoundryTextAdapter
from caller_runtime.adapters.gpt_live import GptLiveAdapter, LiveSocket, sdk_socket
from caller_runtime.adapters.identity import TestPermitVerifier
from caller_runtime.adapters.text_history_repository import TextHistoryRepository
from caller_runtime.api.connection import WorkloadIdentity
from caller_runtime.api.text import create_router as create_text_router
from caller_runtime.api.voice import create_router as create_voice_router
from caller_runtime.domain.configuration import RevisionCache
from caller_runtime.domain.conversation import Identifier, Metadata, StartPermit, TestSlot, utc
from caller_runtime.domain.ports import ContainerPort, CredentialPort
from caller_runtime.observability import Telemetry
from caller_runtime.services.admission import AdmissionError, TestAdmissionService
from caller_runtime.services.cleanup import CleanupObservations, ResourceCleanupVerifier
from caller_runtime.services.conversation import ConversationExecutor
from caller_runtime.services.conversation_policy import ConversationPolicy, SpeechSegments
from caller_runtime.services.text_conversation import TextConversationService

AUTHORITY_CONTAINER: Final = "call-sessions"
HISTORY_PROBE_SECONDS: Final = 10
HISTORY_STALE_SECONDS: Final = 30


class RuntimeOptions(Metadata):
    __test__ = False
    review_reference: Identifier
    management_endpoint: str
    management_audience: str
    text_endpoint: str
    text_deployment: Identifier
    live_endpoint: str
    approved_hosts: frozenset[str]
    manager_issuer: str
    manager_audience: str
    live_region: Literal["canadacentral", "centralus", "eastus2", "swedencentral"]
    live_model_version: Literal["2026-09-10"]
    available_live_sessions: int = Field(ge=5, le=10000)
    text_features: frozenset[str]
    region_reviewed: Literal[True]
    lifecycle_reviewed: Literal[True]
    retention_reviewed: Literal[True]
    identity_reviewed: Literal[True]
    private_store_reviewed: Literal[True]
    volatile_retention_seconds: Literal[300]
    max_input_tokens: int = Field(ge=1024, le=131072)
    max_output_tokens: int = Field(ge=1, le=4096)
    voice: str = Field(min_length=1, max_length=64)
    max_clock_skew_seconds: float = Field(ge=0, le=1, allow_inf_nan=False)
    text_history_container: str = Field(
        default="text-conversations", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$"
    )
    text_history_ttl_seconds: int = Field(default=2_592_000, ge=1, le=2_592_000)

    @model_validator(mode="after")
    def reviewed_bindings(self) -> Self:
        try:
            UUID(self.management_audience)
            if (
                not self.approved_hosts
                or len(self.approved_hosts) > 8
                or self.text_features
                != {"streaming", "function_tools", "storage_disabled", "structured_output"}
                or self.text_deployment == "gpt-live-1"
                or not math.isfinite(self.max_clock_skew_seconds)
            ):
                raise ValueError("unapproved_binding")
            for role, endpoint in (
                ("management", self.management_endpoint),
                ("text", self.text_endpoint),
                ("live", self.live_endpoint),
            ):
                parts = urlsplit(endpoint)
                if (
                    parts.scheme != "https"
                    or parts.hostname not in self.approved_hosts
                    or not parts.hostname
                    or parts.path not in {"", "/"}
                    or parts.query
                    or parts.fragment
                    or parts.username is not None
                    or parts.password is not None
                    or parts.port not in {None, 443}
                    or "\\" in endpoint
                    or any(ord(character) < 33 for character in endpoint)
                ):
                    raise ValueError("unapproved_binding")
                if role != "management" and not parts.hostname.endswith(
                    (".openai.azure.com", ".services.ai.azure.com")
                ):
                    raise ValueError("unapproved_binding")
        except Exception:
            raise ValueError("invalid_reviewed_test_runtime") from None
        return self


class ConversationRuntime:
    __test__ = False

    def __init__(
        self,
        *,
        options: RuntimeOptions,
        owner_id: str,
        container_factory: Callable[[str], Awaitable[ContainerPort]],
        credential_factory: Callable[[], Awaitable[CredentialPort]],
        workload_identity: WorkloadIdentity,
        verification_keys: Mapping[str, bytes],
        utc_now: Callable[[], datetime],
        monotonic: Callable[[], float],
        configuration_transport: httpx.AsyncBaseTransport | None = None,
        capability_transport: httpx.AsyncBaseTransport | None = None,
        text_transport: httpx2.AsyncBaseTransport | None = None,
        live_socket_factory: Callable[..., Awaitable[LiveSocket]] = sdk_socket,
    ) -> None:
        if (
            not isinstance(options, RuntimeOptions)
            or not owner_id
            or len(owner_id) > 128
            or any(
                port is None
                for port in (
                    container_factory,
                    credential_factory,
                    workload_identity,
                    utc_now,
                    monotonic,
                )
            )
            or not callable(container_factory)
            or not callable(credential_factory)
        ):
            raise ValueError("explicit_reviewed_test_runtime_required")
        self.options, self.owner_id = options, owner_id
        self._container_factory, self._credential_factory = container_factory, credential_factory
        self._workload_identity, self._utc_now, self._monotonic = (
            workload_identity,
            utc_now,
            monotonic,
        )
        self._permits = TestPermitVerifier(
            issuer=options.manager_issuer,
            audience=options.manager_audience,
            keys=verification_keys,
            utc_now=utc_now,
            max_clock_skew_seconds=options.max_clock_skew_seconds,
        )
        self._running = False
        self._used = False
        self.repository: TestAdmissionRepository | None = None
        self.cache = RevisionCache(monotonic=monotonic)
        self._configuration_transport = configuration_transport
        self._capability_transport, self._text_transport = capability_transport, text_transport
        self._live_socket_factory = live_socket_factory
        self._credential: CredentialPort | None = None
        self._configuration: ConfigurationClient | None = None
        self.admission: TestAdmissionService | None = None
        self.observations: CleanupObservations | None = None
        self._sessions: dict[str, ConversationExecutor] = {}
        self._telemetry: Telemetry | None = None
        self.history: TextHistoryRepository | None = None
        self._history_ok_at: float | None = None
        self._history_probe: asyncio.Task[None] | None = None
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        self._starting: set[asyncio.Task[object]] = set()
        self._stop = asyncio.Event()

    @property
    def active_sessions(self) -> tuple[str, ...]:
        return tuple(self._sessions)

    def router(self) -> APIRouter:
        def telemetry() -> Telemetry:
            if self._telemetry is None:
                raise AdmissionError("test_runtime_unavailable")
            return self._telemetry

        router = APIRouter()
        router.include_router(
            create_voice_router(
                service=self,
                workload_identity=self._workload_identity,
                permits=self._permits,
                telemetry=telemetry,
                utc_now=self._utc_now,
                max_clock_skew_seconds=self.options.max_clock_skew_seconds,
            )
        )
        router.include_router(
            create_text_router(
                service=self._text_service(telemetry),
                workload_identity=self._workload_identity,
                permits=self._permits,
            )
        )
        return router

    def _text_service(self, telemetry: Callable[[], Telemetry]) -> TextConversationService:
        def history() -> TextHistoryRepository:
            if self.history is None:
                raise AdmissionError("history_unavailable")
            return self.history

        return TextConversationService(
            runtime=self,
            history=history,
            telemetry=telemetry,
            utc_now=self._utc_now,
        )

    def history_ready(self) -> bool:
        """Report the cached store probe; a stale or failed probe means not ready."""
        if self._history_ok_at is None:
            return False
        return self._monotonic() - self._history_ok_at <= HISTORY_STALE_SECONDS

    async def probe_history(self) -> None:
        history = self.history
        if history is None:
            return
        if await history.probe(deadline=asyncio.get_running_loop().time() + 1):
            self._history_ok_at = self._monotonic()

    async def _probe_history(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.probe_history()
            try:
                async with asyncio.timeout(HISTORY_PROBE_SECONDS):
                    await stop.wait()
            except TimeoutError:
                continue

    def _schedule_cleanup(self, controller: ConversationExecutor) -> None:
        controller.fence()

        async def cleanup() -> None:
            try:
                released = await controller.close()
                if released:
                    self._sessions.pop(controller.policy.slot.session_id, None)
            except Exception:
                pass

        task = asyncio.create_task(cleanup())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    def _schedule_unstarted(self, slot: TestSlot, text: FoundryTextAdapter | None) -> None:
        admission, observations = self.admission, self.observations
        if admission is None or observations is None:
            return
        admission.fence_local(slot.session_id)

        async def disposed() -> bool:
            return text is None or text.disposed

        async def cleanup() -> None:
            try:
                async with asyncio.timeout(10):
                    terminal = await admission.mark_terminal(slot.session_id, slot.owner_epoch)
                    if text is not None:
                        await text.close()
                    observations.register(slot, disposed=disposed)
                    current = await admission.repository.read(
                        deadline=asyncio.get_running_loop().time() + 1
                    )
                    terminal = next(
                        entry
                        for entry in current.authority.slots
                        if entry.session_id == slot.session_id
                    )
                    observed = await observations.observe(
                        terminal, asyncio.get_running_loop().time() + 1
                    )
                    if observed is not None and await admission.release(
                        slot.session_id,
                        slot.owner_epoch,
                        ResourceCleanupVerifier.evidence(terminal, observed),
                    ):
                        observations.forget(slot.session_id)
            except Exception:
                pass

        task = asyncio.create_task(cleanup())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def start(
        self, permit: StartPermit, *, emit: Callable[[dict[str, object]], Awaitable[None]]
    ) -> ConversationExecutor:
        admission, credential, observations, telemetry = (
            self.admission,
            self._credential,
            self.observations,
            self._telemetry,
        )
        if (
            not self._running
            or self._stop.is_set()
            or admission is None
            or credential is None
            or observations is None
            or telemetry is None
        ):
            raise AdmissionError("test_runtime_unavailable")
        remaining = min(
            10.0,
            (permit.admission_deadline_utc - utc(self._utc_now())).total_seconds()
            - self.options.max_clock_skew_seconds,
        )
        if remaining <= 0:
            raise AdmissionError("admission_deadline_exceeded")
        current_task = asyncio.current_task()
        if current_task is not None:
            self._starting.add(current_task)
        controller: ConversationExecutor | None = None
        slot: TestSlot | None = None
        text: FoundryTextAdapter | None = None
        try:
            async with asyncio.timeout(remaining):
                if self._configuration is None or not await self._configuration.refresh(
                    require_current=True
                ):
                    raise AdmissionError("configuration_unavailable")
                slot = await admission.reserve(permit)
                current = await admission.repository.read(
                    deadline=asyncio.get_running_loop().time() + min(1, remaining)
                )
                present = {entry.session_id for entry in current.authority.slots}
                for session_id in tuple(self._sessions):
                    if session_id not in present:
                        self._sessions.pop(session_id, None)
                snapshot = admission.snapshot_for(slot)
                text = FoundryTextAdapter(
                    endpoint=self.options.text_endpoint,
                    deployment=self.options.text_deployment,
                    approved_hosts=self.options.approved_hosts,
                    credential=credential,
                    admission=admission,
                    utc_now=self._utc_now,
                    max_input_tokens=self.options.max_input_tokens,
                    max_output_tokens=self.options.max_output_tokens,
                    transport=self._text_transport,
                    settlement_observer=observations,
                    telemetry=telemetry,
                )
                capabilities = ConversationCapabilityClient(
                    endpoint=self.options.management_endpoint,
                    approved_hosts=self.options.approved_hosts,
                    audience=self.options.management_audience,
                    credential=credential,
                    admission=admission,
                    utc_now=self._utc_now,
                    transport=self._capability_transport,
                    settlement_observer=observations,
                )
                policy = ConversationPolicy(slot=slot, snapshot=snapshot, admission=admission)
                controller = ConversationExecutor(
                    policy=policy,
                    text_adapter=text,
                    capability_client=capabilities,
                    utc_now=self._utc_now,
                    cleanup_observations=observations,
                    telemetry=telemetry,
                )
                self._sessions[slot.session_id] = controller
                if slot.mode == "speech":
                    await controller.prepare()
                if slot.mode == "speech":
                    live = GptLiveAdapter(
                        endpoint=self.options.live_endpoint,
                        approved_hosts=self.options.approved_hosts,
                        credential=credential,
                        admission=admission,
                        utc_now=self._utc_now,
                        voice=self.options.voice,
                        socket_factory=self._live_socket_factory,
                        settlement_observer=observations,
                        telemetry=telemetry,
                    )
                    configured = {
                        agent["agentId"]: agent for agent in snapshot.for_runtime()["agents"]
                    }

                    def instructions(current: TestSlot) -> str:
                        return str(configured[current.active_agent_id]["prompt"]) + (
                            "\nDelegate reasoning, business actions and agent changes "
                            "to the application-owned client. "
                            "Do not authorize capabilities or infer complete tasks "
                            "from transcript fragments."
                        )

                    segment = await live.start(slot, instructions=instructions(slot))
                    segments = SpeechSegments(
                        current=segment, instructions_for=instructions, emit=emit
                    )
                    controller.speech_segments = segments
                    policy._retire_speech, policy._continue_speech = (
                        segments.retire,
                        segments.continue_with,
                    )
                policy.slot = await admission.mark_ready(slot.session_id, slot.owner_epoch)
                telemetry.event("test.admission", "success", mode=slot.mode, reason="ready")
                return controller
        except BaseException as error:
            if not isinstance(error, asyncio.CancelledError) and (
                not isinstance(error, AdmissionError)
                or error.code in {"configuration_unavailable", "test_runtime_unavailable"}
            ):
                telemetry.event("test.admission", "failure", mode=permit.mode, reason="unavailable")
            if controller is not None:
                self._schedule_cleanup(controller)
            elif slot is not None:
                self._schedule_unstarted(slot, text)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise AdmissionError(
                error.code if isinstance(error, AdmissionError) else "test_runtime_unavailable"
            ) from None
        finally:
            if current_task is not None:
                self._starting.discard(current_task)

    @asynccontextmanager
    async def running(self, telemetry: Telemetry) -> AsyncIterator["ConversationRuntime"]:
        if self._used or self._running or telemetry is None:
            raise AdmissionError("test_runtime_unavailable")
        self._used = True
        deadline = asyncio.get_running_loop().time() + 10
        stop = asyncio.Event()
        refresh: asyncio.Task[None] | None = None
        try:
            async with asyncio.timeout_at(deadline):
                container = await self._container_factory(AUTHORITY_CONTAINER)
                repository = TestAdmissionRepository(container=container)
                initial_authority = await repository.read(deadline=deadline)
                self.repository = repository
                self._credential = await self._credential_factory()
                if self._credential is None:
                    raise ValueError("explicit_runtime_credential_required")
                self._configuration = ConfigurationClient(
                    endpoint=self.options.management_endpoint,
                    audience=self.options.management_audience,
                    credential=self._credential,
                    cache=self.cache,
                    transport=self._configuration_transport,
                    monotonic=self._monotonic,
                )
                if not await self._configuration.refresh():
                    raise AdmissionError("test_runtime_unavailable")
                self.observations = CleanupObservations(
                    owner_id=self.owner_id, utc_now=self._utc_now
                )
                self.admission = TestAdmissionService(
                    repository=repository,
                    configuration=self.cache,
                    owner_id=self.owner_id,
                    utc_now=self._utc_now,
                    monotonic=self._monotonic,
                    max_clock_skew_seconds=self.options.max_clock_skew_seconds,
                    cleanup_verifier=ResourceCleanupVerifier(
                        observe=self.observations.observe, utc_now=self._utc_now
                    ),
                )
                self._telemetry = telemetry
                for retained in initial_authority.authority.slots:
                    if retained.terminal_fence:
                        telemetry.event(
                            "test.cleanup", "failure", mode=retained.mode, reason="cleanup_pending"
                        )
                history_container = await self._container_factory(
                    self.options.text_history_container
                )
                self.history = TextHistoryRepository(
                    container=history_container,
                    ttl_seconds=self.options.text_history_ttl_seconds,
                    utc_now=self._utc_now,
                )
                self._history_probe = asyncio.create_task(self._probe_history(stop))
                self._running = True
                refresh = asyncio.create_task(self._configuration.run(stop))
        except Exception:
            if self._credential is not None:
                close = getattr(self._credential, "close", None)
                if callable(close):
                    try:
                        async with asyncio.timeout(10):
                            await close()
                    except Exception:
                        pass
            raise AdmissionError("test_runtime_unavailable") from None
        try:
            yield self
        finally:
            self._running = False
            self._stop.set()
            stop.set()
            if refresh is not None:
                refresh.cancel()
                await asyncio.gather(refresh, return_exceptions=True)
            if self._history_probe is not None:
                self._history_probe.cancel()
                await asyncio.gather(self._history_probe, return_exceptions=True)
            for controller in self._sessions.values():
                controller.fence()
            pending = tuple(task for task in self._starting if task is not asyncio.current_task())
            for task in pending:
                task.cancel()
            try:
                async with asyncio.timeout(10):
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    await asyncio.gather(
                        *(controller.close() for controller in tuple(self._sessions.values())),
                        return_exceptions=True,
                    )
                    if self._cleanup_tasks:
                        await asyncio.gather(*tuple(self._cleanup_tasks), return_exceptions=True)
            finally:
                self._sessions.clear()
            if self._credential is not None:
                close = getattr(self._credential, "close", None)
                if callable(close):
                    async with asyncio.timeout(10):
                        await close()
