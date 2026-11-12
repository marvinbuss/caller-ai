"""`GET /v1/status`: workload-only, reuses probes for 60 s, never names a secret."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from azure.core.credentials import AccessToken
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection

from caller_runtime.adapters import status_probe
from caller_runtime.adapters.status_probe import StatusProbe
from caller_runtime.api.status import StatusBindings, create_router
from caller_runtime.domain.voices import CATALOG
from caller_runtime.observability import Telemetry

BASE = "https://runtime.fixture.example"
BINDINGS = StatusBindings(
    text_host="caller-int-foundry.openai.azure.com",
    text_deployment="gpt-6-1-sol",
    live_host="caller-int-live.openai.azure.com",
    live_region="swedencentral",
    max_output_tokens=1024,
    voice="quartz",
)


class Workload:
    async def require_runtime(self, request: HTTPConnection) -> str:
        if request.headers.get("authorization") != "Bearer workload":
            raise HTTPException(401)
        return "management-principal"


class Credential:
    def __init__(self) -> None:
        self.scopes: list[tuple[str, ...]] = []

    async def get_token(self, *scopes: str) -> AccessToken:
        self.scopes.append(scopes)
        return AccessToken("secret-token-value", 4_000_000_000)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(connect, *, clock: Clock | None = None, telemetry: Telemetry | None = None):
    credential = Credential()
    probe = StatusProbe(
        credential=lambda: credential,
        hosts={"text": BINDINGS.text_host, "voice": BINDINGS.live_host},
        telemetry=lambda: telemetry,
        utc_now=lambda: datetime(2026, 11, 11, 10, tzinfo=UTC),
        monotonic=clock or Clock(),
        connect=connect,
    )
    application = FastAPI()
    application.include_router(
        create_router(workload_identity=Workload(), probe=lambda: probe, bindings=BINDINGS)
    )
    return TestClient(application, base_url=BASE), probe, credential


def counting():
    hosts: list[str] = []

    async def connect(host: str) -> None:
        hosts.append(host)

    return connect, hosts


AUTH = {"Authorization": "Bearer workload"}


def test_status_requires_the_workload_token() -> None:
    connect, hosts = counting()
    client, _probe, _credential = build(connect)
    response = client.get("/v1/status")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert client.get("/v1/status", headers={"Authorization": "Bearer other"}).status_code == 401
    assert hosts == []


def test_status_refuses_plain_http() -> None:
    connect, _hosts = counting()
    _client, probe, _credential = build(connect)
    application = FastAPI()
    application.include_router(
        create_router(workload_identity=Workload(), probe=lambda: probe, bindings=BINDINGS)
    )
    response = TestClient(application, base_url="http://runtime.fixture.example").get(
        "/v1/status", headers=AUTH
    )
    assert response.status_code == 403


def test_status_body_matches_the_contract_and_names_no_secret() -> None:
    connect, hosts = counting()
    client, probe, credential = build(connect)
    probe.record_call("text", True)
    response = client.get("/v1/status", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"text", "voice", "supportedVoices", "defaults", "limits"}
    assert body["text"] == {
        "endpointHost": "caller-int-foundry.openai.azure.com",
        "deployment": "gpt-6-1-sol",
        "reachable": True,
        "checkedAt": "2026-11-11T10:00:00Z",
        "lastCallOutcome": "success",
        "lastCallAt": "2026-11-11T10:00:00Z",
    }
    assert body["voice"] == {
        "endpointHost": "caller-int-live.openai.azure.com",
        "region": "swedencentral",
        "model": "gpt-live-1",
        "reachable": True,
        "checkedAt": "2026-11-11T10:00:00Z",
        "lastCallOutcome": None,
        "lastCallAt": None,
    }
    assert [voice["id"] for voice in body["supportedVoices"]] == [voice.id for voice in CATALOG]
    assert set(body["supportedVoices"][0]) == {"id", "name", "language", "accent", "presentation"}
    # The environment voice (CALLER_LIVE_VOICE), not the catalog's own default.
    assert body["defaults"] == {
        "voice": "quartz",
        "maxOutputTokens": 1024,
        "idleTimeoutSeconds": 120,
    }
    assert body["limits"] == {
        "maxOutputTokens": 1024,
        "idleTimeoutSeconds": {"min": 30, "max": 120},
    }
    text = json.dumps(body).lower()
    for secret in ("secret-token-value", 'token"', "key", "https://", "password"):
        assert secret not in text
    assert sorted(hosts) == sorted([BINDINGS.text_host, BINDINGS.live_host])
    assert credential.scopes == [("https://ai.azure.com/.default",)] * 2


def test_probe_results_are_reused_for_sixty_seconds() -> None:
    connect, hosts = counting()
    clock = Clock()
    client, _probe, _credential = build(connect, clock=clock)
    for _ in range(3):
        assert client.get("/v1/status", headers=AUTH).status_code == 200
    assert len(hosts) == 2
    clock.now += 59
    client.get("/v1/status", headers=AUTH)
    assert len(hosts) == 2
    clock.now += 2
    client.get("/v1/status", headers=AUTH)
    assert len(hosts) == 4


def test_a_probe_that_exceeds_two_seconds_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert status_probe.PROBE_SECONDS == 2
    monkeypatch.setattr(status_probe, "PROBE_SECONDS", 0.05)

    async def hang(host: str) -> None:
        await asyncio.sleep(10)

    client, _probe, _credential = build(hang)
    body = client.get("/v1/status", headers=AUTH).json()
    assert body["text"]["reachable"] is False
    assert body["voice"]["reachable"] is False


def test_a_failed_token_or_connection_is_unreachable() -> None:
    async def refuse(host: str) -> None:
        raise OSError("refused")

    client, probe, _credential = build(refuse)
    probe.record_call("voice", False)
    body = client.get("/v1/status", headers=AUTH).json()
    assert body["text"]["reachable"] is False
    assert body["voice"]["lastCallOutcome"] == "failure"

    missing = StatusProbe(
        credential=lambda: None,
        hosts={"text": "a", "voice": "b"},
        telemetry=lambda: None,
        connect=refuse,
    )
    assert asyncio.run(missing.check("text"))[0] is False


def test_one_probe_per_model_runs_at_a_time() -> None:
    started: list[str] = []

    async def slow(host: str) -> None:
        started.append(host)
        await asyncio.sleep(0.05)

    probe = StatusProbe(
        credential=Credential,
        hosts={"text": "text-host", "voice": "voice-host"},
        telemetry=lambda: None,
        connect=slow,
    )

    async def many() -> list[tuple[bool, str | None]]:
        return await asyncio.gather(*(probe.check("text") for _ in range(5)))

    results = asyncio.run(many())
    assert started == ["text-host"]
    assert all(reachable for reachable, _ in results)


def test_each_probe_emits_a_status_probe_event() -> None:
    connect, _hosts = counting()
    telemetry = Telemetry("caller-runtime", "local")
    client, _probe, _credential = build(connect, telemetry=telemetry)
    client.get("/v1/status", headers=AUTH)
    events = [event for event in telemetry.events if event["event"] == "status.probe"]
    assert {event["measurement"] for event in events} == {
        "text_probe_seconds",
        "voice_probe_seconds",
    }
    assert all(event["outcome"] == "success" for event in events)
    assert "caller-int" not in json.dumps(events)


def test_an_unreadable_probe_state_is_503() -> None:
    async def broken(probe, bindings):
        raise RuntimeError("probe state")

    application = FastAPI()
    application.include_router(
        create_router(
            workload_identity=Workload(),
            probe=lambda: SimpleNamespace(),
            bindings=BINDINGS,
            body=broken,
        )
    )
    response = TestClient(application, base_url=BASE).get("/v1/status", headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "status_unavailable"}}
