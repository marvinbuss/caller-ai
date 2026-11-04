"""Runtime OTel aggregates must not carry caller/session/agent/tool dimensions."""

import math

import pytest
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    InMemoryMetricReader,
    MetricExporter,
    MetricExportResult,
)


@pytest.fixture
def measured():
    from caller_runtime.metrics import RuntimeMetrics

    reader = InMemoryMetricReader()
    metrics = RuntimeMetrics(mode="local", reader=reader)
    yield metrics, reader
    metrics.close()


@pytest.fixture
def exported():
    from caller_runtime.metrics import DeltaExportReader, RuntimeMetrics

    class Sink(MetricExporter):
        def __init__(self):
            super().__init__()
            self.batches = []
            self.failure = None
            self.flush_ok = True
            self.closed = 0
            self.budgets = []

        def export(self, metrics_data, timeout_millis=10000, **kwargs):
            self.budgets.append(timeout_millis)
            if self.failure == "raise":
                raise RuntimeError("synthetic-private-export-details")
            if self.failure == "result":
                return MetricExportResult.FAILURE
            self.batches.append(metrics_data)
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis=10000):
            if self.failure == "flush_raise":
                raise RuntimeError("synthetic-private-flush-details")
            return self.flush_ok

        def shutdown(self, timeout_millis=30000, **kwargs):
            self.closed += 1
            if self.failure == "shutdown_raise":
                raise RuntimeError("synthetic-private-shutdown-details")

    sink = Sink()
    reader = DeltaExportReader(sink)
    metrics = RuntimeMetrics(mode="local", reader=reader)
    yield metrics, reader, sink
    metrics.close()


