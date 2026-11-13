"""The console reaches the backend only through the two public endpoints, built in one place."""

import asyncio
import contextlib
import json
from datetime import timedelta
from functools import partial
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
        compose_request(
            "text",
            origin=ORIGIN,
            token="t",
            signed_permit="p",
            request_id="r",
            conversation_id="c1",
        )


def test_a_text_request_without_text_or_conversation_is_the_open_request():
    from management_app.adapters.backend_connection import compose_request

    request = compose_request("text", origin=ORIGIN, token="t", signed_permit="p", request_id="r")
    assert request.message == {"permit": "p", "requestId": "r"}


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


def greeting_reply(conversation="conversation-1", text="Synthetic greeting"):
    return sse(
        ("conversation", {"conversationId": conversation}),
        ("text.delta", {"messageId": "greeting-1", "text": text}),
        ("text.completed", {"messageId": "greeting-1", "text": ""}),
    )


def run_text_session(handler, texts, *, validate=None, idle_seconds=None, opening=None):
    from management_app.adapters.backend_connection import TurnPermits
    from management_app.adapters.test_session_client import TestSessionClient
    from management_app.adapters.text_relay import ActiveRevision
    from management_app.models.test_session import StartPermit

    permit = StartPermit.model_validate_json(json.dumps(permit_document() | {"mode": "text"}))
    requests, tokens = (
        [],
        AsyncMock(return_value=SimpleNamespace(token="synthetic-workload-token", expires_on=1e12)),
    )
    opened = []

    async def dispatch(request):
        # The relay opens every session with a request that has no text; it returns the greeting.
        if "text" not in json.loads(request.content):
            opened.append(request)
            return await opening(request) if opening is not None else greeting_reply()
        requests.append(request)
        return await handler(request)

    async def revision():
        if idle_seconds is not None:
            return ActiveRevision("published-fixture", 7, "welcome", idle_seconds)
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
    browser.opened = opened
    return browser, requests, tokens


def sse(*events):
    body = "".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n" for kind, data in events)
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body)


@pytest.mark.parametrize("idle_seconds,expected", [(None, 120), (45, 45), (120, 120)])
def test_text_relay_idle_timeout_comes_from_the_active_revision(idle_seconds, expected):
    from datetime import datetime, timedelta

    async def handler(request):
        return httpx.Response(500)

    browser, _requests, _tokens = run_text_session(handler, [], idle_seconds=idle_seconds)
    ready = next(message for message in browser.displayed if message["type"] == "ready")
    permit = json.loads(json.dumps(permit_document()))
    started = datetime.fromisoformat(permit["issuedAt"].replace("Z", "+00:00"))
    idle = datetime.fromisoformat(ready["idleExpiresAt"].replace("Z", "+00:00"))
    assert idle - started == timedelta(seconds=expected)


