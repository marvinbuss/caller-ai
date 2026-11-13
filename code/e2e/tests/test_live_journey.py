"""The live journey in one process: management and runtime composed from settings with fake ports.

Real code runs end to end: settings validation, both compositions, the reviewed fingerprint, workload
token verification on both sides, the configuration pull, the signed start permit, the console socket,
the text turn, history storage and the shared correlation id. Only Azure is replaced: the stores, the
identity authority (a local signing key and key set) and the model (a streamed Responses API reply).
Browser sign-in is not driven here; the tester application path stands in for the signed-in tester.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import httpx2
import jwt
import pytest
from caller_runtime.live import build_live_runtime
from caller_runtime.main import create_app
from caller_runtime.settings import Settings as RuntimeSettings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from management_app.adapters.backend_connection import correlation_for
from management_app.adapters.configuration_repository import graph_document
from management_app.live import build_live_management, reviewed_fingerprint
from management_app.settings import Settings as ManagementSettings

from tests.doubles import AuditStore, ConfigurationStore, Cosmos, RuntimeStore

TENANT = "00000000-0000-4000-8000-000000000002"
MANAGEMENT_CLIENT = "00000000-0000-4000-8000-000000000005"
RUNTIME_REGISTRATION = "00000000-0000-4000-8000-000000000008"
RUNTIME_IDENTITY = "00000000-0000-4000-8000-000000000020"
MANAGEMENT_IDENTITY = "00000000-0000-4000-8000-000000000030"
MANAGEMENT_PRINCIPAL = "00000000-0000-4000-8000-000000000031"
TESTER_PRINCIPAL = "00000000-0000-4000-8000-000000000041"
MANAGEMENT_HOST = "management.e2e.example.net"
RUNTIME_HOST = "runtime.e2e.example.net"
MANAGEMENT_ORIGIN = f"https://{MANAGEMENT_HOST}"
RUNTIME_ORIGIN = f"https://{RUNTIME_HOST}"
MODEL_REPLY = "Hello from the model fixture."
GREETING = "Welcome to the e2e fixture. How can I help?"
PUBLISHED_PROMPT = "Published by the journey: greet the caller briefly."

IDENTITY_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PERMIT_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PERMIT_PRIVATE = PERMIT_KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
PERMIT_PUBLIC = (
    PERMIT_KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)


def workload_token(audience: str, *roles: str, oid: str, azp: str | None = None) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "tid": TENANT,
        "aud": audience,
        "oid": oid,
        "sub": oid,
        "iat": now - 5,
        "nbf": now - 5,
        "exp": now + 600,
        "ver": "2.0",
        "roles": list(roles),
        "idtyp": "app",
    }
    if azp is not None:
        claims["azp"] = azp
    return jwt.encode(claims, IDENTITY_KEY, algorithm="RS256", headers={"kid": "identity-key"})


def identity_keys() -> httpx.AsyncClient:
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(IDENTITY_KEY.public_key()))
    public.update({"kid": "identity-key", "use": "sig", "alg": "RS256"})

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "login.microsoftonline.com"
        return httpx.Response(200, json={"keys": [public]})

    return httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)


class Credential:
    """Managed identity of one app: workload tokens for the other app, dummy tokens elsewhere."""

    def __init__(self, tokens: dict[str, str]) -> None:
        self.tokens = tokens

    async def get_token(self, *scopes: str, **keywords: Any) -> Any:
        token = next(
            (value for key, value in self.tokens.items() if scopes[0].startswith(key)), "x"
        )
        return SimpleNamespace(token=token, expires_on=time.time() + 600)

    async def close(self) -> None:
        return None


class Late(httpx.AsyncBaseTransport):
    """Routes to an app that is built after the caller."""

    target: httpx.AsyncBaseTransport | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert self.target is not None
        return await self.target.handle_async_request(request)


class Hosts:
    """One ASGI entry for both apps, chosen by host name; lifespan goes to the runtime."""

    def __init__(self, runtime: Any, management: Any) -> None:
        self.runtime, self.apps = runtime, {RUNTIME_HOST: runtime, MANAGEMENT_HOST: management}

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.runtime(scope, receive, send)
            return
        host = dict(scope["headers"]).get(b"host", b"").decode().split(":")[0]
        await self.apps[host](scope, receive, send)


def model_transport(requests: list[dict[str, Any]]) -> httpx2.MockTransport:
    message = {
        "type": "message",
        "id": "msg-e2e",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": MODEL_REPLY, "annotations": []}],
    }
    reply = {
        "id": "resp-e2e",
        "object": "response",
        "created_at": 1793318400,
        "model": "text-e2e",
        "status": "completed",
        "output": [message],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 8,
            "output_tokens": 3,
            "total_tokens": 11,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
    events = [
        {"type": "response.created", "response": {**reply, "output": []}},
        {"type": "response.output_item.added", "output_index": 0, "item": message},
        {
            "type": "response.output_text.delta",
            "item_id": "msg-e2e",
            "output_index": 0,
            "content_index": 0,
            "delta": MODEL_REPLY,
            "logprobs": [],
        },
        {"type": "response.output_item.done", "output_index": 0, "item": message},
        {"type": "response.completed", "response": reply},
    ]

    class Chunks(httpx2.AsyncByteStream):
        async def __aiter__(self):
            # The runtime calls the Responses API, streamed as server-sent events.
            for number, event in enumerate(events):
                yield (
                    "data: " + json.dumps({**event, "sequence_number": number}) + "\n\n"
                ).encode()

    async def dispatch(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        if not body.get("stream"):
            # The Welcome agent's greeting is one non-streamed request.
            greeting = {**message, "content": [{**message["content"][0], "text": GREETING}]}
            return httpx2.Response(200, json={**reply, "output": [greeting]})
        return httpx2.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Chunks())

    return httpx2.MockTransport(dispatch)


def management_environment() -> dict[str, str]:
    return {
        "CALLER_MODE": "live",
        "AZURE_CLIENT_ID": MANAGEMENT_IDENTITY,
        "CALLER_REVIEW_REFERENCE": "review-e2e-001",
        "CALLER_COSMOS_ENDPOINT": "https://cosmos-e2e.documents.azure.com:443/",
        "CALLER_COSMOS_DATABASE": "caller",
        "CALLER_AUDIT_RETENTION_SECONDS": "7776000",
        "CALLER_TENANT_ID": TENANT,
        "CALLER_ENTRA_CLIENT_ID": MANAGEMENT_CLIENT,
        "CALLER_ENTRA_CLIENT_SECRET": "synthetic-sign-in-credential",  # pragma: allowlist secret
        "CALLER_API_AUDIENCE": MANAGEMENT_CLIENT,
        "CALLER_MANAGER_ROLE": "Caller.Editor",
        "CALLER_MANAGER_SCOPE": f"api://{MANAGEMENT_CLIENT}/.default",
        "CALLER_TEST_ROLE": "Caller.Tester",
        "CALLER_RUNTIME_ROLE": "Caller.Runtime",
        "CALLER_MANAGEMENT_ORIGIN": MANAGEMENT_ORIGIN,
        "CALLER_PUBLIC_ORIGIN": MANAGEMENT_ORIGIN,
        "CALLER_RUNTIME_ENDPOINT": RUNTIME_ORIGIN,
        "CALLER_RUNTIME_AUDIENCE": RUNTIME_REGISTRATION,
        "CALLER_RUNTIME_CLIENT_ID": RUNTIME_IDENTITY,
        "CALLER_PERMIT_ISSUER": MANAGEMENT_ORIGIN,
        "CALLER_PERMIT_AUDIENCE": RUNTIME_ORIGIN,
        "CALLER_SIGNING_KEY_ID": "key-1",
        "CALLER_SIGNING_KEY_PEM": PERMIT_PRIVATE,
    }


def runtime_environment() -> dict[str, str]:
    return {
        "CALLER_MODE": "live",
        "AZURE_CLIENT_ID": RUNTIME_IDENTITY,
        "CALLER_REVIEW_REFERENCE": "review-e2e-001",
        "CALLER_COSMOS_ENDPOINT": "https://cosmos-e2e.documents.azure.com:443/",
        "CALLER_COSMOS_DATABASE": "caller",
        "CALLER_TEXT_HISTORY_TTL_SECONDS": "86400",
        "CALLER_MANAGEMENT_ENDPOINT": MANAGEMENT_ORIGIN,
        "CALLER_MANAGEMENT_AUDIENCE": MANAGEMENT_CLIENT,
        "CALLER_TEXT_ENDPOINT": "https://model-e2e.openai.azure.com",
        "CALLER_TEXT_DEPLOYMENT": "text-e2e",
        "CALLER_LIVE_ENDPOINT": "https://model-e2e.services.ai.azure.com",
        "CALLER_LIVE_REGION": "swedencentral",
        "CALLER_LIVE_VOICE": "alloy",
        "CALLER_APPROVED_HOSTS": f"{MANAGEMENT_HOST},model-e2e.openai.azure.com,"
        "model-e2e.services.ai.azure.com",
        "CALLER_MAX_INPUT_TOKENS": "32000",
        "CALLER_MAX_OUTPUT_TOKENS": "1000",
        "CALLER_AVAILABLE_LIVE_SESSIONS": "20",
        "CALLER_MANAGER_ISSUER": MANAGEMENT_ORIGIN,
        "CALLER_MANAGER_AUDIENCE": RUNTIME_ORIGIN,
        "CALLER_PERMIT_VERIFICATION_KEYS": json.dumps({"key-1": PERMIT_PUBLIC}),
        "CALLER_MAX_CLOCK_SKEW_SECONDS": "1",
        "CALLER_TENANT_ID": TENANT,
        "CALLER_WORKLOAD_AUDIENCE": RUNTIME_REGISTRATION,
        "CALLER_WORKLOAD_ROLE": "Caller.Management",
        "CALLER_WORKLOAD_PRINCIPAL_ID": MANAGEMENT_PRINCIPAL,
    }


def load[T](monkeypatch: pytest.MonkeyPatch, values: dict[str, str], settings: type[T]) -> T:
    for name in {*management_environment(), *runtime_environment()}:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return settings()


class Journey:
    """The composed world; `management` and `runtime` are the apps' real ASGI applications."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.model_requests: list[dict[str, Any]] = []
        self.configuration, self.audit = ConfigurationStore(), AuditStore()
        self.runtime_stores = {
            "call-sessions": RuntimeStore(),
            "text-conversations": RuntimeStore("ownerKey"),
        }
        management_settings = load(monkeypatch, management_environment(), ManagementSettings)
        monkeypatch.setenv(
            "CALLER_REVIEWED_FINGERPRINT",
            reviewed_fingerprint(management_settings.live(reviewable=True)),
        )
        management_settings = ManagementSettings()
        runtime_settings = load(monkeypatch, runtime_environment(), RuntimeSettings)
        late = Late()
        self.management = asyncio.run(
            build_live_management(
                management_settings,
                credential_factory=lambda **keywords: Credential(
                    {
                        f"api://{RUNTIME_REGISTRATION}": workload_token(
                            RUNTIME_REGISTRATION, "Caller.Management", oid=MANAGEMENT_PRINCIPAL
                        )
                    }
                ),
                cosmos_factory=lambda endpoint, **keywords: Cosmos(
                    {"configuration": self.configuration, "configuration-audit": self.audit}
                ),
                http=identity_keys(),
                test_transport=late,
            )
        )
        composition = build_live_runtime(
            runtime_settings,
            credential_factory=lambda **keywords: Credential(
                {
                    f"api://{MANAGEMENT_CLIENT}": workload_token(
                        MANAGEMENT_CLIENT,
                        "Caller.Runtime",
                        oid=RUNTIME_IDENTITY,
                        azp=RUNTIME_IDENTITY,
                    )
                }
            ),
            cosmos_factory=lambda endpoint, **keywords: Cosmos(self.runtime_stores),
            http=identity_keys(),
            configuration_transport=httpx.ASGITransport(app=self.management.api),
            text_transport=model_transport(self.model_requests),
        )
        self.runtime = create_app(
            runtime_settings, dependencies=composition.dependencies, runtime=composition.runtime
        )
        late.target = httpx.ASGITransport(app=self.runtime)

    def publish(self, prompt: str) -> None:
        """An editor publishes: the welcome agent gets a new prompt (through the repository)."""

        async def publish() -> None:
            repository = self.management.api.state.management_repository
            active = await repository.read_active()
            document, etag = active
            graph = graph_document(document)
            welcome = next(agent for agent in graph["agents"] if agent["role"] == "welcome")
            welcome["prompt"] = prompt
            graph["agents"].append(
                {
                    "agentId": "agent-alpha",
                    "name": "Alpha",
                    "prompt": "Alpha prompt",
                    "enabled": True,
                    "role": "ordinary",
                    "communityId": "community-alpha",
                    "capabilityAssignments": [],
                }
            )
            graph["communities"] = [
                {
                    "communityId": "community-alpha",
                    "name": "alpha",
                    "routingDescription": "Alpha routing",
                    "enabled": True,
                    "entryAgentId": "agent-alpha",
                }
            ]
            assert await repository.publish(graph, if_match=etag, actor_id="e2e-editor")

        asyncio.run(publish())


