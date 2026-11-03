"""Real OTel metric export contracts; never emit caller content or unbounded labels."""

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
    from management_app.metrics import ManagementMetrics

    reader = InMemoryMetricReader()
    metrics = ManagementMetrics(mode="local", reader=reader)
    yield metrics, reader
    metrics.close()


@pytest.fixture
def exported():
    from management_app.metrics import DeltaExportReader, ManagementMetrics

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
    metrics = ManagementMetrics(mode="local", reader=reader)
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
        metrics.request(route="configuration", method="PUT", status=503, seconds=seconds)
        metrics.saturation("dependency_budget", saturation)
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
            "service.name": "management",
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
    metrics.dependency_failure("configuration_store", "unavailable")
    assert reader.force_flush() is False
    assert reader.failed
    sink.failure, sink.flush_ok = None, True
    metrics.dependency_failure("configuration_store", "unavailable")
    assert reader.force_flush() is False
    assert "synthetic-private-" not in caplog.text


def test_shutdown_collects_final_interval_and_closes_exporter_once(exported):
    metrics, reader, sink = exported
    metrics.dependency_failure("configuration_store", "unavailable")
    metrics.close()
    metrics.close()
    assert sink.closed == 1
    assert exported_points(sink.batches[-1])["caller.dependency.failures"].data_points[0].value == 1
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
    metrics.dependency_failure("configuration_store", "unavailable")
    assert reader.force_flush(timeout) is False
    assert not sink.budgets


def test_empty_collection_is_not_successful_delivery(exported):
    metrics, reader, sink = exported
    assert reader.force_flush() is False
    assert not sink.batches
    metrics.dependency_failure("configuration_store", "unavailable")
    assert reader.force_flush()


def test_explicit_delta_collection_starts_no_worker(exported, monkeypatch):
    import threading

    from management_app.metrics import DeltaExportReader, ManagementMetrics

    def denied(*args, **kwargs):
        pytest.fail("metric_reader_spawned_worker")

    monkeypatch.setattr(threading.Thread, "start", denied)
    reader = DeltaExportReader(type(exported[2])())
    metrics = ManagementMetrics(mode="local", reader=reader)
    try:
        metrics.dependency_failure("configuration_store", "unavailable")
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


def test_real_export_covers_requests_errors_latency_dependencies_and_saturation(measured):
    metrics, reader = measured
    metrics.request(route="configuration", method="PUT", status=503, seconds=0.25)
    metrics.dependency_failure("configuration_store", "unavailable")
    metrics.saturation("event_queue", 0.8)
    values = points(reader)
    assert values["caller.requests"][0].value == 1
    assert values["caller.errors"][0].value == 1
    assert values["caller.request.duration"][0].count == 1
    assert values["caller.request.duration"][0].sum == 0.25
    assert values["caller.dependency.failures"][0].attributes == {
        "dependency": "configuration_store",
        "reason": "unavailable",
    }
    assert values["caller.saturation"][0].value == 0.8
    assert values["caller.requests"][0].attributes == {
        "route": "configuration",
        "method": "PUT",
        "status_class": "5xx",
    }


@pytest.mark.parametrize("kind", ["route", "method", "dependency", "reason", "queue"])
def test_unbounded_or_content_bearing_labels_are_rejected_before_recording(measured, kind):
    metrics, reader = measured
    candidate = "synthetic-private-content-session-identifier"
    with pytest.raises(ValueError):
        if kind == "route":
            metrics.request(route=candidate, method="GET", status=200, seconds=0.1)
        elif kind == "method":
            metrics.request(route="configuration", method=candidate, status=200, seconds=0.1)
        elif kind == "dependency":
            metrics.dependency_failure(candidate, "unavailable")
        elif kind == "reason":
            metrics.dependency_failure("configuration_store", candidate)
        else:
            metrics.saturation(candidate, 0.5)
    data = reader.get_metrics_data()
    assert candidate not in repr(data)


