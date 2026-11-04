"""Runtime-owned metadata telemetry; no content-bearing fields or network exporters."""

import asyncio
import json
import logging
import math
import re
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock, RLock
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.trace.status import Status, StatusCode

TEST_OPERATIONS = frozenset(
    {
        "test.admission",
        "test.session",
        "test.work",
        "test.cleanup",
        "test.relay",
        "test.delegation",
        "test.denial",
        "test.usage",
        "test.latency",
        "test.alert",
    }
)
TEST_MEASUREMENTS = {
    "test.usage": frozenset(
        {"voice_seconds_snapshot", "voice_seconds_final", "input_tokens", "output_tokens"}
    ),
    "test.latency": frozenset(
        {"backend_first_text_seconds", "work_seconds", "cleanup_seconds", "admission_seconds"}
    ),
    "history.store": frozenset({"store_seconds"}),
}
TEST_MODES = frozenset({"text", "speech"})
TEST_PHASES = frozenset({"starting", "ready", "active", "ending", "terminal", "cleanup_pending"})
TEST_REASONS = frozenset(
    {
        "ready",
        "stopped",
        "denied",
        "unavailable",
        "deadline",
        "idle_expired",
        "lifetime_expired",
        "auth_expired",
        "cleanup_pending",
        "protocol_error",
        "revoked",
        "stale_owner",
        "stale_generation",
        "invalid_input",
        "quota",
        "cleanup_uncertain",
        "dependency_failures",
        "latency_high",
    }
)
OPERATIONS = (
    frozenset(
        {
            "http.request",
            "probe.receive",
            "sdk.trace",
            "sdk.span",
            "configuration.refresh",
            "configuration.publish",
        }
    )
    | TEST_OPERATIONS
)
EVENTS = (
    frozenset(
        {"startup", "shutdown", "request", "probe", "failure", "configuration", "history.store"}
    )
    | TEST_OPERATIONS
)
VALUES = {
    "http.method": frozenset({"GET", "HEAD", "POST", "PUT", "WEBSOCKET"}),
    "http.route": frozenset(
        {
            "/healthz",
            "/readyz",
            "/v1/conversations/text",
            "/v1/conversations/voice",
            "/v2/runtime/configuration",
            "unmatched",
        }
    ),
    "outcome": frozenset({"success", "failure"}),
    "test.kind": frozenset({"text", "voice"}),
    "test.phase": TEST_PHASES,
    "test.reason": TEST_REASONS,
}


def sanitize_correlation_id(value: str | None) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        if not value.startswith(("sk-", "AKIA", "eyJ")):
            return value
    return uuid4().hex


class MemoryExporter(SpanExporter):
    def __init__(self) -> None:
        self._spans: deque[ReadableSpan] = deque(maxlen=512)
        self._lock = Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def snapshot(self) -> tuple[ReadableSpan, ...]:
        with self._lock:
            return tuple(self._spans)

    def shutdown(self) -> None:
        with self._lock:
            self._spans.clear()