def exported_points(batch):
    return {
        metric.name: metric.data
        for resource in batch.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def test_delta_collection_does_not_recount_previous_intervals(exported):
    metrics, reader, sink = exported
    for seconds, saturation in [(0.25, 0.75), (0.5, 0.25)]:
        metrics.request(route="voice", method="GET", status=503, seconds=seconds)
        metrics.saturation("session_slots", saturation)
        assert reader.force_flush()
        values = exported_points(sink.batches[-1])
        assert values["caller.requests"].aggregation_temporality == AggregationTemporality.DELTA
        assert values["caller.requests"].data_points[0].value == 1
        duration = values["caller.request.duration"]
        assert duration.aggregation_temporality == AggregationTemporality.DELTA
        assert duration.data_points[0].count == 1 and duration.data_points[0].sum == seconds
        assert values["caller.saturation"].data_points[0].value == saturation
        assert all(not point.exemplars for data in values.values() for point in data.data_points)
        assert sink.batches[-1].resource_metrics[0].resource.attributes == {
            "service.name": "caller-runtime",
            "deployment.environment.name": "local",
        }
    assert reader.force_flush()
    assert (
        sum(
            point.value
            for batch in sink.batches
            for name, data in exported_points(batch).items()
            if name == "caller.requests"
            for point in data.data_points
        )
        == 2
    )
    assert all(0 < budget <= 1000 for budget in sink.budgets)


@pytest.mark.parametrize("failure", ["raise", "result", "flush", "flush_raise"])
def test_export_failure_is_safe_and_never_silently_cleared(exported, caplog, failure):
    metrics, reader, sink = exported
    sink.failure = failure
    sink.flush_ok = failure != "flush"
    metrics.admission("admitted")
    assert reader.force_flush() is False
    assert reader.failed
    sink.failure, sink.flush_ok = None, True
    metrics.admission("admitted")
    assert reader.force_flush() is False
    assert "synthetic-private-" not in caplog.text


def test_shutdown_collects_final_interval_and_closes_exporter_once(exported):
    metrics, reader, sink = exported
    metrics.admission("unavailable")
    metrics.close()
    metrics.close()
    assert sink.closed == 1
    assert exported_points(sink.batches[-1])["caller.admissions"].data_points[0].value == 1
    assert reader.force_flush() is False


def test_shutdown_failure_is_contained_but_visible(exported, caplog):
    metrics, reader, sink = exported
    sink.failure = "shutdown_raise"
    metrics.close()
    assert reader.failed and sink.closed == 1
    assert "synthetic-private-" not in caplog.text


@pytest.mark.parametrize("timeout", [0, -1, True, math.nan, math.inf])
def test_invalid_export_timeout_is_rejected_before_delegate_call(exported, timeout):
    metrics, reader, sink = exported
    metrics.admission("admitted")
    assert reader.force_flush(timeout) is False
    assert not sink.budgets


def test_empty_collection_is_not_successful_delivery(exported):
    metrics, reader, sink = exported
    assert reader.force_flush() is False
    assert not sink.batches
    metrics.admission("admitted")
    assert reader.force_flush()


def test_explicit_delta_collection_starts_no_worker(exported, monkeypatch):
    import threading

    from caller_runtime.metrics import DeltaExportReader, RuntimeMetrics

    def denied(*args, **kwargs):
        pytest.fail("metric_reader_spawned_worker")

    monkeypatch.setattr(threading.Thread, "start", denied)
    reader = DeltaExportReader(type(exported[2])())
    metrics = RuntimeMetrics(mode="local", reader=reader)
    try:
        metrics.admission("admitted")
        assert reader.force_flush()
    finally:
        metrics.close()


def points(reader):
    data = reader.get_metrics_data()
    assert data is not None
    return {
        metric.name: metric.data.data_points
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def test_runtime_exports_bounded_operational_signals(measured):
    metrics, reader = measured
    metrics.request(route="voice", method="GET", status=503, seconds=0.1)
    metrics.admission("unavailable")
    metrics.dependency_failure("model", "quota")
    metrics.saturation("session_slots", 0.8)
    values = points(reader)
    assert values["caller.admissions"][0].attributes == {"outcome": "unavailable"}
    assert values["caller.dependency.failures"][0].attributes == {
        "dependency": "model",
        "reason": "quota",
    }


@pytest.mark.parametrize("kind", ["request", "admission", "dependency", "reason", "resource"])
def test_untrusted_metric_dimensions_are_rejected_without_export(measured, kind):
    metrics, reader = measured
    marker = "synthetic-private-session-agent-content"
    with pytest.raises(ValueError):
        if kind == "request":
            metrics.request(route=marker, method="GET", status=200, seconds=0.1)
        elif kind == "admission":
            metrics.admission(marker)
        elif kind == "dependency":
            metrics.dependency_failure(marker, "unavailable")
        elif kind == "reason":
            metrics.dependency_failure("model", marker)
        else:
            metrics.saturation(marker, 0.5)
    assert marker not in repr(reader.get_metrics_data())


def test_obsolete_or_empty_context_cannot_add_metric_labels(measured):
    metrics, reader = measured
    assert metrics.route_for("/v1/conversations/text") == "text"
    assert metrics.route_for("/v1/conversations/voice") == "voice"
    assert metrics.route_for("/v1/operations/drain/owner-secret-fixture") == "unmatched"
    assert metrics.route_for("/v1/dev/transport") == "unmatched"
    assert metrics.route_for("/private/session-secret-fixture") == "unmatched"
    metrics.close()
    with pytest.raises(RuntimeError, match="metrics_closed"):
        metrics.admission("admitted")


def test_actual_runtime_http_lifespan_records_safe_routes_and_errors(measured):
    from fastapi.testclient import TestClient

    from caller_runtime.main import create_app

    metrics, reader = measured
    app = create_app(metrics=metrics)

    @app.get("/synthetic-private-error-path")
    async def failed():
        raise RuntimeError("synthetic-private-exception-content")

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/synthetic-private-error-path").status_code == 500
        assert client.get("/unknown/session-secret-fixture").status_code == 404
        values = points(reader)
        assert sum(point.value for point in values["caller.requests"]) == 3
        assert sum(point.value for point in values["caller.errors"]) == 1
        assert "synthetic-private" not in repr(values) and "session-secret-fixture" not in repr(
            values
        )


def test_readiness_failure_reports_only_generic_dependency(measured):
    from fastapi.testclient import TestClient

    from caller_runtime.main import create_app

    class BrokenDependencies:
        async def ready(self):
            raise RuntimeError("private-readiness-details")

    metrics, reader = measured
    with TestClient(create_app(metrics=metrics, dependencies=BrokenDependencies())) as client:
        assert client.get("/readyz").status_code == 503
        values = points(reader)
        assert values["caller.dependency.failures"][0].attributes == {
            "dependency": "readiness",
            "reason": "unavailable",
        }
        assert "private-readiness" not in repr(values)
