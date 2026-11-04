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
    assert set(kinds) <= {"conversation", "text.delta", "text.completed"}
    assert events[0][1]["conversationId"]
    assert "".join(data["text"] for kind, data in events if kind == "text.delta") == REPLY
    assert events[-1][1]["text"] == ""
    assert endpoint.requests[0]["messages"][0]["content"] == "Synthetic welcome prompt"


def test_new_conversation_is_stored_for_its_owner_only_with_item_expiry(endpoint):
    identifier = parse_sse(endpoint.post(1).text)[0][1]["conversationId"]
    record = endpoint.history.records[identifier]
    assert record["id"] == identifier and len(record["ownerKey"]) == 64
    assert record["ttl"] == 2_592_000
    assert [turn["role"] for turn in record["turns"]] == ["user", "assistant"]
    assert record["turns"][0]["content"] == "Synthetic question"


def test_follow_up_continues_from_the_stored_history(endpoint):
    identifier = parse_sse(endpoint.post(1, text="Synthetic first question").text)[0][1][
        "conversationId"
    ]
    second = endpoint.post(2, conversationId=identifier, text="Synthetic second question")
    assert second.status_code == 200
    assert parse_sse(second.text)[0][1]["conversationId"] == identifier
    sent = json.dumps(endpoint.requests[1]["messages"])
    assert "Synthetic first question" in sent and REPLY in sent
    assert "Synthetic second question" in sent
    record = endpoint.history.records[identifier]
    assert len(endpoint.history.records) == 1 and len(record["turns"]) == 4
    assert record["ttl"] == 2_592_000


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
            del document["text"]
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
    record["turns"] = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 65000}
        for index in range(4)
    ]
    response = endpoint.post(2, conversationId=identifier, text="y" * 4000)
    assert response.status_code == 413
    assert error_of(response) == {"code": "limit_exceeded", "reason": "conversation_context_limit"}
    assert len(endpoint.requests) == 1
    assert len(endpoint.history.records[identifier]["turns"]) == 4


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
