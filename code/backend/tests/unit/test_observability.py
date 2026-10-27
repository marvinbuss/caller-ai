"""Application traces and structured events must never carry caller content."""

import json
from datetime import datetime

import pytest

from caller_runtime.observability import Telemetry


@pytest.mark.parametrize(
    "key",
    [
        "credential",
        "prompt",
        "audio",
        "transcript",
        "tool_arguments",
        "tool_result",
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
