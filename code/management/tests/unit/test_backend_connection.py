"""The console reaches the backend only through the two public endpoints, built in one place."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.contract.test_browser_tests import permit_document

ORIGIN = "runtime.fixture.example"


def test_text_request_is_what_any_plain_client_sends():
    from management_app.adapters.backend_connection import compose_request

    request = compose_request(
        "text",
        origin=ORIGIN,
        token="synthetic-token",
        signed_permit="signed-permit",
        request_id="request-1",
        text="Synthetic question",
    )

    assert request.url == "https://runtime.fixture.example/v1/conversations/text"
    assert request.headers == {
        "Authorization": "Bearer synthetic-token",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    assert request.subprotocols == ()
    assert request.message == {
        "permit": "signed-permit",
        "requestId": "request-1",
        "text": "Synthetic question",
    }


def test_text_follow_up_carries_only_the_conversation_id_and_text():
    from management_app.adapters.backend_connection import compose_request

    request = compose_request(
        "text",
        origin=ORIGIN,
        token="t",
        signed_permit="p",
        request_id="request-2",
        text="Follow-up",
        conversation_id="conversation-1",
    )

    assert set(request.message) == {"permit", "requestId", "text", "conversationId"}
    assert "history" not in request.message


def test_voice_request_is_an_upgrade_with_the_voice_subprotocol_and_an_authenticate_message():
    from management_app.adapters.backend_connection import compose_request

    request = compose_request(
        "voice",
        origin=ORIGIN,
        token="synthetic-token",
        signed_permit="signed-permit",
        request_id="request-1",
    )

    assert request.url == "wss://runtime.fixture.example/v1/conversations/voice"
    assert request.headers == {"Authorization": "Bearer synthetic-token"}
    assert request.subprotocols == ("caller.voice.v1",)
    assert request.message == {
        "type": "authenticate",
        "permit": "signed-permit",
        "requestId": "request-1",
    }


@pytest.mark.parametrize("kind", ["text", "voice"])
def test_settings_are_omitted_or_empty_and_never_anything_else(kind):
    from management_app.adapters.backend_connection import compose_request

    arguments = dict(
        origin=ORIGIN,
        token="t",
        signed_permit="p",
        request_id="r",
        text="x" if kind == "text" else None,
    )

    assert "settings" not in compose_request(kind, **arguments).message
    assert compose_request(kind, settings={}, **arguments).message["settings"] == {}
    for forbidden in ({"revision_id": "x"}, {"identity": "other"}, {"capability": "any"}, {"a": 1}):
        with pytest.raises(ValueError, match="^settings_not_supported$"):
            compose_request(kind, settings=forbidden, **arguments)


def test_the_builder_refuses_a_voice_request_that_carries_text_or_a_conversation():
    from management_app.adapters.backend_connection import compose_request

    with pytest.raises(ValueError):
        compose_request(
            "voice", origin=ORIGIN, token="t", signed_permit="p", request_id="r", text="x"
        )
    with pytest.raises(ValueError):
        compose_request("text", origin=ORIGIN, token="t", signed_permit="p", request_id="r")


def test_each_turn_gets_a_fresh_single_use_permit_derived_from_the_opened_one():
    from management_app.adapters.backend_connection import TurnPermits
    from management_app.models.test_session import StartPermit

    opened = StartPermit.model_validate_json(json.dumps(permit_document() | {"mode": "text"}))
    clock = [opened.issued_at]
    signed = []
    signer = SimpleNamespace(sign=lambda claims: signed.append(claims) or "signed-" + claims.nonce)
    permits = TurnPermits(signer, lambda: clock[0])

    first, raw_first = permits.mint(opened)
    clock[0] += timedelta(seconds=4)
    second, raw_second = permits.mint(opened)

    assert first.nonce != second.nonce != opened.nonce
    assert first.request_id != second.request_id != opened.request_id
    assert raw_first != raw_second
    for claims in (first, second):
        assert claims.mode == "text" and claims.actor_id == opened.actor_id
        assert claims.management_session_digest == opened.management_session_digest
        assert claims.expires_epoch - claims.issued_epoch <= 30
        assert claims.admission_deadline_utc - claims.admission_started_at == timedelta(seconds=10)
        assert claims.auth_expires_at == opened.auth_expires_at
    assert second.issued_at == first.issued_at + timedelta(seconds=4)


class Browser:
    """A browser socket double that submits the given texts, waits for each turn, then stops."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.displayed = []
        self.closed = None
        self._turns = 0
        self._finished = asyncio.Event()
        self._index = 0
        self._session = None

    async def receive(self):
        self._index += 1
        if self._index > 1:
            await self._finished.wait()
            self._finished.clear()
        if self.texts:
            value = {"type": "text.submit", "text": self.texts.pop(0)}
        else:
            value = {"type": "stop"}
        return {
            "type": "websocket.receive",
            "text": json.dumps(value | {"sessionId": self._session, "sequence": self._index}),
        }

    async def send_text(self, raw):
        value = json.loads(raw)
        self.displayed.append(value)
        if value["type"] == "ready":
            self._session = value["sessionId"]
        if value["type"] in {"text.completed", "error"}:
            self._finished.set()

    async def close(self, code, reason):
        self.closed = (code, reason)
        self._finished.set()


