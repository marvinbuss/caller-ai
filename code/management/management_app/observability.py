"""Management-owned metadata telemetry; no content fields or network exporters."""

import json
import logging
import re
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.trace.status import Status, StatusCode

OPERATIONS = frozenset(
    {"http.request", "probe.receive", "sdk.trace", "sdk.span", "capability.execute"}
)
VALUES = {
    "http.method": frozenset({"GET", "HEAD", "POST", "WEBSOCKET"}),
    "http.route": frozenset({"/healthz", "/readyz", "/v1/dev/transport", "unmatched"}),
    "outcome": frozenset({"success", "failure"}),
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
    def __init__(self, service: str, mode: str) -> None:
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

    def event(self, name: str, outcome: str, *, correlation_id: str | None = None) -> None:
        if name not in {"startup", "shutdown", "request", "probe", "failure"}:
            raise ValueError("unsafe_event_name")
        if outcome not in {"success", "failure"}:
            raise ValueError("unsafe_event_outcome")
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
        with self._event_lock:
            self._events.append(event)
        self._logger.log(logging.ERROR if outcome == "failure" else logging.INFO, json.dumps(event))

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._provider.shutdown()
            with self._event_lock:
                self._events.clear()