@pytest.mark.parametrize("value", [-1.0, math.nan, math.inf, True])
def test_invalid_durations_never_enter_histograms(measured, value):
    metrics, reader = measured
    with pytest.raises(ValueError):
        metrics.request(route="configuration", method="GET", status=200, seconds=value)


@pytest.mark.parametrize("value", [-0.1, 1.1, math.nan, True])
def test_saturation_is_a_finite_bounded_ratio(measured, value):
    metrics, reader = measured
    with pytest.raises(ValueError):
        metrics.saturation("event_queue", value)


def test_unknown_http_paths_collapse_to_one_bounded_label(measured):
    metrics, reader = measured
    assert metrics.route_for("/v1/configuration") == "configuration"
    assert metrics.route_for("/v1/runtime/capabilities/execute") == "capability_execute"
    assert metrics.route_for("/synthetic-private-path/session-one") == "unmatched"


def test_metrics_shutdown_is_idempotent_and_recording_after_close_is_rejected(measured):
    metrics, reader = measured
    metrics.close()
    metrics.close()
    with pytest.raises(RuntimeError, match="metrics_closed"):
        metrics.request(route="configuration", method="GET", status=200, seconds=0.1)


def test_actual_http_lifespan_records_safe_routes_and_errors(measured):
    import asyncio

    import httpx

    from management_app.health import create_health_api, lifecycle
    from management_app.settings import Settings

    metrics, reader = measured
    settings = Settings()
    api = create_health_api(settings, metrics=metrics)

    @api.get("/synthetic-private-error-path")
    async def failed():
        raise RuntimeError("synthetic-private-exception-content")

    async def scenario():
        async with lifecycle(api, settings):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api), base_url="http://test"
            ) as client:
                assert (await client.get("/healthz")).status_code == 200
                assert (await client.get("/synthetic-private-error-path")).status_code == 500
                assert (await client.get("/unmatched/session-secret-fixture")).status_code == 404
            values = points(reader)
            assert sum(point.value for point in values["caller.requests"]) == 3
            assert sum(point.value for point in values["caller.errors"]) == 1
            assert {point.attributes["route"] for point in values["caller.requests"]} == {
                "health",
                "unmatched",
            }
            assert "synthetic-private" not in repr(values) and "session-secret-fixture" not in repr(
                values
            )

    asyncio.run(scenario())


def test_gateway_store_failure_and_deadline_budget_are_real_safe_signals(measured):
    import asyncio
    from datetime import UTC, datetime, timedelta

    from management_app.models.permissions import CapabilityAttempt
    from management_app.observability import Telemetry
    from management_app.services.capability_gateway import CapabilityGateway, GatewayError

    class BrokenRepository:
        async def read_authority(self):
            raise RuntimeError("synthetic-private-dependency-details")

    metrics, reader = measured
    telemetry = Telemetry("management", "local")
    gateway = CapabilityGateway(
        repository=BrokenRepository(),
        policy=None,
        transport=None,
        telemetry=telemetry,
        metrics=metrics,
    )
    request = CapabilityAttempt(
        requestId="private-request",
        sessionId="private-session",
        agentId="private-agent",
        agentEpoch=1,
        pinnedRevisionId="private-revision",
        toolId="private-tool",
        actionId="private-action",
        inputs={},
        deadlineAt=datetime.now(UTC) + timedelta(seconds=10),
    )

    async def scenario():
        with pytest.raises(GatewayError) as failure:
            await gateway.execute(request, actor_id="private-actor")
        assert failure.value.status == 503
        values = points(reader)
        assert values["caller.dependency.failures"][0].attributes == {
            "dependency": "configuration_store",
            "reason": "unavailable",
        }
        assert values["caller.saturation"][0].attributes == {"resource": "dependency_budget"}
        assert 0 <= values["caller.saturation"][0].value <= 1
        assert "private-" not in repr(values)

    try:
        asyncio.run(scenario())
    finally:
        telemetry.close()
