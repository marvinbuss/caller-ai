"""Application traces and structured events must never carry caller content."""

import json
from datetime import datetime

import pytest

from caller_runtime.observability import Telemetry


def test_runtime_lifecycle_binds_explicit_bounded_nonblocking_alert_sink():
    from queue import Queue

    from fastapi.testclient import TestClient

    from caller_runtime.main import create_app

    pending = Queue(maxsize=1)
    attempted = []

    def enqueue(event):
        attempted.append(event)
        pending.put_nowait(event)

    application = create_app(alert_sink=enqueue)
    with TestClient(application) as client:
        assert client.get("/readyz").status_code == 200
        telemetry = application.state.telemetry
        with telemetry.span(
            "test.work", headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}
        ):
            for _ in range(3):
                telemetry.event(
                    "test.work",
                    "failure",
                    mode="text",
                    reason="unavailable",
                    correlation_id="cross-service-fixture",
                )
        telemetry.event(
            "test.alert",
            "failure",
            mode="speech",
            reason="cleanup_uncertain",
            correlation_id="cross-service-fixture",
        )
        assert pending.qsize() == 1 and len(attempted) == 2
        event = pending.get_nowait()
        assert event["service"] == "caller-runtime" and event["event"] == "test.alert"
        assert event["correlation_id"] == "cross-service-fixture"
        assert event["trace_id"] == "1" * 32
        assert set(event) <= {
            "timestamp",
            "severity",
            "service",
            "environment",
            "event",
            "outcome",
            "correlation_id",
            "trace_id",
            "span_id",
            "kind",
            "reason_code",
        }
        event["reason_code"] = "detached-fixture"
        assert all(item.get("reason_code") != "detached-fixture" for item in telemetry.events)
    telemetry.event("test.alert", "failure", mode="text", reason="dependency_failures")
    assert len(attempted) == 2 and not telemetry.events


def test_test_alert_thresholds_are_bounded_correlated_and_content_free():
    clock, delivered = [0.0], []
    telemetry = Telemetry(
        "caller-runtime", "local", monotonic=lambda: clock[0], alert_sink=delivered.append
    )
    try:
        for _ in range(2):
            telemetry.event("test.work", "failure", mode="text", reason="unavailable")
        assert not delivered
        telemetry.event("test.work", "success", mode="text", reason="ready")
        telemetry.event("test.work", "failure", mode="text", reason="unavailable")
        assert not delivered
        for _ in range(2):
            telemetry.event("test.work", "failure", mode="text", reason="unavailable")
        assert [event["reason_code"] for event in delivered] == ["dependency_failures"]
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
        assert all(
            set(event)
            <= {
                "timestamp",
                "severity",
                "service",
                "environment",
                "event",
                "outcome",
                "correlation_id",
                "trace_id",
                "span_id",
                "kind",
                "reason_code",
            }
            for event in delivered
        )
    finally:
        telemetry.close()


def test_alert_windows_and_owned_timers_remain_bounded_and_close(monkeypatch):
    from types import SimpleNamespace

    from caller_runtime import observability

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
    telemetry = Telemetry("caller-runtime", "local", monotonic=lambda: 0.0)
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

    telemetry = Telemetry("caller-runtime", "local", alert_sink=sink)
    for _ in range(3):
        telemetry.event("test.work", "failure", mode="text", reason="unavailable")
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
    telemetry = Telemetry("caller-runtime", "local")
    with pytest.raises(ValueError, match="unsafe_trace_attribute"):
        with telemetry.span("http.request", {key: "sensitive-synthetic-content"}):
            pytest.fail("Forbidden trace attribute accepted")
    assert not telemetry.spans
    telemetry.close()


