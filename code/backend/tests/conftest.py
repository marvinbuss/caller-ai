"""Remove inherited live configuration from deterministic runtime tests."""

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def community_configuration() -> dict:
    ordinary = [
        ("a-source", "alpha", True),
        ("a-entry", "alpha", True),
        ("a-peer", "alpha", True),
        ("a-disabled", "alpha", False),
        ("b-entry", "beta", True),
        ("b-peer", "beta", True),
    ]
    agents = [
        {
            "agentId": role,
            "name": role.title(),
            "prompt": "Synthetic " + role + " prompt",
            "enabled": True,
            "role": role,
            "communityId": None,
            "capabilityAssignments": [],
        }
        for role in ("welcome", "routing", "goodbye")
    ]
    agents += [
        {
            "agentId": identifier,
            "name": identifier,
            "prompt": "Synthetic prompt for " + identifier,
            "enabled": enabled,
            "role": "ordinary",
            "communityId": community,
            "capabilityAssignments": []
            if community == "alpha"
            else [
                {
                    "agentId": identifier,
                    "toolId": "beta-tool",
                    "permittedActionNames": ["public-lookup"],
                    "allowedInputConstraints": {},
                }
            ],
        }
        for identifier, community, enabled in ordinary
    ]
    pairs = [
        ("welcome", "routing"),
        ("routing", "goodbye"),
        ("routing", "a-entry"),
        ("routing", "b-entry"),
        ("a-source", "a-entry"),
        ("a-source", "a-peer"),
        ("a-peer", "a-source"),
        ("b-entry", "b-peer"),
    ]
    pairs += [
        (identifier, target)
        for identifier, _, enabled in ordinary
        if enabled
        for target in ("routing", "goodbye")
    ]
    return {
        "revisionId": "revision-us3-fixture",
        "schemaVersion": "1.0.0",
        "agents": agents,
        "communities": [
            {
                "communityId": community,
                "name": community.title(),
                "routingDescription": "Synthetic " + community + " routing",
                "enabled": True,
                "entryAgentId": entry,
            }
            for community, entry in (("alpha", "a-entry"), ("beta", "b-entry"))
        ],
        "relationships": [
            {
                "relationshipId": f"path-{index}",
                "sourceAgentId": source,
                "targetAgentId": target,
                "allowedContextFields": ["request_summary", "transfer_reason"],
            }
            for index, (source, target) in enumerate(pairs)
        ],
        "tools": [
            {
                "toolId": "beta-tool",
                "name": "Synthetic beta tool",
                "description": "Foreign tool marker",
                "schemaVersion": "1.0.0",
                "inputSchema": {"type": "object"},
                "outputAllowlist": ["name"],
                "enabled": True,
                "approvedDestinationRef": "approved-public-fixture",
                "mcpConnectionId": None,
                "effect": "read_only",
                "classification": "public",
            }
        ],
        "mcpConnections": [],
    }


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith(("CALLER_", "AZURE_", "OPENAI_")):
            monkeypatch.delenv(name)


@pytest.fixture
def revision_document(community_configuration):
    def create(number=1):
        graph = copy.deepcopy(community_configuration)
        graph.pop("revisionId")
        for agent in graph["agents"]:
            if agent["agentId"] == "a-source":
                agent["prompt"] += f" revision {number}"
        body = json.dumps(
            graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
        return graph | {
            "id": f"revision-fixture-{number}",
            "organizationId": "default",
            "revisionNumber": number,
            "createdAt": "2026-10-26T00:00:00Z",
            "createdBy": "manager-fixture",
            "graphHash": hashlib.sha256(body).hexdigest(),
        }

    return create


@pytest.fixture
def scripted_handover_models():
    from agents.realtime.model import RealtimeModel

    class ScriptedModel(RealtimeModel):
        def __init__(self, factory, behavior):
            self.factory, self.behavior = factory, behavior
            self.listeners, self.options, self.sent = [], [], []
            self.closed = 0

        async def connect(self, options):
            self.options.append(options)
            self.factory.preparing.set()
            if self.behavior == "block":
                await self.factory.release.wait()
            if self.behavior == "fail":
                raise ConnectionError("synthetic-private-model-details")

        def add_listener(self, listener):
            self.listeners.append(listener)

        def remove_listener(self, listener):
            self.listeners.remove(listener)

        async def send_event(self, event):
            self.sent.append(event)

        async def send_event_if(self, event, send_if):
            if not send_if():
                return False
            self.sent.append(event)
            return True

        async def close(self):
            self.closed += 1
            if self.factory.block_close_for is self:
                self.factory.closing.set()
                await self.factory.close_release.wait()

    class Factory:
        def __init__(self):
            self.models = []
            self.next_behavior = "ok"
            self.preparing, self.release = asyncio.Event(), asyncio.Event()
            self.block_close_for = None
            self.closing, self.close_release = asyncio.Event(), asyncio.Event()

        def __call__(self):
            model = ScriptedModel(self, self.next_behavior)
            self.next_behavior = "ok"
            self.models.append(model)
            return model

    return Factory()


@pytest.fixture
def transfer_policy():
    from caller_runtime.domain.transfer_context import TransferContextPolicy

    policy = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/secret-detection-policy.json").read_text()
    )
    return TransferContextPolicy(policy=policy)


