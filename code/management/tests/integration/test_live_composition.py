"""Live management composition from settings: reviewed fingerprint, stores, identity, readiness."""

import asyncio
import copy
import json
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from management_app.health import lifecycle
from management_app.live import build_live_management, reviewed_fingerprint
from management_app.settings import Settings, StartupError
from tests.integration.test_configuration_persistence import ContainerDouble
from tests.unit.test_live_settings import complete

IDENTITY_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PERMIT_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PERMIT_PEM = PERMIT_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
RUNTIME_CLIENT = "00000000-0000-4000-8000-000000000020"
CLIENT = "00000000-0000-4000-8000-000000000005"
TENANT = "00000000-0000-4000-8000-000000000002"


def values() -> dict[str, str]:
    base = {**complete(), "CALLER_SIGNING_KEY_PEM": PERMIT_PEM}
    return base


def settings_for(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, str] | None = None
) -> Settings:
    entries = {**values(), **(changes or {})}
    for name in values():
        monkeypatch.delenv(name, raising=False)
    for name, value in entries.items():
        monkeypatch.setenv(name, value)
    if "CALLER_REVIEWED_FINGERPRINT" not in (changes or {}):
        monkeypatch.setenv(
            "CALLER_REVIEWED_FINGERPRINT", reviewed_fingerprint(Settings().live(reviewable=True))
        )
    return Settings()


class Credential:
    def __init__(self, **keywords: Any) -> None:
        self.fail = False

    async def get_token(self, *scopes: str, **keywords: Any) -> Any:
        if self.fail:
            raise RuntimeError("synthetic-private-detail")
        return SimpleNamespace(token="synthetic-token", expires_on=time.time() + 900)

    async def close(self) -> None:
        return None


class Audit:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def create_item(self, *, body: dict[str, Any], retry_write: int) -> dict[str, Any]:
        self.records.append(copy.deepcopy(body))
        return body

    async def read_item(self, *arguments: Any, **keywords: Any) -> Any:
        raise RuntimeError("not used")


class Cosmos:
    created: list["Cosmos"] = []
    shared: dict[str, Any] = {}

    def __init__(self, endpoint: str, **keywords: Any) -> None:
        if not Cosmos.shared:
            Cosmos.shared = {"configuration": ContainerDouble(), "configuration-audit": Audit()}
        self.containers = Cosmos.shared
        self.closed = 0
        Cosmos.created.append(self)

    def get_database_client(self, database: str) -> "Cosmos":
        assert database == "caller"
        return self

    def get_container_client(self, name: str) -> Any:
        return self.containers[name]

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture(autouse=True)
def reflex_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("__REFLEX_SKIP_COMPILE", "true")
    monkeypatch.setattr(
        "reflex.utils.js_runtimes.install_frontend_packages",
        lambda *arguments, **keywords: pytest.fail("no_frontend_install_in_backend_composition"),
    )
    Cosmos.created = []
    Cosmos.shared = {}


def identity_client() -> httpx.AsyncClient:
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(IDENTITY_KEY.public_key()))
    public.update({"kid": "identity-key", "use": "sig", "alg": "RS256"})

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
        return httpx.Response(200, json={"keys": [public]})

    return httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)


def runtime_token(**changes: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "tid": TENANT,
        "aud": CLIENT,
        "oid": str(uuid4()),
        "sub": "synthetic-subject",
        "iat": now - 5,
        "nbf": now - 5,
        "exp": now + 300,
        "ver": "2.0",
        "roles": ["Caller.Runtime"],
        "idtyp": "app",
        "azp": RUNTIME_CLIENT,
    }
    claims.update(changes)
    return jwt.encode(claims, IDENTITY_KEY, algorithm="RS256", headers={"kid": "identity-key"})


def compose(settings: Settings, credential: type = Credential) -> Any:
    return build_live_management(
        settings,
        credential_factory=credential,
        cosmos_factory=Cosmos,
        http=identity_client(),
    )


def test_the_reviewed_settings_compose_the_live_app_and_the_runtime_identity_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(monkeypatch)

    async def scenario() -> None:
        managed = await compose(settings)
        async with lifecycle(managed.api, settings):
            transport = httpx.ASGITransport(app=managed.api)
            origin = "https://management.example.azurecontainerapps.io"
            async with httpx.AsyncClient(transport=transport, base_url=origin) as client:
                ready = await client.get("/readyz")
                assert ready.status_code == 200
                assert ready.json()["capabilities"]["management_configuration"] is True
                path = "/v2/runtime/configuration"
                good = await client.get(
                    path, headers={"Authorization": "Bearer " + runtime_token()}
                )
                assert good.status_code == 200 and "etag" in good.headers
                assert (await client.get(path)).status_code == 401
                for changes in (
                    {"roles": ["Caller.Editor"]},
                    {"roles": []},
                ):
                    refused = await client.get(
                        path, headers={"Authorization": "Bearer " + runtime_token(**changes)}
                    )
                    assert refused.status_code == 403
                for changes in ({"azp": str(uuid4())}, {"aud": str(uuid4())}, {"idtyp": "user"}):
                    refused = await client.get(
                        path, headers={"Authorization": "Bearer " + runtime_token(**changes)}
                    )
                    assert refused.status_code == 401
                assert (await client.get("/v2/configuration")).status_code == 401
        assert Cosmos.created[0].closed == 1

    asyncio.run(scenario())
    configuration = Cosmos.created[0].containers["configuration"]
    assert any(name != "security-authority" for name in configuration.items)
    assert "security-authority" in configuration.items


def test_the_first_start_creates_the_protected_agents_and_a_restart_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(monkeypatch)

    async def start() -> None:
        managed = await compose(settings)
        async with lifecycle(managed.api, settings):
            pass

    asyncio.run(start())
    first = copy.deepcopy(Cosmos.created[0].containers["configuration"].items)
    assert first
    asyncio.run(start())
    assert len(Cosmos.created) == 2
    assert Cosmos.created[1].containers["configuration"].items == first


def test_readiness_reports_not_ready_when_the_identity_cannot_reach_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(monkeypatch)

    async def scenario() -> None:
        managed = await compose(settings)
        async with lifecycle(managed.api, settings):
            managed.api.state.management_startup.resources.readiness.credential.fail = True
            transport = httpx.ASGITransport(app=managed.api)
            async with httpx.AsyncClient(transport=transport, base_url="https://x.invalid") as c:
                response = await c.get("/readyz")
        assert response.status_code == 503
        assert response.json()["reason_codes"] == ["dependency_unavailable"]
        assert "synthetic-private-detail" not in response.text

    asyncio.run(scenario())


def test_a_fingerprint_that_differs_stops_the_start_before_any_client_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(monkeypatch, {"CALLER_REVIEWED_FINGERPRINT": "b" * 64})
    with pytest.raises(StartupError) as raised:
        asyncio.run(compose(settings))
    assert raised.value.code == "fingerprint_mismatch" and Cosmos.created == []


def test_an_empty_review_reference_stops_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_for(monkeypatch, {"CALLER_REVIEW_REFERENCE": " "})
    with pytest.raises(StartupError) as raised:
        asyncio.run(compose(settings))
    assert raised.value.code == "review_missing" and Cosmos.created == []


def test_a_private_key_that_is_not_rsa_stops_the_start_with_key_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    settings = settings_for(monkeypatch, {"CALLER_SIGNING_KEY_PEM": pem})
    with pytest.raises(StartupError) as raised:
        asyncio.run(compose(settings))
    assert raised.value.code == "key_invalid"
    assert all(client.closed == 1 for client in Cosmos.created)