def test_trace_context_propagates_without_baggage() -> None:
    telemetry = Telemetry("caller-runtime", "local")
    incoming = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    with telemetry.span(
        "http.request", headers={"traceparent": incoming, "baggage": "prompt=secret"}
    ):
        headers = telemetry.headers()
        assert set(headers) == {"traceparent"}
        assert headers["traceparent"].split("-")[1] == "1" * 32
        telemetry.event("request", "success", correlation_id="runtime-001")
    span = telemetry.spans[-1]
    assert span.context.trace_id == int("1" * 32, 16)
    assert span.parent.span_id == int("2" * 16, 16)
    event = telemetry.events[-1]
    assert {
        "timestamp",
        "severity",
        "service",
        "environment",
        "event",
        "outcome",
        "correlation_id",
        "trace_id",
        "span_id",
    } <= event.keys()
    assert (
        datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        .utcoffset()
        .total_seconds()
        == 0
    )
    assert event["service"] == "caller-runtime"
    assert event["environment"] == "local"
    assert event["correlation_id"] == "runtime-001"
    assert event["trace_id"] == "1" * 32
    assert "secret" not in json.dumps(event)
    telemetry.close()


def test_multimodal_metadata_uses_fixed_vocabulary_and_correlated_safe_events():
    telemetry = Telemetry("caller-runtime", "local")
    with telemetry.span(
        "test.session",
        {"http.route": "/v1/conversations/voice", "test.kind": "voice", "test.phase": "active"},
    ):
        telemetry.event(
            "test.session",
            "success",
            correlation_id="test-runtime-001",
            mode="speech",
            reason="ready",
        )
    event = telemetry.events[-1]
    assert event["kind"] == "voice" and event["reason_code"] == "ready"
    assert event["correlation_id"] == "test-runtime-001"
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
        "kind",
        "reason_code",
    }
    with telemetry.span("configuration.refresh", {"http.route": "/v2/runtime/configuration"}):
        telemetry.event("configuration", "success")
    assert len(telemetry.spans) == len(telemetry.events) == 2
    telemetry.close()


@pytest.mark.parametrize(
    "mode,reason", [("synthetic-private-mode", "ready"), ("text", "synthetic-private-reason")]
)
def test_multimodal_metadata_rejects_unbounded_mode_or_reason(mode, reason, caplog):
    telemetry = Telemetry("caller-runtime", "local")
    with pytest.raises(ValueError, match="unsafe_test_metadata"):
        telemetry.event("test.session", "failure", mode=mode, reason=reason)
    assert not telemetry.events and "synthetic-private" not in caplog.text
    telemetry.close()


def test_test_usage_latency_and_delegation_measurements_are_closed_and_content_free(caplog):
    telemetry = Telemetry("caller-runtime", "local")
    try:
        telemetry.event(
            "test.usage",
            "success",
            mode="speech",
            reason="stopped",
            measurement="voice_seconds_final",
            value=3.5,
        )
        telemetry.event(
            "test.latency",
            "success",
            mode="text",
            reason="ready",
            measurement="backend_first_text_seconds",
            value=0.25,
        )
        telemetry.event("test.delegation", "success", mode="speech", reason="ready")
        assert telemetry.events[0]["measurement"] == "voice_seconds_final"
        assert telemetry.events[0]["value"] == "3.5"
        assert telemetry.events[1]["value"] == "0.25"
        for value in (float("nan"), float("inf"), -1, True, "synthetic-private-value"):
            with pytest.raises(ValueError):
                telemetry.event(
                    "test.usage",
                    "success",
                    mode="speech",
                    reason="ready",
                    measurement="voice_seconds_final",
                    value=value,
                )
        with pytest.raises(ValueError):
            telemetry.event(
                "test.usage",
                "success",
                mode="speech",
                reason="ready",
                measurement="synthetic-private-label",
                value=1,
            )
        with pytest.raises(ValueError):
            telemetry.event("request", "success", measurement="voice_seconds_final", value=1)
        assert len(telemetry.events) == 3 and "synthetic-private" not in caplog.text
        telemetry.event("test.usage", "failure", mode="text", reason="unavailable")
        assert "value" not in telemetry.events[-1]
    finally:
        telemetry.close()


