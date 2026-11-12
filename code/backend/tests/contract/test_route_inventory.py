"""The runtime serves exactly five routes; every retired call-era route is gone."""

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from caller_runtime.main import create_app
from caller_runtime.metrics import ROUTE_PATHS, RuntimeMetrics
from tests.integration.test_conversation import reviewed_runtime_options as reviewed_runtime_options
from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store
from tests.unit.test_admission import signed_test_permit as signed_test_permit

LIVE_ROUTES = {
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("POST", "/v1/conversations/text"),
    ("WEBSOCKET", "/v1/conversations/voice"),
    ("GET", "/v1/status"),
}
RETIRED_PATHS = (
    "/v1/internal/test-sessions",
    "/v1/calls/call-1/media",
    "/v1/provider/callbacks",
    "/v1/operations/drain/start",
    "/v1/operations/drain/status",
    "/v1/dev/transport",
    "/openapi.json",
    "/docs",
)


def runtime(options, public):
    from caller_runtime.composition import ConversationRuntime, RuntimeOptions

    return ConversationRuntime(
        options=RuntimeOptions(**options),
        owner_id="runtime-fixture-owner",
        container_factory=AsyncMock(),
        credential_factory=AsyncMock(),
        workload_identity=SimpleNamespace(require_runtime=AsyncMock()),
        verification_keys={"manager-fixture-key": public},
        utc_now=lambda: datetime.now(UTC),
        monotonic=time.monotonic,
    )


def declared(application) -> set[tuple[str, str]]:
    """List every route, descending into included routers."""
    found: set[tuple[str, str]] = set()

    def walk(routes) -> None:
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                walk(included.routes)
            elif isinstance(route, APIRoute):
                found.update((method, route.path) for method in route.methods - {"HEAD"})
            elif isinstance(route, APIWebSocketRoute):
                found.add(("WEBSOCKET", route.path))
            else:
                found.add(("OTHER", getattr(route, "path", repr(route))))

    walk(application.routes)
    return found


def test_composed_runtime_serves_exactly_the_four_live_routes(
    reviewed_runtime_options, signed_test_permit
):
    application = create_app(runtime=runtime(reviewed_runtime_options, signed_test_permit[0]))
    assert declared(application) == LIVE_ROUTES


def test_default_runtime_serves_only_the_health_routes():
    assert declared(create_app()) == {("GET", "/healthz"), ("GET", "/readyz")}


@pytest.mark.parametrize("path", RETIRED_PATHS)
def test_retired_routes_are_not_found(path):
    with TestClient(create_app(), base_url="https://runtime.fixture.example") as client:
        for method in ("GET", "POST"):
            assert client.request(method, path).status_code == 404
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path):
                pytest.fail("retired_websocket_route_accepted")


def test_metric_routes_know_only_the_live_paths():
    assert set(ROUTE_PATHS) == {path for _, path in LIVE_ROUTES}
    for path in RETIRED_PATHS:
        assert RuntimeMetrics.route_for(path) == "unmatched"