def run_text_session(handler, texts, *, validate=None):
    from management_app.adapters.backend_connection import TurnPermits
    from management_app.adapters.test_session_client import TestSessionClient
    from management_app.adapters.text_relay import ActiveRevision
    from management_app.models.test_session import StartPermit

    permit = StartPermit.model_validate_json(json.dumps(permit_document() | {"mode": "text"}))
    requests, tokens = (
        [],
        AsyncMock(return_value=SimpleNamespace(token="synthetic-workload-token", expires_on=1e12)),
    )

    async def dispatch(request):
        requests.append(request)
        return await handler(request)

    async def revision():
        return ActiveRevision("published-fixture", 7, "welcome")

    clock = [permit.issued_at]
    signer = SimpleNamespace(sign=lambda claims: "signed-" + claims.request_id)
    browser = Browser(texts)
    client = TestSessionClient(
        endpoint="https://runtime.fixture.example",
        audience="11111111-2222-4333-8444-555555555555",
        approved_hosts=frozenset({"runtime.fixture.example"}),
        credential=SimpleNamespace(get_token=tokens),
        utc_now=lambda: clock[0],
        monotonic=lambda: 2.0,
        max_clock_skew_seconds=0,
        permits=TurnPermits(signer, lambda: clock[0]),
        active_revision=revision,
        http_transport=httpx.MockTransport(dispatch),
    )

    async def scenario():
        await asyncio.wait_for(
            client.run(
                browser,
                permit=permit,
                signed_permit="synthetic-ticket-permit",
                validate_current=validate
                or (lambda: SimpleNamespace(expires_at=permit.auth_expires_at.timestamp())),
                deadline=asyncio.get_running_loop().time() + 8,
            ),
            5,
        )
        await client.close()

    asyncio.run(scenario())
    return browser, requests, tokens


def sse(*events):
    body = "".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n" for kind, data in events)
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body)


def reply(conversation="conversation-1", text="Synthetic reply"):
    return sse(
        ("conversation", {"conversationId": conversation}),
        ("text.delta", {"messageId": "message-1", "text": text}),
        ("text.completed", {"messageId": "message-1", "text": ""}),
    )


def test_text_session_relays_each_turn_as_a_public_post_and_keeps_only_the_conversation_id():
    async def handler(request):
        return reply()

    browser, requests, tokens = run_text_session(handler, ["First question", "Second question"])

    assert [value["type"] for value in browser.displayed] == [
        "ready",
        "text.delta",
        "text.completed",
        "text.delta",
        "text.completed",
        "ended",
    ]
    ready = browser.displayed[0]
    assert ready["mode"] == "text" and ready["revisionId"] == "published-fixture"
    assert ready["revisionNumber"] == 7 and ready["agentId"] == "welcome"
    assert {request.url.path for request in requests} == {"/v1/conversations/text"}
    assert all(request.method == "POST" for request in requests)
    bodies = [json.loads(request.content) for request in requests]
    assert set(bodies[0]) == {"permit", "requestId", "text"}
    assert set(bodies[1]) == {"permit", "requestId", "text", "conversationId"}
    assert bodies[1]["conversationId"] == "conversation-1"
    assert bodies[0]["text"] == "First question" and "settings" not in bodies[0]
    assert all(
        request.headers["authorization"] == "Bearer synthetic-workload-token"
        for request in requests
    )
    assert browser.closed == (1000, "stopped") and tokens.await_count == 2


