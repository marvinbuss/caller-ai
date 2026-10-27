"""Deterministic local journeys, not eligible real phone/model call acceptance."""

import json
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_two_hundred_local_journeys_have_one_pinned_lifecycle_and_metadata_only_audit(
    community_configuration,
):
    from caller_runtime.adapters.audit_repository import LocalAuditRepository
    from caller_runtime.adapters.session_repository import LocalSessionRepository
    from caller_runtime.domain.audit import AuditRecord
    from caller_runtime.domain.call_lifecycle import CallLifecycle

    class Agents:
        calls = {}

        async def run(self, agent_id, *, session_id, context, deadline):
            self.calls.setdefault(session_id, []).append(agent_id)
            if agent_id == "routing":
                return "a-entry"
            return None

        async def cancel(self, session_id):
            pass

    class Phone:
        hangups = []

        async def clear(self, provider_call_ref):
            pass

        async def hangup(self, provider_call_ref):
            self.hangups.append(provider_call_ref)
            return True

    repository, audits, agents, phone = (
        LocalSessionRepository(),
        LocalAuditRepository(),
        Agents(),
        Phone(),
    )
    lifecycle = CallLifecycle(
        repository=repository, agents=agents, phone=phone, configuration=community_configuration
    )
    for index in range(200):
        session_id, provider_ref = f"journey-{index}", f"provider-{index}"
        assert await lifecycle.admit(
            session_id=session_id,
            provider_call_ref=provider_ref,
            event_id=f"event-{index}",
            revision_id=community_configuration["revisionId"],
        )
        assert await lifecycle.start(session_id)
        assert await lifecycle.handle_input(session_id, "synthetic-private-caller-content")
        context = lifecycle.context_for(session_id)
        context.request_summary = "synthetic-private-summary"
        retained = bytearray(b"synthetic-private-audio")
        context.audio_buffers.append(retained)
        await lifecycle.close(session_id, reason="completed")
        metadata = await repository.read(session_id)
        assert metadata["state"] == "Ended" and metadata["terminalReasonCode"] == "completed"
        assert metadata["configurationRevisionId"] == community_configuration["revisionId"]
        assert (
            agents.calls[session_id][0] == "welcome" and agents.calls[session_id][-1] == "goodbye"
        )
        assert (
            agents.calls[session_id].count("welcome")
            == agents.calls[session_id].count("goodbye")
            == 1
        )
        assert (
            not context.conversation and not context.request_summary and not context.audio_buffers
        )
        assert retained == bytearray(len(retained))
        timestamp = datetime.now(UTC)
        record = AuditRecord(
            id=f"audit-{index}",
            timestamp=timestamp,
            dayBucket=timestamp.date().isoformat(),
            eventName="call.ended",
            outcome="success",
            sessionRef=session_id,
            revisionId=metadata["configurationRevisionId"],
            correlationId=f"correlation-{index}",
            reasonCode="completed",
        )
        assert await audits.append(record)
        assert "synthetic-private" not in json.dumps(metadata)
    assert len(phone.hangups) == len(set(phone.hangups)) == len(audits.records) == 200
    assert "synthetic-private" not in json.dumps(audits.records)
