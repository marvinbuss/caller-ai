"""Health contracts run through Reflex's public composed ASGI entry point."""

import json
import re
import time
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from management_app.management_app import create_app
from management_app.settings import Settings

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts/foundation.openapi.json").read_text()
)


@pytest.mark.parametrize("route,schema", [("/healthz", "Health"), ("/readyz", "Readiness")])
def test_composed_local_health_contract(route: str, schema: str) -> None:
    app = create_app(Settings())
    with TestClient(app()) as client:
        started = time.monotonic()
        response = client.get(route, headers={"X-Correlation-ID": "management-001"})
        assert time.monotonic() - started < 1
        assert response.status_code == 200
        jsonschema.validate(
            response.json(),
            {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]},
        )
        assert response.json()["service"] == "management"
        assert response.json()["mode"] == "local"
        assert response.headers["X-Correlation-ID"] == "management-001"
        if route == "/readyz":
            assert response.json()["status"] == "ready"
            assert response.json()["capabilities"] == {
                "live_calls": False,
                "management_configuration": False,
            }
            assert response.json()["reason_codes"] == []
        assert client.get("/ping").status_code == 200
        assert client.get("/v1/configuration").status_code == 404


def test_composed_failure_readiness_and_safe_correlation() -> None:
    app = create_app(Settings())
    with TestClient(app()) as client:
        app.api_transformer.state.ready = False
        response = client.get("/readyz", headers={"X-Correlation-ID": "bad value/secret"})
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
        assert response.json()["reason_codes"] == ["configuration_invalid"]
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", response.headers["X-Correlation-ID"])
        jsonschema.validate(
            response.json(),
            {"$ref": "#/components/schemas/Readiness", "components": CONTRACT["components"]},
        )
        assert client.get("/healthz").status_code == 200


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
    with TestClient(create_app(Settings(), dependencies=dependencies)()) as client:
        assert client.get("/healthz").status_code == 200
        assert dependencies.calls == 0
        started = time.monotonic()
        response = client.get("/readyz")
        assert time.monotonic() - started < 1
        assert response.status_code == 503
        assert response.json()["reason_codes"] == ["dependency_unavailable"]
        assert "sensitive-dependency-details" not in response.text
        assert dependencies.calls == 1