@pytest.fixture
def handover_host(community_configuration, scripted_handover_models, transfer_policy):
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime, timedelta

    from caller_runtime.adapters.agents_sdk import AgentsSdkAdapter
    from caller_runtime.adapters.session_repository import LocalSessionRepository
    from caller_runtime.domain.session import CallSession, LifecycleState
    from caller_runtime.domain.visibility import CommunityVisibility
    from caller_runtime.observability import Telemetry
    from caller_runtime.services.handover import HandoverCoordinator
    from caller_runtime.settings import Settings

    class Phone:
        def __init__(self):
            self.clears, self.hangups, self.output = 0, 0, []

        async def clear(self, provider_call_ref):
            self.clears += 1

        async def hangup(self, provider_call_ref):
            self.hangups += 1
            return True

        async def send_audio(self, payload):
            self.output.append(payload)

    @asynccontextmanager
    async def opened(*, budget=10.0, clock=None, metrics=None, configuration=None):
        configuration = community_configuration if configuration is None else configuration
        repository, phone = LocalSessionRepository(), Phone()
        now = datetime.now(UTC)
        metadata = CallSession(
            sessionId="session-us3-fixture",
            providerCallRef="phone-us3-fixture",
            configurationRevisionId=configuration["revisionId"],
            physicalOwnerId="physical-us3-owner",
            connectionEpoch=1,
            agentEpoch=1,
            activeAgentId="a-source",
            activeCommunityId="alpha",
            state=LifecycleState.ACTIVE,
            startedAt=now,
            leaseUntil=now + timedelta(seconds=60),
        )
        await repository.create(metadata.to_document())
        telemetry = Telemetry("caller-runtime", "local")
        sdk = AgentsSdkAdapter(
            settings=Settings(), telemetry=telemetry, model_factory=scripted_handover_models
        )
        coordinator = HandoverCoordinator(
            repository=repository,
            sdk=sdk,
            visibility=CommunityVisibility(configuration),
            context_policy=transfer_policy,
            operation_budget=budget,
            metrics=metrics,
            **({"monotonic": clock} if clock else {}),
        )
        try:
            await coordinator.own(
                metadata.session_id, physical_owner_id=metadata.physical_owner_id, phone=phone
            )
            yield coordinator, repository, phone, scripted_handover_models, telemetry
        finally:
            await coordinator.shutdown()
            telemetry.close()

    return opened


@pytest.fixture
def capacity_case(community_configuration):
    import copy
    from datetime import UTC, datetime, timedelta

    from caller_runtime.adapters.session_repository import LocalSessionRepository
    from caller_runtime.services.capacity import CapacityService

    class Clock:
        seconds = 0.0

        def monotonic(self):
            return self.seconds

        def utc_now(self):
            return datetime(2026, 10, 26, tzinfo=UTC) + timedelta(seconds=self.seconds)

        def advance(self, seconds):
            self.seconds += seconds

    clock, repository = Clock(), LocalSessionRepository()
    service = CapacityService(
        repository=repository,
        configuration=copy.deepcopy(community_configuration),
        sessions_per_owner=100,
        maximum_sessions=200,
        model_session_quota=200,
        monotonic=clock.monotonic,
        utc_now=clock.utc_now,
    )
    return service, repository, clock