@pytest.mark.parametrize("steps", range(8))
def test_text_session_teardown_keeps_a_server_cancel_that_lands_during_it(steps):
    import anyio
    from starlette.websockets import WebSocketDisconnect

    from management_app.adapters.backend_connection import TurnPermits
    from management_app.adapters.test_session_client import TestSessionClient
    from management_app.adapters.text_relay import ActiveRevision
    from management_app.models.test_session import StartPermit

    permit = StartPermit.model_validate_json(json.dumps(permit_document() | {"mode": "text"}))
    scopes = []

    class LeavingBrowser(Browser):
        async def receive(self):
            # The server cancel lands `steps` loop steps after the browser leaves.
            loop = asyncio.get_running_loop()
            cancel = scopes[0].cancel
            for _ in range(steps):
                cancel = partial(loop.call_soon, cancel)
            loop.call_soon(cancel)
            raise WebSocketDisconnect(1001)

    async def revision():
        return ActiveRevision("published-fixture", 7, "welcome")

    browser = LeavingBrowser([])
    client = TestSessionClient(
        endpoint="https://runtime.fixture.example",
        audience="11111111-2222-4333-8444-555555555555",
        approved_hosts=frozenset({"runtime.fixture.example"}),
        credential=SimpleNamespace(get_token=AsyncMock()),
        utc_now=lambda: permit.issued_at,
        monotonic=lambda: 2.0,
        max_clock_skew_seconds=0,
        permits=TurnPermits(
            SimpleNamespace(sign=lambda claims: "signed"), lambda: permit.issued_at
        ),
        active_revision=revision,
        http_transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    async def scenario():
        # A bare CancelledError from teardown escapes the scope and ends the server's task group.
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            with contextlib.suppress(Exception):
                await client.run(
                    browser,
                    permit=permit,
                    signed_permit="synthetic-ticket-permit",
                    validate_current=lambda: SimpleNamespace(
                        expires_at=permit.auth_expires_at.timestamp()
                    ),
                    deadline=asyncio.get_running_loop().time() + 8,
                )
            await asyncio.Event().wait()

    anyio.run(scenario)
    assert scopes[0].cancelled_caught


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
        "text.delta",
        "text.completed",
        "ended",
    ]
    assert browser.displayed[1]["messageId"] == "greeting-1"
    ready = browser.displayed[0]
    assert ready["mode"] == "text" and ready["revisionId"] == "published-fixture"
    assert ready["revisionNumber"] == 7 and ready["agentId"] == "welcome"
    assert {request.url.path for request in requests + browser.opened} == {"/v1/conversations/text"}
    assert all(request.method == "POST" for request in requests)
    assert [set(json.loads(request.content)) for request in browser.opened] == [
        {"permit", "requestId"}
    ]
    bodies = [json.loads(request.content) for request in requests]
    # The open request started the conversation, so every user turn continues it.
    assert set(bodies[0]) == {"permit", "requestId", "text", "conversationId"}
    assert set(bodies[1]) == {"permit", "requestId", "text", "conversationId"}
    assert bodies[0]["conversationId"] == bodies[1]["conversationId"] == "conversation-1"
    assert bodies[0]["text"] == "First question" and "settings" not in bodies[0]
    assert all(
        request.headers["authorization"] == "Bearer synthetic-workload-token"
        for request in requests
    )
    assert browser.closed == (1000, "stopped") and tokens.await_count == 3


def test_the_open_request_comes_first_and_an_early_message_is_answered_right_after_it():
    order = []

    async def opening(request):
        # The user's message is already queued while the greeting is still being written.
        await asyncio.sleep(0.05)
        order.append("open")
        return greeting_reply(text="Hello and welcome!")

    async def handler(request):
        order.append(json.loads(request.content)["text"])
        return reply(text="Synthetic answer")

    browser, requests, _tokens = run_text_session(handler, ["Early question"], opening=opening)

    assert order == ["open", "Early question"]
    shown = [(value["type"], value.get("text")) for value in browser.displayed[1:-1]]
    assert shown == [
        ("text.delta", "Hello and welcome!"),
        ("text.completed", ""),
        ("text.delta", "Synthetic answer"),
        ("text.completed", ""),
    ]
    # The greeting is relayed unchanged, with the backend's message id.
    assert browser.displayed[1] == browser.displayed[1] | {
        "type": "text.delta",
        "messageId": "greeting-1",
        "text": "Hello and welcome!",
    }
    assert json.loads(requests[0].content)["conversationId"] == "conversation-1"


def test_a_failed_open_request_is_reported_and_later_turns_still_run():
    async def opening(request):
        return httpx.Response(
            503, json={"error": {"code": "capacity_unavailable", "reason": "manager_busy"}}
        )

    async def handler(request):
        return reply()

    browser, requests, _tokens = run_text_session(handler, ["Question"], opening=opening)

    kinds = [value["type"] for value in browser.displayed]
    assert kinds == ["ready", "error", "text.delta", "text.completed", "ended"]
    assert browser.displayed[1]["reason"] == "manager_busy"
    assert set(json.loads(requests[0].content)) == {"permit", "requestId", "text"}


