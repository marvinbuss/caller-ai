"""Provider-neutral call transport contracts, not qualification of a phone provider."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

AUTHORIZATION = {"Authorization": "Bearer synthetic-transport-fixture"}
SESSION_ID = "session-fixture"
PROVIDER_CALL_REF = "provider-call-fixture"
MEDIA_PATH = f"/v1/calls/{SESSION_ID}/media"
CALLBACK_PATH = "/v1/provider/callbacks"


@dataclass
class PhoneAdapterDouble:
    """Opaque wire events and identities; no JWT implementation or network access."""

    rejection: str | None = None
    media_call_ref: str = PROVIDER_CALL_REF
    codec: str = "fixture-codec"
    hangup_acknowledged: bool = True
    fail_decode: bool = False
    callback_verifications: int = 0
    media_verifications: int = 0
    decoded_frames: int = 0
    answers: list[str] = field(default_factory=list)
    hangups: list[str] = field(default_factory=list)

    def authenticate(self, headers: Mapping[str, str]) -> None:
        if headers.get("authorization") != AUTHORIZATION["Authorization"] or self.rejection:
            raise PermissionError(self.rejection or "invalid_identity")

    async def verify_callback(self, headers: Mapping[str, str], body: bytes) -> dict[str, str]:
        self.callback_verifications += 1
        self.authenticate(headers)
        if body != b"opaque-callback-fixture":
            raise ValueError("invalid_fixture_event")
        return {
            "event_id": "event-fixture",
            "session_id": SESSION_ID,
            "provider_call_ref": PROVIDER_CALL_REF,
            "codec": self.codec,
            "type": "incoming",
        }

    async def verify_media(self, headers: Mapping[str, str], session_id: str) -> str:
        self.media_verifications += 1
        self.authenticate(headers)
        return self.media_call_ref

    def supports_codec(self, codec: str) -> bool:
        return codec == "fixture-codec"

    async def answer(self, provider_call_ref: str) -> None:
        self.answers.append(provider_call_ref)

    async def decode_frame(self, frame: str | bytes) -> dict[str, str | bytes]:
        self.decoded_frames += 1
        if self.fail_decode:
            raise RuntimeError("sensitive-adapter-failure")
        if frame == b"fixture-stop":
            return {"type": "stop"}
        if frame == b"fixture-policy-failure":
            raise ValueError("invalid_fixture_frame")
        if isinstance(frame, bytes) and frame.startswith(b"fixture-audio"):
            return {"type": "audio", "audio": b"synthetic-audio"}
        raise ValueError("invalid_fixture_frame")

    async def hangup(self, provider_call_ref: str) -> bool:
        self.hangups.append(provider_call_ref)
        return self.hangup_acknowledged


@dataclass
class AdmissionDouble:
    """Conditional local admission double; never durable ownership evidence."""

    admitted: bool = False
    provider_call_ref: str = PROVIDER_CALL_REF
    codec: str = "fixture-codec"
    events: set[str] = field(default_factory=set)
    admissions: int = 0
    ended: int = 0

    async def admit(self, event: Mapping[str, str]) -> bool:
        if event["event_id"] in self.events or self.admitted:
            return False
        self.events.add(event["event_id"])
        self.admitted = True
        self.admissions += 1
        return True

    async def read(self, session_id: str) -> dict[str, str] | None:
        if not self.admitted or session_id != SESSION_ID:
            return None
        return {
            "session_id": SESSION_ID,
            "provider_call_ref": self.provider_call_ref,
            "codec": self.codec,
            "state": "admitted",
        }

    async def end(self, session_id: str) -> None:
        self.ended += 1


@pytest.fixture
def provider_app() -> tuple[FastAPI, PhoneAdapterDouble, AdmissionDouble]:
    from caller_runtime.api.provider_webhooks import create_router as callback_router

    phone = PhoneAdapterDouble()
    admissions = AdmissionDouble()
    app = FastAPI()
    app.include_router(callback_router(provider=phone, admission=admissions))
    return app, phone, admissions


@pytest.fixture
def callbacks(provider_app) -> Iterator[tuple[TestClient, PhoneAdapterDouble, AdmissionDouble]]:
    app, phone, admissions = provider_app
    with TestClient(app) as client:
        yield client, phone, admissions


@pytest.fixture
def transport(provider_app) -> Iterator[tuple[TestClient, PhoneAdapterDouble, AdmissionDouble]]:
    from caller_runtime.api.media import create_router as media_router

    app, phone, admissions = provider_app
    app.include_router(media_router(provider=phone, admission=admissions))
    with TestClient(app) as client:
        yield client, phone, admissions


def admit(client: TestClient) -> None:
    response = client.post(CALLBACK_PATH, content=b"opaque-callback-fixture", headers=AUTHORIZATION)
    assert 200 <= response.status_code < 300


@pytest.mark.parametrize("acknowledged", [True, False])
def test_explicit_audio_router_observes_physical_calls_until_verified_closure(acknowledged):
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from caller_runtime.api.media import MediaOutput, create_router
    from caller_runtime.metrics import PhysicalCallObservations, RuntimeMetrics

    class Admission(AdmissionDouble):
        async def read(self, session_id):
            value = await super().read(session_id)
            return (
                {
                    **value,
                    "physical_owner_id": "owner-observed",
                    "connection_epoch": 1,
                    "agent_epoch": 1,
                }
                if value is not None
                else None
            )

    class Phone(PhoneAdapterDouble):
        async def decode_frame(self, frame):
            value = await super().decode_frame(frame)
            if value.get("type") == "audio":
                value["duration_ms"] = 20
            return value

    class Audio:
        async def receive(self, session_id, audio, metadata):
            return (MediaOutput(b"synthetic-observed-output", "owner-observed", 1, 1),)

        async def dispose(self, session_id, *, deadline):
            return None

    reader = InMemoryMetricReader()
    metrics = RuntimeMetrics(mode="local", reader=reader)
    observations = PhysicalCallObservations(metrics=metrics)
    phone, admission = Phone(hangup_acknowledged=acknowledged), Admission(admitted=True)
    application = FastAPI()
    application.include_router(
        create_router(provider=phone, admission=admission, audio=Audio(), observations=observations)
    )

    def values():
        document = reader.get_metrics_data()
        assert document is not None
        return {
            metric.name: metric.data.data_points
            for resource in document.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }

    try:
        with TestClient(application) as client:
            with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION) as websocket:
                websocket.send_bytes(b"fixture-audio")
                assert websocket.receive_bytes() == b"synthetic-observed-output"
                assert values()["caller.active.calls"][0].value == 1
                websocket.send_bytes(b"fixture-stop")
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_bytes()
            assert values()["caller.active.calls"][0].value == (0 if acknowledged else 1)
            assert "caller.speech.delay" not in values()
            assert SESSION_ID not in repr(reader.get_metrics_data())
        assert phone.hangups == [PROVIDER_CALL_REF]
    finally:
        metrics.close()


@pytest.mark.parametrize("rejection", [None, "issuer", "audience", "signature", "expired"])
def test_invalid_provider_callback_is_denied_before_admission(
    callbacks, rejection: str | None
) -> None:
    client, phone, admissions = callbacks
    phone.rejection = rejection
    headers = AUTHORIZATION if rejection is not None else {}
    response = client.post(CALLBACK_PATH, content=b"opaque-callback-fixture", headers=headers)
    assert response.status_code in {401, 403}
    assert phone.callback_verifications == 1
    assert admissions.admissions == 0 and phone.answers == []
    assert AUTHORIZATION["Authorization"] not in response.text


def test_duplicate_callback_does_not_answer_or_admit_twice(callbacks) -> None:
    client, phone, admissions = callbacks
    admit(client)
    admit(client)
    assert phone.callback_verifications == 2
    assert admissions.admissions == 1
    assert phone.answers == [PROVIDER_CALL_REF]


@pytest.mark.parametrize(
    "case", ["missing_identity", "unadmitted", "wrong_call", "unknown_session"]
)
def test_media_is_rejected_with_http_before_upgrade(transport, case: str) -> None:
    client, phone, admissions = transport
    admissions.admitted = case != "unadmitted"
    headers = {} if case == "missing_identity" else AUTHORIZATION
    if case == "wrong_call":
        phone.media_call_ref = "other-provider-call"
    path = MEDIA_PATH if case != "unknown_session" else "/v1/calls/unknown/media"
    with pytest.raises(WebSocketDenialResponse) as denied:
        with client.websocket_connect(path, headers=headers):
            pytest.fail("Untrusted media was upgraded")
    assert denied.value.status_code == 403
    assert phone.decoded_frames == 0
    assert admissions.ended == 0


def test_query_string_credential_cannot_authenticate_media(transport) -> None:
    client, phone, admissions = transport
    admissions.admitted = True
    with pytest.raises(WebSocketDenialResponse) as denied:
        with client.websocket_connect(MEDIA_PATH + "?token=synthetic-transport-fixture"):
            pytest.fail("Query-string credential authenticated media")
    assert denied.value.status_code == 403
    assert phone.decoded_frames == 0


def test_unsupported_callback_codec_is_rejected_before_admission(callbacks) -> None:
    client, phone, admissions = callbacks
    phone.codec = "unsupported-fixture-codec"
    response = client.post(CALLBACK_PATH, content=b"opaque-callback-fixture", headers=AUTHORIZATION)
    assert response.status_code in {400, 415, 422}
    assert admissions.admissions == 0 and phone.answers == []
    assert phone.decoded_frames == 0


def test_unsupported_media_codec_is_rejected_before_acceptance(transport) -> None:
    client, phone, admissions = transport
    admissions.admitted = True
    admissions.codec = "unsupported-fixture-codec"
    with pytest.raises(WebSocketDenialResponse) as denied:
        with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION):
            pytest.fail("Unsupported codec was upgraded")
    assert denied.value.status_code == 403
    assert phone.decoded_frames == 0


@pytest.mark.parametrize(
    "body,status",
    [(b"invalid-sensitive-callback", 400), (b"x" * 65537, 413)],
    ids=["invalid", "oversize"],
)
def test_callback_payload_failure_is_safe_and_has_no_admission(callbacks, body, status) -> None:
    client, phone, admissions = callbacks
    response = client.post(CALLBACK_PATH, content=body, headers=AUTHORIZATION)
    assert response.status_code == status
    assert admissions.admissions == 0 and phone.answers == []
    assert "invalid-sensitive-callback" not in response.text
    if status == 413:
        assert phone.callback_verifications == 0


@pytest.mark.parametrize(
    "frame,code",
    [
        (b"fixture-policy-failure", 1008),
        (b"x" * 65537, 1009),
        ("\u00e9" * 32769, 1009),
    ],
    ids=["policy", "binary_oversize", "utf8_oversize"],
)
def test_post_upgrade_policy_and_encoded_frame_limits(transport, frame, code: int) -> None:
    client, phone, admissions = transport
    admissions.admitted = True
    with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION) as websocket:
        if isinstance(frame, bytes):
            websocket.send_bytes(frame)
        else:
            websocket.send_text(frame)
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_text()
        assert closed.value.code == code
    if code == 1009:
        assert phone.decoded_frames == 0


def test_internal_adapter_failure_closes_safely(transport) -> None:
    client, phone, admissions = transport
    admissions.admitted = True
    phone.fail_decode = True
    with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION) as websocket:
        websocket.send_bytes(b"fixture-audio")
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_text()
        assert closed.value.code == 1011
        assert "sensitive-adapter-failure" not in closed.value.reason


@pytest.mark.parametrize("acknowledged,code", [(True, 1000), (False, 1011)])
def test_normal_phone_closure_requires_provider_hangup_acknowledgment(
    transport,
    acknowledged: bool,
    code: int,
) -> None:
    client, phone, admissions = transport
    admissions.admitted = True
    phone.hangup_acknowledged = acknowledged
    with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION) as websocket:
        websocket.send_bytes(b"fixture-stop")
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_text()
        assert closed.value.code == code
    assert phone.hangups == [PROVIDER_CALL_REF]
    assert admissions.ended == 1


def test_exact_encoded_frame_limit_is_accepted(transport) -> None:
    client, phone, admissions = transport
    admissions.admitted = True
    frame = b"fixture-audio" + b" " * (65536 - len(b"fixture-audio"))
    with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION) as websocket:
        websocket.send_bytes(frame)
        websocket.send_bytes(b"fixture-stop")
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_text()
        assert closed.value.code == 1000
    assert phone.decoded_frames == 2
    assert phone.hangups == [PROVIDER_CALL_REF]


def test_second_media_connection_cannot_compete_with_existing_socket(transport) -> None:
    client, phone, admissions = transport
    admissions.admitted = True
    with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION) as first:
        with pytest.raises(WebSocketDenialResponse) as denied:
            with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION):
                pytest.fail("duplicate_media_owner_accepted")
        assert denied.value.status_code == 403 and admissions.ended == 0
        first.send_bytes(b"fixture-stop")
        with pytest.raises(WebSocketDisconnect) as closed:
            first.receive_text()
        assert closed.value.code == 1000
    assert phone.hangups == [PROVIDER_CALL_REF] and admissions.ended == 1


@pytest.mark.parametrize("kind", ["too-long", "decoded-oversize", "obsolete-output"])
def test_audio_port_is_bounded_and_obsolete_epoch_output_never_reaches_phone(
    provider_app, monkeypatch, kind
):
    from caller_runtime.api.media import MediaOutput, create_router

    application, phone, admissions = provider_app
    admissions.admitted = True
    epoch = [1]

    async def current(session_id):
        return {
            "session_id": session_id,
            "provider_call_ref": PROVIDER_CALL_REF,
            "codec": "fixture-codec",
            "state": "active",
            "physical_owner_id": "owner-fixture",
            "connection_epoch": 1,
            "agent_epoch": epoch[0],
        }

    async def decode(frame):
        if frame == b"fixture-stop":
            return {"type": "stop"}
        return {
            "type": "audio",
            "audio": b"x" * (65537 if kind == "decoded-oversize" else 10),
            "duration_ms": 201 if kind == "too-long" else 200,
        }

    class Audio:
        calls = 0
        disposed = False

        async def receive(self, session_id, audio, metadata):
            self.calls += 1
            epoch[0] = 2
            return (MediaOutput(b"synthetic-private-old-audio", "owner-fixture", 1, 1),)

        async def dispose(self, session_id, *, deadline):
            self.disposed = True

    handler = Audio()
    monkeypatch.setattr(admissions, "read", current)
    monkeypatch.setattr(phone, "decode_frame", decode)
    application.include_router(create_router(provider=phone, admission=admissions, audio=handler))
    with TestClient(application) as client:
        with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION) as websocket:
            websocket.send_bytes(b"fixture-audio")
            if kind == "obsolete-output":
                websocket.send_bytes(b"fixture-stop")
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_bytes()
            assert closed.value.code == (1000 if kind == "obsolete-output" else 1008)
    assert handler.calls == (1 if kind == "obsolete-output" else 0)
    assert handler.disposed


def test_blocked_decode_uses_original_failure_budget_and_still_ends_once(provider_app, monkeypatch):
    import asyncio

    from caller_runtime.api.media import create_router

    application, phone, admissions = provider_app
    admissions.admitted = True

    async def blocked(frame):
        await asyncio.Event().wait()
        return {"type": "stop"}

    monkeypatch.setattr(phone, "decode_frame", blocked)
    application.include_router(
        create_router(provider=phone, admission=admissions, operation_budget=0.1)
    )
    with TestClient(application) as client:
        with client.websocket_connect(MEDIA_PATH, headers=AUTHORIZATION) as websocket:
            websocket.send_bytes(b"fixture-audio")
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_text()
            assert closed.value.code == 1011
    assert phone.hangups == [PROVIDER_CALL_REF] and admissions.ended == 1


@pytest.mark.parametrize(
    "field,value", [("provider_call_ref", "other-call"), ("codec", "other-codec")]
)
def test_callback_cannot_rebind_an_existing_call(callbacks, field: str, value: str) -> None:
    client, phone, admissions = callbacks
    admissions.admitted = True
    setattr(admissions, field, value)
    response = client.post(CALLBACK_PATH, content=b"opaque-callback-fixture", headers=AUTHORIZATION)
    assert response.status_code == 403
    assert phone.answers == [] and admissions.admissions == 0


def test_callback_answer_failure_is_not_reported_as_success(callbacks, monkeypatch) -> None:
    client, phone, admissions = callbacks

    async def fail_answer(provider_call_ref):
        raise RuntimeError("sensitive-answer-failure")

    monkeypatch.setattr(phone, "answer", fail_answer)
    response = client.post(CALLBACK_PATH, content=b"opaque-callback-fixture", headers=AUTHORIZATION)
    assert response.status_code == 503
    assert admissions.admissions == 1 and admissions.ended == 1
    assert "sensitive-answer-failure" not in response.text
