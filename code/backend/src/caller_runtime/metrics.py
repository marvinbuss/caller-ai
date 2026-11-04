"""Runtime-owned bounded aggregate metrics; no conversation content or implicit exporter."""

import math
from typing import Any

from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter, Counter, Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    InMemoryMetricReader,
    MetricExporter,
    MetricExportResult,
    MetricReader,
    MetricsData,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

METHODS = frozenset({"GET", "POST", "OTHER"})
ROUTE_PATHS = {
    "/healthz": "health",
    "/readyz": "readiness",
    "/v1/conversations/text": "text",
    "/v1/conversations/voice": "voice",
}
ROUTES = frozenset({"health", "readiness", "text", "voice", "unmatched"})
DEPENDENCIES = frozenset(
    {"readiness", "session_store", "history_store", "model", "configuration", "capability_gateway"}
)
REASONS = frozenset({"unavailable", "deadline", "quota", "rejected"})
RESOURCES = frozenset({"session_slots", "model_sessions", "admission_queue"})
ADMISSIONS = frozenset({"admitted", "duplicate", "unavailable"})


def finite(value: float, *, maximum: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("invalid_metric_value")
    return float(value)


def export_budget(timeout_millis: float) -> float:
    budget = min(1000.0, finite(timeout_millis, maximum=30000))
    if budget == 0:
        raise ValueError("invalid_metric_export_budget")
    return budget


class _GuardedExporter(MetricExporter):
    def __init__(self, delegate: MetricExporter) -> None:
        super().__init__(
            preferred_temporality={
                Counter: AggregationTemporality.DELTA,
                Histogram: AggregationTemporality.DELTA,
            }
        )
        self._delegate = delegate
        self.failed = False
        self.exported = False
        self._closed = False

    def export(
        self, metrics_data: MetricsData, timeout_millis: float = 1000, **kwargs: Any
    ) -> MetricExportResult:
        try:
            if (
                not self._closed
                and self._delegate.export(
                    metrics_data, timeout_millis=export_budget(timeout_millis)
                )
                is MetricExportResult.SUCCESS
            ):
                self.exported = True
                return MetricExportResult.SUCCESS
        except Exception:
            pass
        self.failed = True
        return MetricExportResult.FAILURE

    def force_flush(self, timeout_millis: float = 1000) -> bool:
        try:
            if (
                not self._closed
                and self._delegate.force_flush(timeout_millis=export_budget(timeout_millis)) is True
            ):
                return self.exported and not self.failed
        except Exception:
            pass
        self.failed = True
        return False

    def shutdown(self, timeout_millis: float = 1000, **kwargs: Any) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._delegate.shutdown(timeout_millis=export_budget(timeout_millis))
            except Exception:
                self.failed = True


class DeltaExportReader(PeriodicExportingMetricReader):
    """Manual SDK delta collection for a trusted exporter; no worker or implicit destination."""

    def __init__(self, exporter: MetricExporter) -> None:
        self._guard = _GuardedExporter(exporter)
        self._reader_closed = False
        super().__init__(self._guard, export_interval_millis=math.inf, export_timeout_millis=1000)

    @property
    def failed(self) -> bool:
        return self._guard.failed

    def force_flush(self, timeout_millis: float = 1000) -> bool:
        if self._reader_closed:
            return False
        try:
            return (
                super().force_flush(timeout_millis=export_budget(timeout_millis))
                and self._guard.exported
                and not self.failed
            )
        except Exception:
            self._guard.failed = True
            return False

    def shutdown(self, timeout_millis: float = 1000, **kwargs: Any) -> None:
        if not self._reader_closed:
            self.force_flush(timeout_millis)
            self._reader_closed = True
            try:
                super().shutdown(timeout_millis=1000)
            except Exception:
                self._guard.failed = True


class RuntimeMetrics:
    def __init__(self, *, mode: str, reader: MetricReader | None = None) -> None:
        if mode not in {"local", "live"}:
            raise ValueError("invalid_metric_mode")
        self._provider = MeterProvider(
            resource=Resource(
                {"service.name": "caller-runtime", "deployment.environment.name": mode}
            ),
            metric_readers=[reader if reader is not None else InMemoryMetricReader()],
            exemplar_filter=AlwaysOffExemplarFilter(),
        )
        meter = self._provider.get_meter("caller-runtime")
        self._requests = meter.create_counter("caller.requests", unit="{request}")
        self._errors = meter.create_counter("caller.errors", unit="{error}")
        self._request_duration = meter.create_histogram(
            "caller.request.duration",
            unit="s",
            explicit_bucket_boundaries_advisory=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30),
        )
        self._admissions = meter.create_counter("caller.admissions", unit="{admission}")
        self._dependencies = meter.create_counter("caller.dependency.failures", unit="{failure}")
        self._saturation = meter.create_gauge("caller.saturation", unit="1")
        self._closed = False

    @staticmethod
    def route_for(path: str) -> str:
        return ROUTE_PATHS.get(path, "unmatched")

    def _open(self) -> None:
        if self._closed:
            raise RuntimeError("metrics_closed")

    def request(self, *, route: str, method: str, status: int, seconds: float) -> None:
        self._open()
        if (
            route not in ROUTES
            or method not in METHODS
            or type(status) is not int
            or not 100 <= status <= 599
        ):
            raise ValueError("invalid_request_metric_labels")
        duration = finite(seconds, maximum=3600)
        attributes = {"route": route, "method": method, "status_class": f"{status // 100}xx"}
        self._requests.add(1, attributes)
        self._request_duration.record(duration, attributes)
        if status >= 500:
            self._errors.add(1, attributes)

    def admission(self, outcome: str) -> None:
        self._open()
        if outcome not in ADMISSIONS:
            raise ValueError("invalid_admission_metric_label")
        self._admissions.add(1, {"outcome": outcome})

    def dependency_failure(self, dependency: str, reason: str) -> None:
        self._open()
        if dependency not in DEPENDENCIES or reason not in REASONS:
            raise ValueError("invalid_dependency_metric_labels")
        self._dependencies.add(1, {"dependency": dependency, "reason": reason})

    def saturation(self, resource: str, ratio: float) -> None:
        self._open()
        if resource not in RESOURCES:
            raise ValueError("invalid_saturation_metric_label")
        self._saturation.set(finite(ratio, maximum=1), {"resource": resource})

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._provider.shutdown()