def test_a_completed_markdown_reply_is_followed_by_one_formatted_tree():
    async def handler(request):
        return sse(
            ("conversation", {"conversationId": "conversation-1"}),
            ("text.delta", {"messageId": "message-1", "text": "- one\n"}),
            ("text.delta", {"messageId": "message-1", "text": "- two\n"}),
            ("text.completed", {"messageId": "message-1", "text": ""}),
        )

    browser, _requests, _tokens = run_text_session(handler, ["Question"])

    kinds = [value["type"] for value in browser.displayed]
    assert kinds[-4:] == ["text.delta", "text.completed", "text.formatted", "ended"]
    formatted = browser.displayed[-2]
    assert set(formatted) == {"type", "sessionId", "sequence", "generation", "messageId", "tree"}
    assert formatted["messageId"] == "message-1"
    assert formatted["tree"] == {
        "type": "root",
        "children": [
            {
                "type": "ul",
                "children": [
                    {"type": "li", "children": ["one"]},
                    {"type": "li", "children": ["two"]},
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "text", ["Just a plain sentence.", "- item\n" * 1100], ids=["plain", "over-limit"]
)
def test_no_formatted_tree_is_sent_for_plain_or_unformattable_replies(text):
    async def handler(request):
        return sse(
            ("conversation", {"conversationId": "conversation-1"}),
            *[
                ("text.delta", {"messageId": "message-1", "text": text[start : start + 4096]})
                for start in range(0, len(text), 4096)
            ],
            ("text.completed", {"messageId": "message-1", "text": ""}),
        )

    browser, _requests, _tokens = run_text_session(handler, ["Question"])

    assert "text.formatted" not in [value["type"] for value in browser.displayed]


def test_progress_is_relayed_with_exactly_message_state_and_text():
    async def handler(request):
        return sse(
            ("conversation", {"conversationId": "conversation-1"}),
            (
                "progress",
                {"messageId": "message-1", "state": "still_working", "text": "Checking."},
            ),
            ("text.delta", {"messageId": "message-1", "text": "Done."}),
            ("text.completed", {"messageId": "message-1", "text": ""}),
        )

    browser, _requests, _tokens = run_text_session(handler, ["Question"])

    progress = [value for value in browser.displayed if value["type"] == "progress"]
    assert len(progress) == 1
    assert set(progress[0]) == {
        "type",
        "sessionId",
        "sequence",
        "generation",
        "messageId",
        "state",
        "text",
    }
    assert (progress[0]["state"], progress[0]["text"]) == ("still_working", "Checking.")


@pytest.mark.parametrize(
    "data",
    [
        {"messageId": "message-1", "state": "unknown", "text": "Checking."},
        {"messageId": "message-1", "state": "completed", "text": ""},
        {"messageId": "message-1", "state": "completed", "text": "x" * 201},
        {"messageId": "message-1", "state": "completed", "text": "Done.", "extra": 1},
        {"messageId": "bad id", "state": "completed", "text": "Done."},
    ],
    ids=["state", "empty", "long", "extra", "identifier"],
)
def test_invalid_progress_is_refused_and_never_shown(data):
    async def handler(request):
        return sse(("conversation", {"conversationId": "conversation-1"}), ("progress", data))

    browser, _requests, _tokens = run_text_session(handler, ["Question"])

    assert "progress" not in [value["type"] for value in browser.displayed]


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
    assert not any(
        value["type"] in {"text.delta", "text.completed"} and value["messageId"] != "greeting-1"
        for value in browser.displayed
    )


def test_error_event_after_the_stream_started_is_relayed_with_its_public_code():
    async def handler(request):
        return sse(
            ("conversation", {"conversationId": "conversation-1"}),
            ("text.delta", {"messageId": "message-1", "text": "partial"}),
            ("error", {"code": "upstream_unavailable", "reason": "history_unavailable"}),
        )

    browser, _requests, _tokens = run_text_session(handler, ["Question"])

    kinds = [value["type"] for value in browser.displayed]
    assert kinds == ["ready", "text.delta", "text.completed", "text.delta", "error", "ended"]
    assert browser.displayed[4]["code"] == "upstream_unavailable"
    assert browser.displayed[4]["reason"] == "history_unavailable"


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


def test_one_console_session_sends_one_correlation_id_to_the_runtime_and_shows_it_in_its_own_end():
    """Principle VII: the runtime records the id the management app sends (its request event)."""
    import re

    from management_app.adapters.backend_connection import correlation_for

    async def handler(request):
        return reply()

    browser, requests, _tokens = run_text_session(handler, ["First", "Second"])
    expected = correlation_for(permit_document()["requestId"])
    assert re.fullmatch(r"[0-9a-f]{32}", expected)
    assert {request.headers["x-correlation-id"] for request in requests} == {expected}
    ended = [value for value in browser.displayed if value["type"] == "ended"]
    assert [value["correlationId"] for value in ended] == [expected]


def test_the_correlation_id_is_deterministic_per_request_and_opaque():
    from management_app.adapters.backend_connection import compose_request, correlation_for

    assert correlation_for("request-1") == correlation_for("request-1")
    assert correlation_for("request-1") != correlation_for("request-2")
    assert "request-1" not in correlation_for("request-1")
    request = compose_request(
        "text",
        origin=ORIGIN,
        token="t",
        signed_permit="p",
        request_id="r",
        text="x",
        correlation_id=correlation_for("request-1"),
    )
    assert request.headers["X-Correlation-ID"] == correlation_for("request-1")
