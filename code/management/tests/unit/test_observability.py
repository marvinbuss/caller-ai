"""Management owns its metadata-only telemetry and accepts safe trace context."""

import json

import pytest

from management_app.observability import Telemetry


def test_test_alert_thresholds_are_bounded_correlated_and_content_free():
    clock, delivered = [0.0], []
    telemetry = Telemetry(
        "management", "local", monotonic=lambda: clock[0], alert_sink=delivered.append
    )
    try:
        for _ in range(2):
            telemetry.event("test.relay", "failure", mode="text", reason="unavailable")
        assert not delivered
        telemetry.event("test.relay", "success", mode="text", reason="ready")
        for _ in range(3):
            telemetry.event("test.relay", "failure", mode="text", reason="unavailable")
        assert delivered[-1]["reason_code"] == "dependency_failures"
        for _ in range(19):
            telemetry.event(
                "test.latency",
                "success",
                mode="text",
                reason="ready",
                measurement="backend_first_text_seconds",
                value=2.1,
            )
        assert len(delivered) == 1
        telemetry.event(
            "test.latency",
            "success",
            mode="text",
            reason="ready",
            measurement="backend_first_text_seconds",
            value=2.1,
        )
        assert delivered[-1]["reason_code"] == "latency_high"
        telemetry.event(
            "test.cleanup",
            "failure",
            mode="speech",
            reason="cleanup_pending",
            correlation_id="cleanup-fixture",
        )
        clock[0] = 10
        telemetry.poll_alerts()
        assert len(delivered) == 2
        clock[0] = 10.001
        telemetry.poll_alerts()
        assert delivered[-1]["reason_code"] == "cleanup_uncertain"
        assert delivered[-1]["correlation_id"] == "cleanup-fixture"
        telemetry.event(
            "test.cleanup",
            "success",
            mode="speech",
            reason="stopped",
            correlation_id="cleanup-fixture",
        )
        clock[0] = 301
        for _ in range(19):
            telemetry.event(
                "test.latency",
                "success",
                mode="text",
                reason="ready",
                measurement="backend_first_text_seconds",
                value=2.1,
            )
        assert len(delivered) == 3
        telemetry.event(
            "test.session",
            "failure",
            mode="speech",
            reason="cleanup_pending",
            correlation_id="already-waited",
        )
        telemetry.event(
            "test.latency",
            "failure",
            mode="speech",
            reason="cleanup_pending",
            correlation_id="already-waited",
            measurement="cleanup_seconds",
            value=10.1,
        )
        assert len(delivered) == 4 and delivered[-1]["reason_code"] == "cleanup_uncertain"
        assert all(event["event"] == "test.alert" for event in delivered)
        assert "provider_body" not in json.dumps(delivered)
    finally:
        telemetry.close()


def test_alert_windows_and_owned_timers_remain_bounded_and_close(monkeypatch):
    from types import SimpleNamespace

    from management_app import observability

    timers = []

    class Timer:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    def schedule(delay, callback):
        assert 0 < delay <= 10.001
        timer = Timer()
        timers.append(timer)
        return timer

    monkeypatch.setattr(
        observability.asyncio, "get_running_loop", lambda: SimpleNamespace(call_later=schedule)
    )
    telemetry = Telemetry("management", "local", monotonic=lambda: 0.0)
    for number in range(10):
        telemetry.event(
            "test.cleanup",
            "failure",
            mode="text",
            reason="cleanup_pending",
            correlation_id="pending-" + str(number),
        )
    assert len(telemetry._pending_cleanup) == len(timers) == 5
    for _ in range(600):
        telemetry.event(
            "test.latency",
            "success",
            mode="text",
            reason="ready",
            measurement="backend_first_text_seconds",
            value=0.1,
        )
    assert len(telemetry._response_samples["text"]) == 512
    telemetry.close()
    assert all(timer.cancelled for timer in timers) and not telemetry._pending_cleanup
    telemetry.event("test.cleanup", "failure", mode="text", reason="cleanup_pending")
    assert not telemetry.events and not telemetry._pending_cleanup


