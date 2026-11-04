"""Runtime health must satisfy the accepted contract without external services."""

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from fastapi.testclient import TestClient

from caller_runtime.main import create_app
from caller_runtime.settings import Settings

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts/foundation.openapi.json").read_text()
)


@pytest.mark.parametrize("route,schema", [("/healthz", "Health"), ("/readyz", "Readiness")])
def test_local_health_contract(route: str, schema: str) -> None:
    app = create_app(Settings())
    with TestClient(app) as client:
        started = time.monotonic()
        response = client.get(route, headers={"X-Correlation-ID": "contract-001"})
        assert time.monotonic() - started < 1
        assert response.status_code == 200
        jsonschema.validate(
            response.json(),
            {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]},
        )
        assert response.json()["service"] == "caller-runtime"
        assert response.json()["mode"] == "local"
        assert response.headers["X-Correlation-ID"] == "contract-001"
        if route == "/readyz":
            assert response.json()["status"] == "ready"
            assert response.json()["capabilities"] == {
                "live_calls": False,
                "management_configuration": False,
            }
            assert response.json()["reason_codes"] == []
    assert app.state.ready is False


@pytest.mark.parametrize("correlation", ["bad value/secret", "x" * 65, ""])
def test_untrusted_correlation_is_replaced(correlation: str) -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/healthz", headers={"X-Correlation-ID": correlation})
        safe = response.headers["X-Correlation-ID"]
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", safe)
        assert safe != correlation


def test_failure_readiness_and_liveness_are_distinct() -> None:
    app = create_app(Settings())
    with TestClient(app) as client:
        app.state.ready = False
        response = client.get("/readyz")
        assert response.status_code == 503
        jsonschema.validate(
            response.json(),
            {"$ref": "#/components/schemas/Readiness", "components": CONTRACT["components"]},
        )
        assert response.json()["status"] == "not_ready"
        assert response.json()["reason_codes"] == ["configuration_invalid"]
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/configuration").status_code == 404


@pytest.mark.parametrize("behavior", ["unavailable", "failure", "timeout"])
def test_injected_dependencies_fail_safely_without_affecting_liveness(behavior: str) -> None:
    import asyncio

    class Dependencies:
        calls = 0

        async def ready(self) -> bool:
            self.calls += 1
            if behavior == "failure":
                raise RuntimeError("sensitive-dependency-details")
            if behavior == "timeout":
                await asyncio.Event().wait()
            return False

    dependencies = Dependencies()
    with TestClient(create_app(Settings(), dependencies=dependencies)) as client:
        assert client.get("/healthz").status_code == 200
        assert dependencies.calls == 0
        started = time.monotonic()
        response = client.get("/readyz")
        assert time.monotonic() - started < 1
        assert response.status_code == 503
        assert response.json()["reason_codes"] == ["dependency_unavailable"]
        assert "sensitive-dependency-details" not in response.text
        assert dependencies.calls == 1


@pytest.mark.parametrize("healthy", [True, False])
def test_readiness_reads_only_the_cached_history_probe(healthy: bool) -> None:
    app = create_app(Settings())
    with TestClient(app) as client:
        checks = []

        def history_ready() -> bool:
            checks.append(True)
            return healthy

        app.state.runtime = SimpleNamespace(history_ready=history_ready)
        response = client.get("/readyz")
        jsonschema.validate(
            response.json(),
            {"$ref": "#/components/schemas/Readiness", "components": CONTRACT["components"]},
        )
        assert checks == [True]
        assert response.status_code == (200 if healthy else 503)
        assert response.json()["reason_codes"] == ([] if healthy else ["history_store_unavailable"])
        assert client.get("/healthz").status_code == 200