def bearer_headers() -> dict[str, str]:
    token = workload_token(MANAGEMENT_CLIENT, "Caller.Tester", oid=TESTER_PRINCIPAL)
    return {"Authorization": f"Bearer {token}"}


def receive_until(socket: Any, kind: str) -> list[dict[str, Any]]:
    seen = []
    for _ in range(20):
        message = json.loads(socket.receive_text())
        seen.append(message)
        if message["type"] == kind:
            return seen
        assert message["type"] != "error", json.dumps(message)
    raise AssertionError(f"no {kind} message: {[item['type'] for item in seen]}")


def test_a_tester_gets_a_model_reply_through_both_apps_and_one_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = Journey(monkeypatch)
    world.publish(PUBLISHED_PROMPT)
    # The management lifespan is not started: the runtime's pull below proves it serves.
    world.management.api.state.ready = True
    hosts = Hosts(world.runtime, world.management.api)

    with TestClient(hosts, base_url=MANAGEMENT_ORIGIN) as client:
        ready = client.get("/readyz", headers={"Host": RUNTIME_HOST})
        assert ready.status_code == 200, ready.text

        configuration = client.get("/v2/configuration", headers=bearer_headers())
        assert configuration.status_code == 200
        assert any(agent["role"] == "welcome" for agent in configuration.json()["agents"])

        ticket = client.post(
            "/v1/console/tickets",
            json={"mode": "text", "requestId": "journey-1"},
            headers=bearer_headers(),
        )
        assert ticket.status_code == 200, ticket.text
        permit = ticket.json()["permit"]

        with client.websocket_connect(
            f"wss://{MANAGEMENT_HOST}" + ticket.json()["socketPath"],
            subprotocols=["caller.console.v1"],
            headers=bearer_headers(),
        ) as socket:
            socket.send_text(
                json.dumps({"type": "authenticate", "permit": permit, "requestId": "journey-1"})
            )
            opened = receive_until(socket, "ready")[-1]
            assert opened["mode"] == "text"
            session = opened["sessionId"]
            # The conversation opens with the Welcome agent's greeting.
            greeting = receive_until(socket, "text.completed")
            assert "".join(m["text"] for m in greeting if m["type"] == "text.delta") == GREETING
            socket.send_text(
                json.dumps(
                    {
                        "type": "text.submit",
                        "sessionId": session,
                        "sequence": 1,
                        "text": "Hello, who is there?",
                    }
                )
            )
            reply = receive_until(socket, "text.completed")
            assert "".join(m["text"] for m in reply if m["type"] == "text.delta") == MODEL_REPLY
            socket.send_text(json.dumps({"type": "stop", "sessionId": session, "sequence": 2}))
            ended = receive_until(socket, "ended")[-1]
            assert ended["cleanupStatus"] == "complete"
        # Shutdown clears the runtime's in-memory events, so read them while it runs.
        runtime_events = tuple(world.runtime.state.telemetry.events)

    expected = correlation_for("journey-1")
    assert ended["correlationId"] == expected
    management_events = world.management.api.state.telemetry.events
    assert any(
        event["event"] == "test.relay" and event["correlation_id"] == expected
        for event in management_events
    )
    assert any(
        event["event"] == "request" and event["correlation_id"] == expected
        for event in runtime_events
    )
    greeting_requests = [request for request in world.model_requests if not request.get("stream")]
    work_requests = [request for request in world.model_requests if request.get("stream")]
    assert len(greeting_requests) == 1 and len(work_requests) == 1
    assert PUBLISHED_PROMPT in json.dumps(work_requests[0]["instructions"])
    stored = world.runtime_stores["text-conversations"].records
    assert len(stored) == 1 and [t["role"] for t in next(iter(stored.values()))["turns"]] == [
        "assistant",
        "user",
        "assistant",
    ]
    assert "synthetic-sign-in-credential" not in json.dumps(world.audit.records)