def test_each_turn_uses_a_fresh_permit_that_never_reaches_the_browser():
    async def handler(request):
        return reply()

    browser, requests, _tokens = run_text_session(handler, ["First", "Second"])

    permits = [json.loads(request.content)["permit"] for request in requests]
    assert len(set(permits)) == 2 and "synthetic-ticket-permit" not in permits
    shown = json.dumps(browser.displayed)
    assert all(permit not in shown for permit in permits)
    assert "synthetic-ticket-permit" not in shown and "synthetic-workload-token" not in shown
    assert all("conversation" not in value["type"] for value in browser.displayed)


@pytest.mark.parametrize(
    "status,body,code,reason",
    [
        (
            503,
            {"error": {"code": "capacity_unavailable", "reason": "manager_busy"}},
            "capacity_unavailable",
            "manager_busy",
        ),
        (
            502,
            {"error": {"code": "upstream_unavailable", "reason": "history_unavailable"}},
            "upstream_unavailable",
            "history_unavailable",
        ),
        (
            413,
            {"error": {"code": "limit_exceeded", "reason": "conversation_context_limit"}},
            "limit_exceeded",
            "conversation_context_limit",
        ),
        (422, {"error": {"code": "settings_not_supported"}}, "settings_not_supported", None),
    ],
)
def test_backend_error_codes_surface_unchanged(status, body, code, reason):
    async def handler(request):
        return httpx.Response(status, json=body)

    browser, requests, _tokens = run_text_session(handler, ["Question"])

    if code == "settings_not_supported":
        # Nothing can be done in this session: the console ends it with the same code.
        assert browser.closed == (1008, code)
        return
    failures = [value for value in browser.displayed if value["type"] == "error"]
    assert len(failures) == 1
    assert failures[0]["code"] == code and failures[0].get("reason") == reason
    assert failures[0]["cleanupStatus"] == "active"
    assert browser.displayed[-1]["type"] == "ended"


def test_unauthorized_backend_response_ends_the_console_session():
    async def handler(request):
        return httpx.Response(
            401, json={"error": {"code": "unauthorized", "reason": "permit_invalid"}}
        )

    browser, _requests, _tokens = run_text_session(handler, ["Question"])

    assert browser.closed == (1008, "unauthorized")
    assert not any(value["type"] in {"text.delta", "text.completed"} for value in browser.displayed)


def test_error_event_after_the_stream_started_is_relayed_with_its_public_code():
    async def handler(request):
        return sse(
            ("conversation", {"conversationId": "conversation-1"}),
            ("text.delta", {"messageId": "message-1", "text": "partial"}),
            ("error", {"code": "upstream_unavailable", "reason": "history_unavailable"}),
        )

    browser, _requests, _tokens = run_text_session(handler, ["Question"])

    kinds = [value["type"] for value in browser.displayed]
    assert kinds == ["ready", "text.delta", "error", "ended"]
    assert browser.displayed[2]["code"] == "upstream_unavailable"
    assert browser.displayed[2]["reason"] == "history_unavailable"


@pytest.mark.parametrize(
    "events",
    [
        [("provider.debug", {"body": "synthetic-private-provider-body"})],
        [
            (
                "text.delta",
                {"messageId": "m", "text": "x", "extra": "synthetic-private-provider-body"},
            )
        ],
        [("conversation", {"conversationId": "bad id with spaces"})],
    ],
)
def test_malformed_backend_stream_ends_the_session_without_relaying_content(events):
    async def handler(request):
        return sse(*events)

    browser, _requests, _tokens = run_text_session(handler, ["Question"])

    assert browser.closed is not None and browser.closed[0] == 1008
    assert "synthetic-private" not in json.dumps(browser.displayed)


def test_lost_console_session_ends_the_text_session_before_any_request():
    valid = [True]

    async def handler(request):
        return reply()

    def validate():
        if not valid[0]:
            return None
        return SimpleNamespace(expires_at=1e12)

    valid[0] = False
    browser, requests, tokens = run_text_session(handler, ["Question"], validate=validate)

    assert not requests and tokens.await_count == 0
    assert browser.closed is not None and browser.closed[0] == 1008
