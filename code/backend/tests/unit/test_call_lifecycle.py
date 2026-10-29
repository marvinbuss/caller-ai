"""Deterministic call-lifecycle contracts; not live model or phone acceptance."""

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

SESSION_ID = "session-lifecycle-fixture"
PROVIDER_CALL_REF = "provider-lifecycle-fixture"
REVISION_ID = "revision-lifecycle-fixture"
WELCOME = "welcome-fixture"
ROUTING = "routing-fixture"
GOODBYE = "goodbye-fixture"
BUSINESS = "business-fixture"
SENSITIVE = "synthetic-volatile-content-not-for-persistence"

CONFIGURATION = {
    "revisionId": REVISION_ID,
    "agents": [
        {
            "agentId": agent_id,
            "name": agent_id,
            "prompt": "Synthetic fixture only",
            "enabled": True,
            "role": role,
            "communityId": community,
        }
        for agent_id, role, community in [
            (WELCOME, "welcome", None),
            (ROUTING, "routing", None),
            (GOODBYE, "goodbye", None),
            (BUSINESS, "ordinary", "community-fixture"),
        ]
    ],
    "communities": [
        {
            "communityId": "community-fixture",
            "name": "Fixture community",
            "routingDescription": "Synthetic routing only",
            "enabled": True,
            "entryAgentId": BUSINESS,
        }
    ],
    "relationships": [
        {
            "relationshipId": f"path-{index}",
            "sourceAgentId": source,
            "targetAgentId": target,
            "allowedContextFields": ["request_summary", "transfer_reason"],
        }
        for index, (source, target) in enumerate(
            [
                (WELCOME, ROUTING),
                (ROUTING, BUSINESS),
                (BUSINESS, ROUTING),
                (WELCOME, GOODBYE),
                (ROUTING, GOODBYE),
                (BUSINESS, GOODBYE),
            ]
        )
    ],
    "tools": [],
    "mcpConnections": [],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class ClockDouble:
    value: float = 0.0

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@dataclass
class RepositoryDouble:
    """Conditional in-memory metadata port, not proof of Cosmos consistency."""

    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    created: int = 0
    terminal_writes: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def create(self, metadata: dict[str, Any]) -> bool:
        async with self.lock:
            session_id = metadata["sessionId"]
            if session_id in self.records:
                return False
            self.records[session_id] = copy.deepcopy(metadata)
            self.created += 1
            return True

    async def read(self, session_id: str) -> dict[str, Any] | None:
        value = self.records.get(session_id)
        return copy.deepcopy(value) if value is not None else None

    async def replace(self, session_id: str, metadata: dict[str, Any]) -> None:
        async with self.lock:
            previous = self.records[session_id]
            if previous["state"] == "Ended":
                raise RuntimeError("terminal_metadata_is_immutable")
            self.records[session_id] = copy.deepcopy(metadata)
            if metadata["state"] == "Ended":
                self.terminal_writes += 1


@dataclass
class AgentDouble:
    clock: ClockDouble
    calls: list[tuple[str, str, float]] = field(default_factory=list)
    finished: list[tuple[str, float]] = field(default_factory=list)
    block_agent: str | None = None
    fail_agent: str | None = None
    costs: dict[str, float] = field(default_factory=dict)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    running: dict[str, asyncio.Task] = field(default_factory=dict)
    tool_calls: int = 0

    async def run(self, agent_id: str, *, session_id: str, context: Any, deadline: float):
        self.calls.append((agent_id, session_id, deadline))
        cost = self.costs.get(agent_id, 0)
        remaining = max(0, deadline - self.clock.monotonic())
        self.clock.advance(min(cost, remaining))
        if cost > remaining:
            raise TimeoutError("synthetic_agent_deadline")
        if self.fail_agent == agent_id:
            raise RuntimeError("synthetic-agent-failure-details")
        if self.block_agent == agent_id:
            task = asyncio.current_task()
            assert task is not None
            self.running[session_id] = task
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            finally:
                self.running.pop(session_id, None)
        self.finished.append((agent_id, self.clock.monotonic()))
        return BUSINESS if agent_id == ROUTING else None

    async def cancel(self, session_id: str) -> None:
        task = self.running.get(session_id)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def execute_tool(self, *arguments, **keywords):
        self.tool_calls += 1
        raise AssertionError("No business tool may run in these lifecycle fixtures")


@dataclass
class PhoneDouble:
    clock: ClockDouble
    hangups: list[tuple[str, float]] = field(default_factory=list)
    clears: list[tuple[str, float]] = field(default_factory=list)

    async def clear(self, provider_call_ref: str) -> None:
        self.clears.append((provider_call_ref, self.clock.monotonic()))

    async def hangup(self, provider_call_ref: str) -> bool:
        self.hangups.append((provider_call_ref, self.clock.monotonic()))
        return True


@pytest.fixture
def lifecycle():
    from caller_runtime.domain.call_lifecycle import CallLifecycle

    clock = ClockDouble()
    repository = RepositoryDouble()
    agents = AgentDouble(clock)
    phone = PhoneDouble(clock)
    coordinator = CallLifecycle(
        repository=repository,
        agents=agents,
        phone=phone,
        configuration=copy.deepcopy(CONFIGURATION),
        monotonic=clock.monotonic,
    )
    return coordinator, repository, agents, phone, clock


async def admit(coordinator) -> bool:
    return await coordinator.admit(
        session_id=SESSION_ID,
        provider_call_ref=PROVIDER_CALL_REF,
        event_id="event-lifecycle-fixture",
        revision_id=REVISION_ID,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_admission_creates_one_pinned_session(lifecycle, concurrent: bool) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    if concurrent:
        results = await asyncio.gather(admit(coordinator), admit(coordinator))
    else:
        results = [await admit(coordinator), await admit(coordinator)]
    assert results.count(True) == 1
    assert repository.created == 1 and len(repository.records) == 1
    metadata = repository.records[SESSION_ID]
    assert metadata["sessionId"] == SESSION_ID
    assert metadata["providerCallRef"] == PROVIDER_CALL_REF
    assert metadata["configurationRevisionId"] == REVISION_ID
    assert metadata["state"] == "Admitted"


@pytest.mark.anyio
async def test_welcome_precedes_routing_and_business_agent(lifecycle) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    await coordinator.start(SESSION_ID)
    assert [call[0] for call in agents.calls] == [WELCOME, ROUTING, BUSINESS]
    assert all(call[1] == SESSION_ID for call in agents.calls)
    assert repository.records[SESSION_ID]["state"] == "Active"
    assert repository.records[SESSION_ID]["configurationRevisionId"] == REVISION_ID


@pytest.mark.anyio
async def test_normal_closure_speaks_goodbye_and_hangs_up_once(lifecycle) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    await coordinator.start(SESSION_ID)
    agents.costs[GOODBYE] = 6
    await asyncio.gather(
        coordinator.close(SESSION_ID, reason="completed"),
        coordinator.close(SESSION_ID, reason="completed"),
    )
    assert [call[0] for call in agents.calls] == [WELCOME, ROUTING, BUSINESS, GOODBYE]
    assert len(phone.hangups) == 1 and phone.hangups[0][0] == PROVIDER_CALL_REF
    goodbye_finished = next(
        finished for agent_id, finished in agents.finished if agent_id == GOODBYE
    )
    assert 0 <= phone.hangups[0][1] - goodbye_finished <= 5
    assert repository.terminal_writes == 1
    assert repository.records[SESSION_ID]["state"] == "Ended"


@pytest.mark.anyio
@pytest.mark.parametrize("agent_id", [WELCOME, ROUTING, BUSINESS])
async def test_disconnect_during_work_cancels_without_spoken_goodbye(lifecycle, agent_id) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    agents.block_agent = agent_id
    work = asyncio.create_task(coordinator.start(SESSION_ID))
    try:
        await asyncio.wait_for(agents.started.wait(), timeout=1)
        await asyncio.wait_for(
            asyncio.gather(
                coordinator.disconnect(SESSION_ID),
                coordinator.disconnect(SESSION_ID),
            ),
            timeout=1,
        )
        assert agents.cancelled.is_set()
        assert GOODBYE not in [call[0] for call in agents.calls]
        assert len(phone.hangups) == 1
        assert repository.terminal_writes == 1
        assert repository.records[SESSION_ID]["state"] == "Ended"
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.anyio
async def test_interruption_cancels_generation_and_clears_playback_within_one_second(
    lifecycle,
) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    agents.block_agent = BUSINESS
    work = asyncio.create_task(coordinator.start(SESSION_ID))
    try:
        await asyncio.wait_for(agents.started.wait(), timeout=1)
        started = time.monotonic()
        await asyncio.wait_for(coordinator.interrupt(SESSION_ID), timeout=1)
        assert time.monotonic() - started < 1
        assert agents.cancelled.is_set()
        assert len(phone.clears) == 1 and phone.clears[0][0] == PROVIDER_CALL_REF
        assert not phone.hangups
        assert repository.records[SESSION_ID]["state"] == "Active"
        agents.block_agent = None
        assert await coordinator.handle_input(SESSION_ID, "synthetic-new-caller-turn") is True
        assert agents.calls[-1][:2] == (BUSINESS, SESSION_ID)
        assert (
            coordinator.context_for(SESSION_ID).conversation[-1]["content"]
            == "synthetic-new-caller-turn"
        )
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("terminal", ["close", "disconnect"])
async def test_terminal_session_cannot_restart_speech_or_actions(lifecycle, terminal) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    await coordinator.start(SESSION_ID)
    if terminal == "close":
        await coordinator.close(SESSION_ID, reason="completed")
    else:
        await coordinator.disconnect(SESSION_ID)
    calls = list(agents.calls)
    assert await coordinator.handle_input(SESSION_ID, "synthetic-late-input") is False
    with pytest.raises(PermissionError):
        await coordinator.execute_tool(SESSION_ID, "synthetic-tool", {})
    assert agents.calls == calls and agents.tool_calls == 0
    assert len(phone.hangups) == 1 and repository.terminal_writes == 1


@pytest.mark.anyio
async def test_failure_uses_one_absolute_ten_second_budget(lifecycle) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    agents.costs = {WELCOME: 6, ROUTING: 9}
    agents.fail_agent = ROUTING
    started = clock.monotonic()
    assert await coordinator.start(SESSION_ID) is False
    assert clock.monotonic() - started <= 10
    assert len(agents.calls) >= 2
    assert all(call[2] == started + 10 for call in agents.calls)
    assert repository.records[SESSION_ID]["state"] == "Ended"
    assert repository.terminal_writes == 1 and len(phone.hangups) == 1
    assert phone.hangups[0][1] - started <= 10
    assert "synthetic-agent-failure-details" not in json.dumps(repository.records)


@pytest.mark.anyio
@pytest.mark.parametrize("agent_id", [WELCOME, ROUTING, BUSINESS])
async def test_agent_failure_terminates_once_with_safe_metadata(lifecycle, agent_id) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    agents.fail_agent = agent_id
    started = clock.monotonic()
    assert await coordinator.start(SESSION_ID) is False
    assert clock.monotonic() - started <= 10
    assert repository.records[SESSION_ID]["state"] == "Ended"
    assert repository.terminal_writes == 1 and len(phone.hangups) == 1
    assert "synthetic-agent-failure-details" not in json.dumps(repository.records)


@pytest.mark.anyio
@pytest.mark.parametrize("terminal", ["close", "disconnect", "failure"])
async def test_volatile_content_is_purged_without_durable_serialization(
    lifecycle, terminal
) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    await coordinator.start(SESSION_ID)
    context = coordinator.context_for(SESSION_ID)
    audio = bytearray(SENSITIVE.encode())
    context.audio_buffers.append(audio)
    context.conversation.append({"role": "user", "content": SENSITIVE})
    context.request_summary = SENSITIVE
    context.transfer_reason = SENSITIVE
    context.tool_results.append({"synthetic": SENSITIVE})
    assert SENSITIVE not in json.dumps(repository.records)
    terminal_started = clock.monotonic()
    if terminal == "close":
        await coordinator.close(SESSION_ID, reason="completed")
    elif terminal == "disconnect":
        await coordinator.disconnect(SESSION_ID)
    else:
        agents.fail_agent = BUSINESS
        assert await coordinator.handle_input(SESSION_ID, "synthetic-failing-turn") is False
    clock.advance(300)
    await coordinator.purge_expired()
    assert clock.monotonic() - terminal_started <= 300
    assert context.audio_buffers == [] and context.conversation == []
    assert context.request_summary == "" and context.transfer_reason == ""
    assert context.tool_results == []
    assert SENSITIVE.encode() not in audio
    assert SENSITIVE not in json.dumps(repository.records)
    assert repository.records[SESSION_ID]["state"] == "Ended"


def session_metadata(**changes):
    from datetime import UTC, datetime, timedelta

    from caller_runtime.domain.session import CallSession

    started = datetime(2026, 10, 23, tzinfo=UTC)
    values = {
        "sessionId": SESSION_ID,
        "providerCallRef": PROVIDER_CALL_REF,
        "configurationRevisionId": REVISION_ID,
        "physicalOwnerId": "owner-fixture",
        "connectionEpoch": 1,
        "agentEpoch": 0,
        "state": "Admitted",
        "startedAt": started,
        "leaseUntil": started + timedelta(seconds=30),
    }
    return CallSession.model_validate({**values, **changes})


def test_session_document_contains_only_allowlisted_metadata() -> None:
    session = session_metadata()
    document = session.to_document()
    assert document["id"] == document["sessionId"] == SESSION_ID
    assert document["state"] == "Admitted"
    assert document["configurationRevisionId"] == REVISION_ID
    assert set(document) == {
        "id",
        "sessionId",
        "providerCallRef",
        "configurationRevisionId",
        "physicalOwnerId",
        "connectionEpoch",
        "agentEpoch",
        "activeAgentId",
        "activeCommunityId",
        "state",
        "leaseUntil",
        "startedAt",
        "terminatedAt",
        "terminalReasonCode",
    }
    assert "_etag" not in document
    assert SENSITIVE not in json.dumps(document)


@pytest.mark.parametrize("field_name", ["audio", "transcript", "summary", "toolPayload", "context"])
def test_session_metadata_rejects_content_fields(field_name: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        session_metadata(**{field_name: SENSITIVE})


@pytest.mark.parametrize(
    "changes",
    [
        {"connectionEpoch": 0},
        {"agentEpoch": -1},
        {"state": "unknown"},
        {"startedAt": "2026-10-23T00:00:00"},
        {"state": "Ended"},
        {"terminatedAt": "2026-10-23T00:00:01Z", "terminalReasonCode": "completed"},
    ],
)
def test_invalid_session_metadata_is_rejected(changes) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        session_metadata(**changes)


def test_session_transitions_and_terminal_immutability() -> None:
    from datetime import UTC, datetime, timedelta

    from pydantic import ValidationError

    from caller_runtime.domain.session import LifecycleState

    session = session_metadata()
    original = session.to_document()
    for state in ("Introducing", "Routing", "Active", "Transferring", "Active", "Closing"):
        session = session.transition(LifecycleState(state))
    ended = session.transition(
        LifecycleState.ENDED,
        at=datetime(2026, 10, 23, tzinfo=UTC) + timedelta(seconds=20),
        terminal_reason="completed",
    )
    assert ended.state == LifecycleState.ENDED and ended.terminal_reason_code == "completed"
    with pytest.raises(ValueError, match="invalid_session_transition"):
        ended.transition(LifecycleState.ACTIVE)
    with pytest.raises(ValidationError):
        ended.state = LifecycleState.ACTIVE
    assert original["state"] == "Admitted"


@pytest.mark.parametrize("target", ["Active", "Closing", "Transferring"])
def test_illegal_admission_transition_is_rejected(target: str) -> None:
    from caller_runtime.domain.session import LifecycleState

    with pytest.raises(ValueError, match="invalid_session_transition"):
        session_metadata().transition(LifecycleState(target))


def test_volatile_context_is_not_json_serializable_and_purges_retained_audio() -> None:
    from caller_runtime.domain.session import VolatileCallContext

    context = VolatileCallContext()
    audio = bytearray(SENSITIVE.encode())
    context.audio_buffers.append(audio)
    context.conversation.append({"role": "user", "content": SENSITIVE})
    context.request_summary = SENSITIVE
    context.transfer_reason = SENSITIVE
    context.tool_results.append({"result": SENSITIVE})
    assert SENSITIVE not in repr(context)
    with pytest.raises(TypeError):
        json.dumps(context)
    context.purge()
    context.purge()
    assert context.audio_buffers == [] and context.conversation == [] and context.tool_results == []
    assert context.request_summary == context.transfer_reason == ""
    assert SENSITIVE.encode() not in audio


def test_session_timestamps_are_normalized_to_utc() -> None:
    from datetime import UTC, datetime, timedelta, timezone

    offset = timezone(timedelta(hours=2))
    session = session_metadata(
        startedAt=datetime(2026, 10, 23, 2, tzinfo=offset),
        leaseUntil=datetime(2026, 10, 23, 2, 0, 30, tzinfo=offset),
    )
    assert session.started_at.tzinfo == UTC
    assert session.lease_until.tzinfo == UTC
    assert session.to_document()["startedAt"].endswith("Z")


@pytest.mark.anyio
async def test_concurrent_start_with_yielding_store_does_not_duplicate_speech(lifecycle) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    original = repository.read

    async def yielding_read(session_id):
        snapshot = await original(session_id)
        await asyncio.sleep(0)
        return snapshot

    repository.read = yielding_read
    results = await asyncio.gather(coordinator.start(SESSION_ID), coordinator.start(SESSION_ID))
    assert results.count(True) == 1
    assert [call[0] for call in agents.calls] == [WELCOME, ROUTING, BUSINESS]


@pytest.mark.anyio
async def test_input_racing_terminal_cleanup_does_not_restore_volatile_content(lifecycle) -> None:
    coordinator, repository, agents, phone, clock = lifecycle
    assert await admit(coordinator)
    await coordinator.start(SESSION_ID)
    original = repository.read
    captured = asyncio.Event()
    resume = asyncio.Event()
    input_task = None

    async def delayed_input_read(session_id):
        snapshot = await original(session_id)
        if asyncio.current_task() is input_task:
            captured.set()
            await resume.wait()
        return snapshot

    repository.read = delayed_input_read
    context = coordinator.context_for(SESSION_ID)
    input_task = asyncio.create_task(coordinator.handle_input(SESSION_ID, SENSITIVE))
    try:
        await asyncio.wait_for(captured.wait(), timeout=1)
        await asyncio.wait_for(coordinator.disconnect(SESSION_ID), timeout=1)
        resume.set()
        assert await asyncio.wait_for(input_task, timeout=1) is False
        assert context.conversation == []
        assert repository.records[SESSION_ID]["state"] == "Ended"
    finally:
        resume.set()
        input_task.cancel()
        await asyncio.gather(input_task, return_exceptions=True)
