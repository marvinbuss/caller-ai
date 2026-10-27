"""Bounded local probe protocol, with no phone, model or tool behavior."""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from caller_runtime.main import create_app
from caller_runtime.settings import Settings


@pytest.mark.parametrize("request_id", ["probe-001", "A_z-09", "a" * 64])
def test_exact_acknowledgment_and_normal_disconnect(request_id: str) -> None:
    app = create_app(Settings())
    with TestClient(app) as client:
        with client.websocket_connect("/v1/dev/transport") as websocket:
            websocket.send_json({"type": "probe", "request_id": request_id})
            assert websocket.receive_json() == {
                "type": "probe_ack",
                "request_id": request_id,
                "protocol_version": 1,
            }
            websocket.close(code=1000)
        assert app.state.active_probes == 0


@pytest.mark.parametrize(
    "message",
    [
        "not-json",
        "[]",
        "1",
        '{"type":"other","request_id":"test"}',
        '{"type":"probe","request_id":""}',
        json.dumps({"type": "probe", "request_id": "a" * 65}),
        json.dumps({"type": "probe", "request_id": "caf\u00e9"}),
        json.dumps({"type": "probe", "request_id": "test", "extra": "secret"}),
    ],
)
def test_invalid_message_policy_close(message: str) -> None:
    with TestClient(create_app(Settings())) as client:
        with client.websocket_connect("/v1/dev/transport") as websocket:
            websocket.send_text(message)
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_text()
            assert closed.value.code == 1008


def test_binary_input_is_rejected() -> None:
    with TestClient(create_app(Settings())) as client:
        with client.websocket_connect("/v1/dev/transport") as websocket:
            websocket.send_bytes(b"binary-content")
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_text()
            assert closed.value.code == 1008


def test_encoded_size_boundary_and_oversize_close() -> None:
    payload = json.dumps({"type": "probe", "request_id": "boundary"})
    message = " " * (65536 - len(payload.encode("utf-8"))) + payload
    with TestClient(create_app(Settings())) as client:
        with client.websocket_connect("/v1/dev/transport") as websocket:
            websocket.send_text(message)
            assert websocket.receive_json()["request_id"] == "boundary"
            websocket.send_text(message + " ")
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_text()
            assert closed.value.code == 1009


def test_internal_failure_closes_without_exception_content() -> None:
    def broken_ack(request_id: str) -> dict[str, str | int]:
        raise RuntimeError("sensitive-internal-error")

    app = create_app(Settings(), probe_ack=broken_ack)
    with TestClient(app) as client:
        with client.websocket_connect("/v1/dev/transport") as websocket:
            websocket.send_json({"type": "probe", "request_id": "failure"})
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_text()
            assert closed.value.code == 1011
            assert "sensitive-internal-error" not in closed.value.reason
        assert app.state.active_probes == 0


def test_probe_is_not_registered_in_live_mode() -> None:
    app = create_app(Settings(mode="live"))
    assert all(getattr(route, "path", None) != "/v1/dev/transport" for route in app.routes)