def test_exception_content_is_not_recorded_and_capture_is_bounded() -> None:
    telemetry = Telemetry("caller-runtime", "local")
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


def test_history_store_event_carries_only_outcome_and_latency():
    telemetry = Telemetry("caller-runtime", "local")
    try:
        telemetry.event("history.store", "success", measurement="store_seconds", value=0.01)
        event = telemetry.events[-1]
        assert event["event"] == "history.store" and event["measurement"] == "store_seconds"
        assert "kind" not in event and "reason_code" not in event
        with pytest.raises(ValueError, match="unsafe_test_metadata"):
            telemetry.event("history.store", "success", mode="text", reason="ready")
        with pytest.raises(ValueError, match="unsafe_test_measurement"):
            telemetry.event("history.store", "success", measurement="work_seconds", value=1)
    finally:
        telemetry.close()


def test_status_probe_event_carries_only_model_outcome_and_duration():
    telemetry = Telemetry("caller-runtime", "local")
    try:
        telemetry.event("status.probe", "failure", measurement="voice_probe_seconds", value=2.0)
        event = telemetry.events[-1]
        assert event["event"] == "status.probe" and event["outcome"] == "failure"
        assert event["measurement"] == "voice_probe_seconds" and event["value"] == "2.0"
        assert "kind" not in event and "reason_code" not in event
        assert "openai.azure.com" not in json.dumps(event)
        with pytest.raises(ValueError, match="unsafe_test_measurement"):
            telemetry.event("status.probe", "success", measurement="host_seconds", value=1)
        with pytest.raises(ValueError, match="unsafe_test_metadata"):
            telemetry.event("status.probe", "success", mode="text", reason="ready")
    finally:
        telemetry.close()


def test_diagnostic_events_follow_the_event_schema_and_drop_free_text(caplog):
    telemetry = Telemetry("caller-runtime", "live")
    try:
        with caplog.at_level("WARNING", logger="caller-runtime.events"):
            telemetry.diagnostic(
                "test.work.failure", mode="text", error="PermissionError", code="model_unavailable"
            )
            telemetry.diagnostic(
                "test.admission.failure",
                mode="speech",
                error="RuntimeError",
                code="Synthetic provider said: caller name is Alice",
            )
            telemetry.diagnostic(
                "test.speech.unobserved",
                mode="speech",
                numbers={
                    "end_ms": 25400,
                    "since_first_frame_ms": 46348,
                    "since_last_frame_ms": None,
                },
            )
        work, admission, speech = telemetry.events[-3:]
        for event in (work, admission, speech):
            assert {
                "timestamp",
                "severity",
                "service",
                "environment",
                "event",
                "outcome",
                "correlation_id",
                "trace_id",
                "span_id",
            } <= event.keys()
            assert event["severity"] == "WARNING" and event["outcome"] == "failure"
        assert (
            work["error_class"] == "PermissionError" and work["error_code"] == "model_unavailable"
        )
        # Anything outside the closed shape of a fixed code is dropped, never logged.
        assert "error_code" not in admission and "Alice" not in caplog.text
        assert speech["end_ms"] == "25400" and "since_last_frame_ms" not in speech
        assert [json.loads(record.message)["event"] for record in caplog.records] == [
            "test.work.failure",
            "test.admission.failure",
            "test.speech.unobserved",
        ]
        for name, keywords in (
            ("test.unknown", {}),
            ("test.work.failure", {"numbers": {"end_ms": 1}}),
            ("test.speech.unobserved", {"numbers": {"end_ms": 1.5}}),
        ):
            with pytest.raises(ValueError, match="unsafe_diagnostic"):
                telemetry.diagnostic(name, mode="speech", **keywords)
        with pytest.raises(ValueError, match="unsafe_diagnostic"):
            telemetry.diagnostic("test.work.failure", mode="video")
    finally:
        telemetry.close()


