"""Contract of `POST /v1/conversations/text`: one turn per request, streamed as server-sent events.

The tests run the composed runtime (permit verification, admission, the model runner and the
history repository) against controlled doubles; nothing reaches a network or a cloud service.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.doubles import ContainerDouble, parse_sse
from tests.integration.test_conversation import reviewed_runtime_options as reviewed_runtime_options
from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store
from tests.unit.test_admission import signed_test_permit as signed_test_permit
from tests.unit.test_conversation import text_execution_case as text_execution_case

PATH = "/v1/conversations/text"
WORKLOAD = {"Authorization": "Bearer synthetic-management-workload"}
REPLY = "Synthetic shared streamed reply"


@pytest.fixture
def endpoint(
    reviewed_runtime_options,
    signed_test_permit,
    admission_case,
    text_execution_case,
    runtime_revision_v2,
):
    import httpx

    from caller_runtime.composition import ConversationRuntime, RuntimeOptions
    from caller_runtime.main import create_app

    public, sign, clock, assertion = signed_test_permit
    service, repository, container, controlled_clock, permit, cache = admission_case
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    history = ContainerDouble(partition_field="ownerKey")

    class Credentials:
        async def get_token(self, scope):
            return SimpleNamespace(token="synthetic-endpoint-token", expires_on=time.time() + 900)

        async def close(self):
            pass

    class Identity:
        async def require_runtime(self, request):
            if request.headers.get("authorization") != WORKLOAD["Authorization"]:
                raise HTTPException(401, "synthetic-private-auth")
            return "management-workload-fixture"

    async def configuration(request):
        return httpx.Response(
            200, json=runtime_revision_v2(), headers={"etag": '"current-fixture"'}
        )

    async def containers(name):
        return {"call-sessions": container, "text-conversations": history}[name]

    options = RuntimeOptions(
        **(
            reviewed_runtime_options
            | {"manager_issuer": assertion.issuer, "manager_audience": assertion.audience}
        )
    )
    runtime = ConversationRuntime(
        options=options,
        owner_id="runtime-fixture-owner",
        container_factory=containers,
        credential_factory=AsyncMock(return_value=Credentials()),
        workload_identity=Identity(),
        verification_keys={"manager-fixture-key": public},
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        configuration_transport=httpx.MockTransport(configuration),
        text_transport=adapter._transport,
    )

    async def initialize():
        await repository.initialize(
            now=clock.utc_now(), deadline=asyncio.get_running_loop().time() + 1
        )

    asyncio.run(initialize())
    application = create_app(runtime=runtime)

    def body(number, actor="manager-fixture", mode=None, **fields):
        changes = {
            "actorId": actor,
            "requestId": f"request-{number}",
            "nonce": f"{number:064x}",
            "managementSessionDigest": f"{number:064x}",
        }
        if mode is not None:
            changes["mode"] = mode
        return {
            "permit": sign(changes),
            "requestId": f"request-{number}",
            "text": "Synthetic question",
        } | fields

    with TestClient(application, base_url="https://runtime.fixture.example") as client:

        def post(number, *, headers=WORKLOAD, actor="manager-fixture", mode=None, **fields):
            return client.post(PATH, headers=headers, json=body(number, actor, mode, **fields))

        yield SimpleNamespace(
            client=client,
            post=post,
            body=body,
            history=history,
            requests=requests,
            controls=controls,
            release=release,
            runtime=runtime,
            repository=repository,
            clock=clock,
            permit=permit,
            service=service,
            application=application,
        )


def error_of(response):
    return response.json()["error"]


def authority_slots(endpoint):
    async def read():
        return (
            await endpoint.repository.read(deadline=asyncio.get_running_loop().time() + 1)
        ).authority.slots

    return asyncio.run(read())


def test_turn_streams_conversation_then_deltas_then_completion(endpoint):
    response = endpoint.post(1)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    events = parse_sse(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "conversation" and kinds[-1] == "text.completed"
    assert set(kinds) <= {"conversation", "agent.changed", "text.delta", "text.completed"}
    assert events[0][1]["conversationId"]
    assert "".join(data["text"] for kind, data in events if kind == "text.delta") == REPLY
    assert events[-1][1]["text"] == ""
    from caller_runtime.services.modality_defaults import TEXT_PLATFORM_RULES

    # Welcome never runs a request: it hands over to Routing first (FR-030).
    expected = TEXT_PLATFORM_RULES + "\n\nSynthetic routing prompt"
    assert endpoint.requests[0]["instructions"] == expected


def test_progress_events_stream_during_a_turn_that_still_ends_with_completion(endpoint):
    endpoint.controls["wire_actions"] = [
        ("report_progress", {"state": "still_working", "message": "Checking the order status."}),
        ("report_progress", {"state": "completed", "message": "The order was found."}),
    ]
    events = parse_sse(endpoint.post(1).text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "conversation" and kinds[-1] == "text.completed"
    progress = [data for kind, data in events if kind == "progress"]
    message_id = events[-1][1]["messageId"]
    assert progress == [
        {"messageId": message_id, "state": "still_working", "text": "Checking the order status."},
        {"messageId": message_id, "state": "completed", "text": "The order was found."},
    ]
    # Updates come before the answer text of the same turn.
    assert kinds.index("progress") < kinds.index("text.delta")


def test_new_conversation_is_stored_for_its_owner_only_with_item_expiry(endpoint):
    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    record = endpoint.history.records[identifier]
    assert record["id"] == identifier and len(record["ownerKey"]) == 64
    assert record["ttl"] == 2_592_000 and record["historyVersion"] == 2
    assert kinds(record) == ["user_text", "agent_answer"]
    assert record["entries"][0]["content"] == "Synthetic question"


def kinds(record):
    return [entry["kind"] for entry in record["entries"]]


def test_follow_up_continues_from_the_stored_history(endpoint):
    identifier = parse_sse(endpoint.post(1, text="Synthetic first question").text)[0][1][
        "conversationId"
    ]
    second = endpoint.post(2, conversationId=identifier, text="Synthetic second question")
    assert second.status_code == 200
    assert parse_sse(second.text)[0][1]["conversationId"] == identifier
    sent = json.dumps(endpoint.requests[1]["input"])
    assert "Synthetic first question" in sent and REPLY in sent
    assert "Synthetic second question" in sent
    record = endpoint.history.records[identifier]
    assert len(endpoint.history.records) == 1 and len(record["entries"]) == 4
    assert record["ttl"] == 2_592_000


def test_a_later_request_resumes_with_the_tool_and_handover_entries(endpoint):
    # Routing (after Welcome's move) hands off to beta's entry, which reports progress.
    endpoint.controls["wire_actions"] = [
        ("handover_1", {}),
        ("report_progress", {"state": "completed", "message": "The order was found."}),
    ]
    identifier = parse_sse(endpoint.post(1, text="Synthetic order question").text)[0][1][
        "conversationId"
    ]
    record = endpoint.history.records[identifier]
    assert kinds(record) == [
        "user_text",
        "handover",
        "tool_call",
        "tool_result",
        "progress",
        "agent_answer",
    ]
    assert record["activeAgentId"] == "b-entry" and record["activeCommunityId"] == "beta"
    second = endpoint.post(2, conversationId=identifier, text="Synthetic follow-up")
    assert second.status_code == 200
    # The resumed agent sees the earlier handover, tool call and result (US1 AC4).
    sent = endpoint.requests[-1]["input"]
    calls = [item["name"] for item in sent if item.get("type") == "function_call"]
    outputs = [item["output"] for item in sent if item.get("type") == "function_call_output"]
    assert calls == ["handover_1", "report_progress"]
    assert outputs == ["applied", '"reported"']
    assert "Status (completed): The order was found." in json.dumps(sent)
    expected = endpoint.requests[-1]["instructions"]
    assert expected.endswith("Synthetic prompt for b-entry")


def test_a_conversation_stored_before_version_2_starts_a_new_one_with_the_greeting(endpoint):
    old = parse_sse(endpoint.post(1, text="Synthetic old question").text)[0][1]["conversationId"]
    record = endpoint.history.records[old]
    for key in ("historyVersion", "entries", "pendingDestination"):
        record.pop(key)
    record["turns"] = [{"role": "user", "content": "Synthetic old question"}]
    before = json.dumps(record, sort_keys=True)
    endpoint.controls["greeting"] = "Hello and welcome again."
    response = endpoint.post(2, conversationId=old, text="Synthetic new question")
    assert response.status_code == 200
    events = parse_sse(response.text)
    # The stream's first event carries the new conversation id (FR-025).
    assert events[0][0] == "conversation" and events[0][1]["conversationId"] != old
    identifier = events[0][1]["conversationId"]
    texts = [data["text"] for kind, data in events if kind == "text.delta"]
    assert texts[0] == "Hello and welcome again." and "".join(texts[1:]) == REPLY
    assert "Synthetic old question" not in json.dumps(endpoint.requests[-1]["input"])
    assert kinds(endpoint.history.records[identifier]) == [
        "agent_answer",
        "user_text",
        "agent_answer",
    ]
    # The old document is left to its expiry.
    assert json.dumps(endpoint.history.records[old], sort_keys=True) == before


def test_a_stored_agent_that_was_removed_restarts_at_welcome_with_all_entries(endpoint):
    identifier = parse_sse(endpoint.post(1, text="Synthetic earlier fact").text)[0][1][
        "conversationId"
    ]
    endpoint.history.records[identifier]["activeAgentId"] = "a-disabled"
    endpoint.history.records[identifier]["activeCommunityId"] = "alpha"
    endpoint.controls["greeting"] = "Unused greeting"
    response = endpoint.post(2, conversationId=identifier, text="Synthetic next question")
    assert response.status_code == 200
    events = parse_sse(response.text)
    # Welcome moves straight to Routing without a new greeting (FR-030), entries kept.
    assert not endpoint.controls.get("greeting_requests")
    assert [data["agentId"] for kind, data in events if kind == "agent.changed"] == ["routing"]
    sent = json.dumps(endpoint.requests[-1]["input"])
    assert "Synthetic earlier fact" in sent and "Synthetic next question" in sent
    assert endpoint.requests[-1]["instructions"].endswith("Synthetic routing prompt")
    assert len(endpoint.history.records[identifier]["entries"]) == 4


def test_unknown_expired_and_foreign_ids_fail_identically(endpoint):
    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    foreign = endpoint.post(2, actor="manager-other", conversationId=identifier)
    unknown = endpoint.post(3, conversationId="a" * 32)
    stored = endpoint.history.records.pop(identifier)
    expired = endpoint.post(4, conversationId=identifier)
    assert stored["id"] == identifier
    for response in (foreign, unknown, expired):
        assert response.status_code == 400
        assert response.json() == {
            "error": {"code": "invalid_request", "reason": "conversation_not_found"}
        }
    assert len(endpoint.requests) == 1


def test_missing_workload_credential_and_invalid_permit_are_unauthorized(endpoint):
    missing = endpoint.post(1, headers={})
    assert missing.status_code == 401 and error_of(missing)["reason"] == "permit_invalid"
    tampered = endpoint.client.post(
        PATH, headers=WORKLOAD, json=endpoint.body(2) | {"permit": "not-a-signed-permit"}
    )
    assert tampered.status_code == 401 and error_of(tampered)["reason"] == "permit_invalid"
    assert "synthetic-private" not in missing.text + tampered.text
    assert not endpoint.requests and not endpoint.history.records


def test_replayed_permit_is_refused(endpoint):
    document = endpoint.body(1)
    assert endpoint.client.post(PATH, headers=WORKLOAD, json=document).status_code == 200
    replay = endpoint.client.post(PATH, headers=WORKLOAD, json=document)
    assert replay.status_code == 401 and error_of(replay)["reason"] == "permit_replayed"
    assert len(endpoint.requests) == 1


def test_voice_permit_is_forbidden_on_the_text_endpoint(endpoint):
    response = endpoint.post(1, mode="speech")
    assert response.status_code == 403
    assert response.json() == {"error": {"code": "forbidden", "reason": "mode_mismatch"}}
    assert not endpoint.requests and not endpoint.history.records


def test_plain_http_and_query_strings_are_refused(endpoint):
    insecure = TestClient(endpoint.application, base_url="http://runtime.fixture.example")
    refused = insecure.post(PATH, headers=WORKLOAD, json=endpoint.body(1))
    assert refused.status_code == 403 and error_of(refused)["reason"] == "insecure_transport"
    queried = endpoint.client.post(PATH + "?debug=1", headers=WORKLOAD, json=endpoint.body(2))
    assert queried.status_code == 403
    assert not endpoint.requests


def test_one_slot_is_taken_per_request_and_released_after_cleanup(endpoint):
    assert endpoint.post(1).status_code == 200
    assert not authority_slots(endpoint)
    assert endpoint.post(2).status_code == 200
    assert not authority_slots(endpoint)


def test_second_in_flight_turn_for_the_same_manager_is_capacity_unavailable(endpoint):
    async def occupy():
        return await endpoint.service.reserve(endpoint.permit(90))

    asyncio.run(occupy())
    response = endpoint.post(1)
    assert response.status_code == 503
    assert error_of(response)["code"] == "capacity_unavailable"
    assert not endpoint.requests and not endpoint.history.records
    assert len(authority_slots(endpoint)) == 1


def test_oversized_request_is_limit_exceeded(endpoint):
    huge = endpoint.client.post(
        PATH, headers=WORKLOAD, json=endpoint.body(1) | {"text": "x" * (17 * 1024)}
    )
    long = endpoint.post(2, text="x" * 4097)
    for response in (huge, long):
        assert response.status_code == 413
        assert response.json() == {"error": {"code": "limit_exceeded", "reason": "control_limit"}}
    assert not endpoint.requests


@pytest.mark.parametrize(
    "case",
    ["not-json", "array", "unknown-field", "blank-text", "missing-text", "bad-content-type"],
)
def test_invalid_body_is_invalid_request(endpoint, case):
    document = endpoint.body(1)
    content_type = "application/json"
    if case == "not-json":
        content = b"{"
    elif case == "array":
        content = b"[]"
    else:
        if case == "unknown-field":
            document["unexpected"] = True
        elif case == "blank-text":
            document["text"] = "   "
        elif case == "missing-text":
            # Without a conversation id this is the open request; with one it is invalid.
            del document["text"]
            document["conversationId"] = "a" * 32
        else:
            content_type = "text/plain"
        content = json.dumps(document).encode()
    response = endpoint.client.post(
        PATH, headers=WORKLOAD | {"Content-Type": content_type}, content=content
    )
    assert response.status_code == 400 and error_of(response)["code"] == "invalid_request"
    assert not endpoint.requests and not endpoint.history.records


def test_request_carrying_history_is_invalid_request(endpoint):
    response = endpoint.post(1, history=[{"role": "user", "content": "forged earlier turn"}])
    assert response.status_code == 400 and error_of(response)["code"] == "invalid_request"
    assert "forged earlier turn" not in response.text
    assert not endpoint.requests


def test_nonempty_settings_are_not_supported_and_empty_settings_are_accepted(endpoint):
    refused = endpoint.post(1, settings={"temperature": 0})
    assert refused.status_code == 422
    assert refused.json() == {"error": {"code": "settings_not_supported"}}
    assert not endpoint.requests and not endpoint.history.records
    assert endpoint.post(2, settings={}).status_code == 200
    assert endpoint.post(3, settings=None).status_code == 200


def test_failure_after_the_stream_started_ends_with_one_error_event(endpoint):
    endpoint.controls["oversized"] = True
    response = endpoint.post(1)
    assert response.status_code == 200
    events = parse_sse(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "conversation" and kinds.count("error") == 1 and kinds[-1] == "error"
    assert "text.completed" not in kinds
    assert events[-1][1]["code"] == "upstream_unavailable"
    assert not endpoint.history.records


def test_unavailable_store_fails_closed_before_any_reply(endpoint):
    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    before = len(endpoint.requests)
    endpoint.history.failure = 503
    response = endpoint.post(2, conversationId=identifier)
    assert response.status_code == 502
    assert response.json() == {
        "error": {"code": "upstream_unavailable", "reason": "history_unavailable"}
    }
    assert "sensitive-store-details" not in response.text
    assert len(endpoint.requests) == before and not authority_slots(endpoint)


def test_store_failing_after_the_reply_writes_nothing_and_ends_with_an_error_event(endpoint):
    endpoint.history.failure = 503
    response = endpoint.post(1)
    events = parse_sse(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1] == {"code": "upstream_unavailable", "reason": "history_unavailable"}
    assert not endpoint.history.records and "sensitive-store-details" not in response.text
    assert not authority_slots(endpoint)


def test_conversation_beyond_the_context_limit_is_limit_exceeded(endpoint):
    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    record = endpoint.history.records[identifier]
    record["entries"] = [
        {"kind": "user_text" if index % 2 == 0 else "agent_answer", "content": "x" * 65000}
        for index in range(4)
    ]
    response = endpoint.post(2, conversationId=identifier, text="y" * 4000)
    assert response.status_code == 413
    assert error_of(response) == {"code": "limit_exceeded", "reason": "conversation_context_limit"}
    assert len(endpoint.requests) == 1
    assert len(endpoint.history.records[identifier]["entries"]) == 4


@pytest.fixture
def small_limits(endpoint, monkeypatch):
    """The fixture model takes 64 KiB of input, so the condensing thresholds shrink with it."""
    from caller_runtime.services import condensing

    options = endpoint.runtime.options.model_copy(update={"max_input_tokens": 65536})
    monkeypatch.setattr(endpoint.runtime, "options", options)
    monkeypatch.setattr(condensing, "CONDENSE_ABOVE", 48 * 1024)
    monkeypatch.setattr(condensing, "CONDENSE_BELOW", 32 * 1024)


def grown(endpoint, identifier, turns):
    endpoint.history.records[identifier]["entries"] = [
        {"kind": kind, "content": f"{label} {turn} " + "x" * 4000}
        for turn in range(turns)
        for kind, label in (("user_text", "Request"), ("agent_answer", "Answer"))
    ]


def test_history_condensed_comes_before_the_answer_and_is_stored(endpoint, small_limits):
    # Feature 007, R11 and FR-016: older parts are condensed with a visible event.
    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    grown(endpoint, identifier, 8)
    response = endpoint.post(2, conversationId=identifier, text="And the next question?")
    events = parse_sse(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds.index("history.condensed") < kinds.index("text.delta")
    assert events[kinds.index("history.condensed")][1] == {}
    assert kinds[-1] == "text.completed"
    (condense,) = endpoint.controls["condense_requests"]
    assert "Request 0 " in json.dumps(condense["input"]) and not condense.get("tools")
    record = endpoint.history.records[identifier]
    assert record["entries"][0]["kind"] == "condensed"
    assert record["entries"][0]["content"] == "Synthetic condensed account"
    assert "Request 0 " not in json.dumps(record)
    assert record["entries"][-1]["kind"] == "agent_answer"


def test_when_condensing_fails_the_conversation_ends_with_the_limit(endpoint, small_limits):
    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    grown(endpoint, identifier, 8)
    before = json.dumps(endpoint.history.records[identifier], sort_keys=True)
    endpoint.controls["condense_status"] = 400
    response = endpoint.post(2, conversationId=identifier, text="And the next question?")
    events = parse_sse(response.text)
    assert events[-1] == (
        "error",
        {"code": "limit_exceeded", "reason": "conversation_context_limit"},
    )
    assert "text.delta" not in [kind for kind, _ in events]
    assert json.dumps(endpoint.history.records[identifier], sort_keys=True) == before


def test_a_document_over_its_limit_is_condensed_before_it_is_saved(
    endpoint, small_limits, monkeypatch
):
    from caller_runtime.services import text_conversation

    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    grown(endpoint, identifier, 4)
    # Below the condensing threshold for the model, but above the document limit.
    monkeypatch.setattr(text_conversation, "MAX_DOCUMENT_BYTES", 30 * 1024)
    response = endpoint.post(2, conversationId=identifier, text="And the next question?")
    kinds = [kind for kind, _ in parse_sse(response.text)]
    assert kinds.index("text.completed") < kinds.index("history.condensed")
    record = endpoint.history.records[identifier]
    assert record["entries"][0]["kind"] == "condensed"
    assert len(json.dumps(record).encode("utf-8")) < 32 * 1024


def test_update_conflict_is_capacity_unavailable_and_keeps_the_stored_conversation(endpoint):
    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    stored = json.dumps(endpoint.history.records[identifier])
    endpoint.history.conflict_on_replace = True
    response = endpoint.post(2, conversationId=identifier)
    events = parse_sse(response.text)
    assert events[-1][0] == "error"
    assert (
        events[-1][1]["code"] == "capacity_unavailable" and events[-1][1]["reason"] == "work_busy"
    )
    assert json.dumps(endpoint.history.records[identifier]) == stored


def open_conversation(endpoint, number, **fields):
    document = endpoint.body(number) | fields
    document.pop("text")
    return endpoint.client.post(PATH, headers=WORKLOAD, json=document)


def greeting_events(endpoint):
    return [
        event
        for event in endpoint.application.state.telemetry.events
        if event["event"] == "test.greeting"
    ]


def test_open_request_streams_the_welcome_greeting_and_stores_it_first(endpoint):
    from caller_runtime.services.greeting import TEXT_GREETING_INPUT

    greeting = "Hello and welcome to the service. How can I help you today?"
    endpoint.controls["greeting"] = greeting
    response = open_conversation(endpoint, 1)
    assert response.status_code == 200
    events = parse_sse(response.text)
    # The greeting, then Welcome hands over to Routing without a conversation row (FR-030).
    assert [kind for kind, _ in events] == [
        "conversation",
        "text.delta",
        "text.completed",
        "agent.changed",
    ]
    assert events[3][1]["agentId"] == "routing"
    identifier = events[0][1]["conversationId"]
    assert events[1][1]["text"] == greeting and events[1][1]["messageId"]
    assert events[2][1] == {"messageId": events[1][1]["messageId"], "text": ""}
    asked = endpoint.controls["greeting_requests"]
    assert len(asked) == 1 and asked[0]["input"] == TEXT_GREETING_INPUT
    assert not endpoint.requests
    record = endpoint.history.records[identifier]
    # The greeting is the first entry, an answer of the Welcome agent (FR-002).
    assert [(e["kind"], e["content"], e["agentId"]) for e in record["entries"]] == [
        ("agent_answer", greeting, "welcome")
    ]
    assert record["activeAgentId"] == "routing"
    events = greeting_events(endpoint)
    assert [(e["outcome"], e["reason_code"], e["kind"]) for e in events] == [
        ("success", "ready", "text")
    ]
    assert float(events[0]["value"]) <= 5 and greeting not in json.dumps(events)
    assert not authority_slots(endpoint)
    follow_up = endpoint.post(2, conversationId=identifier, text="Synthetic question")
    assert follow_up.status_code == 200
    sent = endpoint.requests[0]["input"]
    assert sent[0]["role"] == "assistant" and greeting in json.dumps(sent[0])
    assert kinds(endpoint.history.records[identifier]) == [
        "agent_answer",
        "user_text",
        "agent_answer",
    ]


def test_slow_greeting_falls_back_to_the_fixed_greeting(endpoint, monkeypatch):
    from caller_runtime.services import conversation

    monkeypatch.setattr(conversation, "TEXT_GREETING_SECONDS", 0.2)
    endpoint.controls["greeting"], endpoint.controls["greeting_block"] = "Too late", True
    response = open_conversation(endpoint, 1)
    events = parse_sse(response.text)
    assert [kind for kind, _ in events] == [
        "conversation",
        "text.delta",
        "text.completed",
        "agent.changed",
    ]
    assert events[1][1]["text"] == "Hello and welcome! How can I help you today?"
    identifier = events[0][1]["conversationId"]
    assert [
        (entry["kind"], entry["content"])
        for entry in endpoint.history.records[identifier]["entries"]
    ] == [("agent_answer", "Hello and welcome! How can I help you today?")]
    assert [(e["outcome"], e["reason_code"]) for e in greeting_events(endpoint)] == [
        ("success", "fallback")
    ]

    async def release():
        endpoint.release.set()

    # The cancelled request still finishes in the background before the slot is released.
    endpoint.client.portal.call(release)
    deadline = time.monotonic() + 5
    while authority_slots(endpoint) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not authority_slots(endpoint)


def test_text_without_a_conversation_starts_without_a_greeting(endpoint):
    endpoint.controls["greeting"] = "Unused greeting"
    events = parse_sse(endpoint.post(1).text)
    assert "".join(data["text"] for kind, data in events if kind == "text.delta") == REPLY
    assert not endpoint.controls.get("greeting_requests") and not greeting_events(endpoint)


def test_a_conversation_id_without_text_is_invalid(endpoint):
    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    response = open_conversation(endpoint, 2, conversationId=identifier)
    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"
    assert len(endpoint.history.records[identifier]["entries"]) == 2
