"""Management owns its metadata-only telemetry and accepts safe trace context."""

import json

import pytest

from management_app.observability import Telemetry


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