def test_library_request_logs_stay_out_of_the_structured_application_log():
    import logging

    telemetry = Telemetry("caller-runtime", "live")
    try:
        # On int each authority read logged about forty unstructured header lines at INFO.
        for name in (
            "azure.core.pipeline.policies.http_logging_policy",
            "azure.identity",
            "httpx",
            "uvicorn.access",
        ):
            assert not logging.getLogger(name).isEnabledFor(logging.INFO)
            assert logging.getLogger(name).isEnabledFor(logging.WARNING)
        assert logging.getLogger("caller-runtime.events").isEnabledFor(logging.INFO)
    finally:
        telemetry.close()


def test_greeting_speech_parts_and_progress_events_use_closed_sets(caplog):
    telemetry = Telemetry("caller-runtime", "local")
    try:
        telemetry.event(
            "test.greeting",
            "success",
            mode="speech",
            reason="ready",
            measurement="greeting_seconds",
            value=1.5,
        )
        telemetry.event(
            "test.greeting",
            "success",
            mode="text",
            reason="fallback",
            measurement="greeting_seconds",
            value=4.0,
        )
        telemetry.event(
            "test.greeting",
            "failure",
            mode="speech",
            reason="deadline",
            measurement="greeting_seconds",
            value=5.0,
        )
        telemetry.event("test.greeting", "success", mode="speech", reason="skipped")
        telemetry.event(
            "test.speech_parts",
            "success",
            mode="speech",
            reason="ready",
            measurement="parts",
            value=5,
        )
        telemetry.event(
            "test.speech_parts",
            "success",
            mode="speech",
            reason="ready",
            measurement="dropped_parts",
            value=2,
        )
        for reason in ("still_working", "waiting_for_input", "completed", "failed"):
            telemetry.event("test.progress", "success", mode="text", reason=reason)
        telemetry.event("test.progress", "success", mode="speech", reason="cancellation_confirmed")
        for reason in ("rate_limited", "stale", "fallback_line"):
            telemetry.event("test.progress", "failure", mode="speech", reason=reason)
        events = telemetry.events
        assert len(events) == 14
        assert events[0]["event"] == "test.greeting" and events[0]["kind"] == "voice"
        assert events[0]["measurement"] == "greeting_seconds" and events[0]["value"] == "1.5"
        assert events[1]["reason_code"] == "fallback" and events[1]["kind"] == "text"
        assert events[3]["reason_code"] == "skipped" and "measurement" not in events[3]
        assert events[5]["measurement"] == "dropped_parts"
        with pytest.raises(ValueError, match="unsafe_test_metadata"):
            telemetry.event("test.progress", "success", mode="text", reason="synthetic-private")
        with pytest.raises(ValueError, match="unsafe_test_measurement"):
            telemetry.event(
                "test.greeting",
                "success",
                mode="text",
                reason="ready",
                measurement="parts",
                value=1,
            )
        with pytest.raises(ValueError, match="unsafe_test_measurement"):
            telemetry.event(
                "test.speech_parts",
                "success",
                mode="speech",
                reason="ready",
                measurement="greeting_seconds",
                value=1,
            )
        with pytest.raises(ValueError, match="unsafe_test_measurement"):
            telemetry.event(
                "test.greeting",
                "success",
                mode="text",
                reason="ready",
                measurement="greeting_seconds",
                value="synthetic-private-text",
            )
        assert len(telemetry.events) == 14 and "synthetic-private" not in caplog.text
        assert all(
            set(event)
            <= {
                "timestamp",
                "severity",
                "service",
                "environment",
                "event",
                "outcome",
                "correlation_id",
                "trace_id",
                "span_id",
                "kind",
                "reason_code",
                "measurement",
                "value",
            }
            for event in events
        )
    finally:
        telemetry.close()