class Telemetry:
    def __init__(
        self,
        service: str,
        mode: str,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        alert_sink: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        if service not in {"caller-runtime", "management"} or mode not in {"local", "live"}:
            raise ValueError("invalid_telemetry_identity")
        self.service = service
        self.mode = mode
        self._exporter = MemoryExporter()
        self._provider = TracerProvider(
            resource=Resource(
                {
                    "service.name": service,
                    "deployment.environment.name": mode,
                }
            )
        )
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        self._tracer = self._provider.get_tracer(service)
        self._propagator = TraceContextTextMapPropagator()
        self._events: deque[dict[str, str]] = deque(maxlen=512)
        self._event_lock = Lock()
        self._closed = False
        self._alert_clock, self._alert_sink = monotonic, alert_sink
        self._alert_tick: float | None = None
        self._alert_lock = RLock()
        self._dependency_failures: dict[str, deque[float]] = {
            mode: deque(maxlen=3) for mode in TEST_MODES
        }
        self._response_samples: dict[str, deque[tuple[float, float]]] = {
            mode: deque(maxlen=512) for mode in TEST_MODES
        }
        self._pending_cleanup: dict[str, tuple[float, str]] = {}
        self._cleanup_notified: set[str] = set()
        self._cleanup_timers: dict[str, asyncio.TimerHandle] = {}
        self._alerted: dict[tuple[str, str], float] = {}
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        self._logger = logging.getLogger(f"{service}.events")
        self._logger.setLevel(logging.INFO)

    @property
    def spans(self) -> tuple[ReadableSpan, ...]:
        return self._exporter.snapshot()

    @property
    def events(self) -> tuple[dict[str, str], ...]:
        with self._event_lock:
            return tuple(dict(event) for event in self._events)

    @contextmanager
    def span(
        self,
        operation: str,
        attributes: Mapping[str, str | int] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[None]:
        if operation not in OPERATIONS:
            raise ValueError("unsafe_trace_operation")
        safe = dict(attributes or {})
        for key, value in safe.items():
            if key == "http.status_code" and type(value) is int and 100 <= value <= 599:
                continue
            if key not in VALUES or not isinstance(value, str) or value not in VALUES[key]:
                raise ValueError("unsafe_trace_attribute")
        if self._closed:
            yield
            return
        context = (
            self._propagator.extract({"traceparent": headers.get("traceparent", "")})
            if headers is not None
            else None
        )
        with self._tracer.start_as_current_span(
            operation,
            context=context,
            attributes=safe,
            record_exception=False,
            set_status_on_exception=False,
        ) as current:
            try:
                yield
            except BaseException:
                current.set_status(Status(StatusCode.ERROR))
                raise

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        self._propagator.inject(headers)
        return {key: value for key, value in headers.items() if key == "traceparent"}

    def event(
        self,
        name: str,
        outcome: str,
        *,
        correlation_id: str | None = None,
        mode: str | None = None,
        reason: str | None = None,
        measurement: str | None = None,
        value: int | float | None = None,
    ) -> None:
        if name not in EVENTS:
            raise ValueError("unsafe_event_name")
        if outcome not in {"success", "failure"}:
            raise ValueError("unsafe_event_outcome")
        if name in TEST_OPERATIONS:
            if (
                mode is None
                or reason is None
                or mode not in TEST_MODES
                or reason not in TEST_REASONS
            ):
                raise ValueError("unsafe_test_metadata")
        elif mode is not None or reason is not None:
            raise ValueError("unsafe_test_metadata")
        if measurement is not None:
            if (
                measurement not in TEST_MEASUREMENTS.get(name, frozenset())
                or value is None
                or type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= (600 if name == "test.latency" else 2**31)
                or measurement.endswith("_tokens")
                and type(value) is not int
            ):
                raise ValueError("unsafe_test_measurement")
        elif value is not None:
            raise ValueError("unsafe_test_measurement")
        if self._closed:
            return
        context = trace.get_current_span().get_span_context()
        event = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "severity": "ERROR" if outcome == "failure" else "INFO",
            "service": self.service,
            "environment": self.mode,
            "event": name,
            "outcome": outcome,
            "correlation_id": sanitize_correlation_id(correlation_id),
            "trace_id": format(context.trace_id, "032x") if context.is_valid else "",
            "span_id": format(context.span_id, "016x") if context.is_valid else "",
        }
        if mode is not None:
            event["kind"] = "voice" if mode == "speech" else "text"
        if reason is not None:
            event["reason_code"] = reason
        if measurement is not None:
            event["measurement"] = measurement
            event["value"] = str(value)
        with self._event_lock:
            self._events.append(event)
        self._logger.log(logging.ERROR if outcome == "failure" else logging.INFO, json.dumps(event))
        if name == "test.alert":
            if self._alert_sink is not None:
                try:
                    self._alert_sink(dict(event))
                except Exception:
                    pass
        elif name in TEST_OPERATIONS and mode is not None:
            self._observe_test(event, mode, measurement, value)

    def _alert_now(self) -> float:
        now = self._alert_clock()
        if (
            type(now) not in (int, float)
            or not math.isfinite(now)
            or now < 0
            or self._alert_tick is not None
            and now < self._alert_tick
        ):
            raise ValueError("invalid_test_alert_clock")
        self._alert_tick = now
        return now

    def _test_alert(self, reason: str, mode: str, correlation: str, now: float) -> None:
        key = reason, mode
        interval = 60 if reason == "dependency_failures" else 300
        if reason != "cleanup_uncertain" and now - self._alerted.get(key, -math.inf) < interval:
            return
        self._alerted[key] = now
        self.event("test.alert", "failure", mode=mode, reason=reason, correlation_id=correlation)

    def poll_alerts(self) -> None:
        if self._closed:
            return
        with self._alert_lock:
            now = self._alert_now()
            for correlation, (started, mode) in tuple(self._pending_cleanup.items()):
                if now - started > 10 and correlation not in self._cleanup_notified:
                    self._cleanup_notified.add(correlation)
                    timer = self._cleanup_timers.pop(correlation, None)
                    if timer is not None:
                        timer.cancel()
                    self._test_alert("cleanup_uncertain", mode, correlation, now)

    def _observe_test(
        self, event: dict[str, str], mode: str, measurement: str | None, value: int | float | None
    ) -> None:
        with self._alert_lock:
            now = self._alert_now()
            name, correlation = event["event"], event["correlation_id"]
            if name in {"test.admission", "test.work", "test.relay"}:
                failures = self._dependency_failures[mode]
                if event["outcome"] == "success":
                    failures.clear()
                elif event.get("reason_code") == "unavailable":
                    failures.append(now)
                    if len(failures) == 3 and now - failures[0] <= 60:
                        self._test_alert("dependency_failures", mode, correlation, now)
            if measurement == "backend_first_text_seconds" and value is not None:
                samples = self._response_samples[mode]
                samples.append((now, float(value)))
                while samples and now - samples[0][0] > 300:
                    samples.popleft()
                if len(samples) >= 20:
                    ordered = sorted(sample for _time, sample in samples)
                    if ordered[math.ceil(len(ordered) * 0.95) - 1] > 2:
                        self._test_alert("latency_high", mode, correlation, now)
            if name == "test.cleanup" and event["outcome"] == "success":
                self._pending_cleanup.pop(correlation, None)
                self._cleanup_notified.discard(correlation)
                timer = self._cleanup_timers.pop(correlation, None)
                if timer is not None:
                    timer.cancel()
            elif (
                event.get("reason_code") == "cleanup_pending"
                and correlation not in self._pending_cleanup
            ):
                if len(self._pending_cleanup) < 5:
                    elapsed = (
                        float(value)
                        if measurement == "cleanup_seconds" and value is not None
                        else 0
                    )
                    self._pending_cleanup[correlation] = now - elapsed, mode
                    try:
                        self._cleanup_timers[correlation] = asyncio.get_running_loop().call_later(
                            max(0.001, 10.001 - elapsed), self.poll_alerts
                        )
                    except RuntimeError:
                        pass
            if (
                measurement == "cleanup_seconds"
                and value is not None
                and correlation in self._pending_cleanup
            ):
                started, pending_mode = self._pending_cleanup[correlation]
                earliest = min(started, now - float(value))
                self._pending_cleanup[correlation] = earliest, pending_mode
                if earliest < started:
                    timer = self._cleanup_timers.pop(correlation, None)
                    if timer is not None:
                        timer.cancel()
                    try:
                        self._cleanup_timers[correlation] = asyncio.get_running_loop().call_later(
                            max(0.001, 10.001 - (now - earliest)), self.poll_alerts
                        )
                    except RuntimeError:
                        pass
            self.poll_alerts()

    def model_usage(
        self, *, mode: str, input_tokens: int | None, output_tokens: int | None
    ) -> None:
        if type(input_tokens) is not int or input_tokens <= 0 or type(output_tokens) is not int:
            self.event("test.usage", "failure", mode=mode, reason="unavailable")
            return
        self.event(
            "test.usage",
            "success",
            mode=mode,
            reason="ready",
            measurement="input_tokens",
            value=input_tokens,
        )
        self.event(
            "test.usage",
            "success",
            mode=mode,
            reason="ready",
            measurement="output_tokens",
            value=output_tokens,
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            for timer in self._cleanup_timers.values():
                timer.cancel()
            self._cleanup_timers.clear()
            self._pending_cleanup.clear()
            self._cleanup_notified.clear()
            self._provider.shutdown()
            with self._event_lock:
                self._events.clear()