def test_alert_sink_failure_never_exposes_provider_details(caplog):
    def sink(event):
        raise RuntimeError("synthetic-private-alert-sink-body")

    telemetry = Telemetry("management", "local", alert_sink=sink)
    for _ in range(3):
        telemetry.event("test.relay", "failure", mode="text", reason="unavailable")
    assert any(event["event"] == "test.alert" for event in telemetry.events)
    assert "synthetic-private-alert-sink-body" not in caplog.text
    telemetry.close()


@pytest.mark.parametrize(
    "key",
    [
        "credential",
        "prompt",
        "audio",
        "transcript",
        "tool_arguments",
        "tool_result",
        "permit",
        "provider_body",
        "session_id",
        "actor_id",
    ],
)
def test_sensitive_trace_attributes_are_rejected(key: str) -> None:
    telemetry = Telemetry("management", "local")
    with pytest.raises(ValueError, match="unsafe_trace_attribute"):
        with telemetry.span("http.request", {key: "sensitive-synthetic-content"}):
            pytest.fail("Forbidden trace attribute accepted")
    assert not telemetry.spans
    telemetry.close()


def test_runtime_context_and_safe_structured_events() -> None:
    telemetry = Telemetry("management", "local")
    incoming = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    with telemetry.span(
        "http.request", headers={"traceparent": incoming, "baggage": "prompt=secret"}
    ):
        headers = telemetry.headers()
        assert set(headers) == {"traceparent"}
        assert headers["traceparent"].split("-")[1] == "a" * 32
        telemetry.event("request", "success", correlation_id="management-001")
    span = telemetry.spans[-1]
    assert span.context.trace_id == int("a" * 32, 16)
    assert span.parent.span_id == int("b" * 16, 16)
    event = telemetry.events[-1]
    assert event["service"] == "management"
    assert event["environment"] == "local"
    assert event["trace_id"] == "a" * 32
    assert event["correlation_id"] == "management-001"
    assert "secret" not in json.dumps(event)
    assert {"timestamp", "severity", "event", "outcome", "span_id"} <= event.keys()
    telemetry.close()


def test_multimodal_metadata_uses_fixed_vocabulary_and_correlated_safe_events():
    telemetry = Telemetry("management", "local")
    with telemetry.span(
        "test.relay",
        {"http.route": "/v1/console/sessions", "test.mode": "text", "test.phase": "active"},
    ):
        telemetry.event(
            "test.relay",
            "success",
            correlation_id="test-management-001",
            mode="text",
            reason="ready",
        )
    event = telemetry.events[-1]
    assert event["mode"] == "text" and event["reason_code"] == "ready"
    assert event["correlation_id"] == "test-management-001"
    assert event["trace_id"] and event["span_id"]
    assert set(event) == {
        "timestamp",
        "severity",
        "service",
        "environment",
        "event",
        "outcome",
        "correlation_id",
        "trace_id",
        "span_id",
        "mode",
        "reason_code",
    }
    with telemetry.span("configuration.publish", {"http.route": "/v2/configuration"}):
        telemetry.event("configuration", "success")
    assert len(telemetry.spans) == len(telemetry.events) == 2
    telemetry.close()


@pytest.mark.parametrize(
    "mode,reason", [("synthetic-private-mode", "ready"), ("text", "synthetic-private-reason")]
)
def test_multimodal_metadata_rejects_unbounded_mode_or_reason(mode, reason, caplog):
    telemetry = Telemetry("management", "local")
    with pytest.raises(ValueError, match="unsafe_test_metadata"):
        telemetry.event("test.relay", "failure", mode=mode, reason=reason)
    assert not telemetry.events and "synthetic-private" not in caplog.text
    telemetry.close()


def test_exception_content_excluded_and_capture_bounded() -> None:
    telemetry = Telemetry("management", "local")
    with pytest.raises(RuntimeError):
        with telemetry.span("http.request"):
            raise RuntimeError("sensitive-internal-content")
    assert "sensitive-internal-content" not in telemetry.spans[-1].to_json()
    for sequence in range(600):
        with telemetry.span("http.request"):
            telemetry.event("request", "success", correlation_id=f"probe-{sequence}")
    assert len(telemetry.spans) <= 512
    assert len(telemetry.events) <= 512
    telemetry.close()
    telemetry.close()
