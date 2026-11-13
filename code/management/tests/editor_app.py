"""Owned HTTPS browser-test host; fixture sessions cannot be loaded by the shipped app."""

import asyncio
import json
import os
import secrets
import ssl
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from reflex.config import get_config
from starlette.responses import JSONResponse, RedirectResponse

import management_app.security as management_security
from management_app.adapters.audit_repository import LocalAuditRepository
from management_app.adapters.backend_connection import TurnPermits
from management_app.adapters.configuration_repository import (
    LocalConfigurationRepository,
    verified_revision,
)
from management_app.adapters.test_session_client import TestSessionClient, direct_socket
from management_app.adapters.text_relay import ActiveRevision
from management_app.api.configuration import create_router
from management_app.api.runtime_configuration import create_router as runtime_router
from management_app.api.status import create_router as status_router
from management_app.api.test_sessions import create_router as test_router
from management_app.health import create_health_api
from management_app.management_app import create_app, editor_asgi
from management_app.models.capabilities import ApprovedCapabilities
from management_app.observability import Telemetry
from management_app.reflex_security import ReflexEventGuard
from management_app.security import (
    EntraPolicy,
    IdentityService,
    LoginFlow,
    Principal,
    ServerSession,
    SessionStore,
    TestPermitSigner,
    TokenVerifier,
    create_auth_router,
)
from management_app.services.audit import AuditService
from management_app.services.configuration_publication import runtime_projection
from management_app.services.configuration_validation import ConfigurationValidator
from management_app.settings import Settings
from management_app.state.editor_draft import EditorServices
from management_app.state.test_console import ConsoleServices
from tests.conftest import capability_graph_document

get_config().app_module_import = "tests.editor_app"
fixture_path = Path(os.environ["CALLER_EDITOR_TEST_FIXTURE"])
fixture = json.loads(fixture_path.read_text())
backend_path = Path(__file__).resolve().parents[3] / "code/backend"
settings = Settings()
if fixture["mode"] != "local" or settings.mode != "local":
    raise ValueError("test_identity_live_mode_prohibited")

origin = fixture["origin"]
fixture_namespace = uuid5(NAMESPACE_URL, fixture_path.resolve().as_uri())
policy = EntraPolicy(
    tenant_id=str(uuid5(fixture_namespace, "tenant")),
    client_id=str(uuid5(fixture_namespace, "client")),
    api_audience=str(uuid5(fixture_namespace, "api")),
    origin=origin,
    manager_role="Configuration.Manager",
    manager_scope="Configuration.ReadWrite",
)


identity_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FixtureKeys:
    """The fixture's own signing key; the test host never reaches an identity provider."""

    async def signing_keys(self):
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(identity_key.public_key()))
        public.update({"kid": "fixture-identity-key", "use": "sig", "alg": "RS256"})
        return {"keys": [public]}


class FixtureOidc:
    """Sign-in double: the browser test answers the authorize URL with a form post.

    The `code` names the fixture user and roles as `<name>:<roles>` (roles joined by `+`,
    `none` for neither); `fail` makes the exchange fail like a cancelled sign-in.
    """

    async def begin(self, state):
        nonce = secrets.token_urlsafe(16)
        return LoginFlow(
            url=f"https://login.microsoftonline.com/{policy.tenant_id}/oauth2/v2.0/authorize"
            f"?state={state}&client_id={policy.client_id}",
            state=state,
            private=nonce,
        )

    async def finish(self, flow, response):
        name, _, roles = response.get("code", "").partition(":")
        if name not in fixture["identities"] or roles == "fail":
            raise ValueError("fixture_sign_in_failed")
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": policy.issuer,
                "tid": policy.tenant_id,
                "aud": policy.client_id,
                "oid": str(uuid5(fixture_namespace, "manager/" + name)),
                "sub": "fixture-" + name,
                "iat": now - 5,
                "nbf": now - 5,
                "exp": now + 3600,
                "ver": "2.0",
                "nonce": flow.private,
                "roles": fixture_roles(roles),
                "name": "Fixture " + name,
                "preferred_username": name + "@fixture.example",
            },
            identity_key,
            algorithm="RS256",
            headers={"kid": "fixture-identity-key"},
        )
        return token, flow.private


test_role = "Conversation.Tester"


def fixture_roles(value: str) -> list[str]:
    names = {"editor": policy.manager_role, "tester": test_role}
    requested = [] if value in {"", "none"} else value.split("+")
    if any(name not in names for name in requested):
        raise HTTPException(400, "unknown_fixture_role")
    return [names[name] for name in requested]


sessions = SessionStore(policy=policy)
reviewed = ApprovedCapabilities.from_reviewed(capability_graph_document())
identity = IdentityService(
    policy=policy, verifier=TokenVerifier(policy=policy, keys=FixtureKeys()), sessions=sessions
)
validator = ConfigurationValidator(
    policy=json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/secret-detection-policy.json").read_text()
    ),
    approved_secret_aliases=frozenset({"fixture-connection"}),
    approved_destination_refs=frozenset({"fixture-public-endpoint"}),
    reviewed=reviewed,
)
repository = LocalConfigurationRepository(validator=validator)
guard = ReflexEventGuard(identity=identity)
api = create_health_api(settings)
api.include_router(
    create_router(
        repository=repository,
        identity=identity,
        validator=validator,
        audit=AuditService(repository=LocalAuditRepository()),
    )
)
test_telemetry = Telemetry("management", "local")
api.include_router(
    create_auth_router(
        identity=identity, oidc=FixtureOidc(), test_role=test_role, telemetry=test_telemetry
    )
)
faults: dict[str, dict[str, float]] = {}


@api.middleware("http")
async def fixture_faults(request, call_next):
    # Delayed or failing responses for the page loading and error states.
    fault = faults.get(request.url.path)
    if fault and request.method == "GET":
        await asyncio.sleep(fault.get("delay", 0))
        if fault.get("fail"):
            return JSONResponse({"code": "fixture_failure"}, status_code=503)
    return await call_next(request)


@api.get("/_test/fault")
async def fixture_fault(path: str, delay: float = 0, fail: bool = False):
    if path not in {"/v2/configuration", "/v1/status"} or not 0 <= delay <= 10:
        raise HTTPException(400, "unknown_fixture_fault")
    if delay == 0 and not fail:
        faults.pop(path, None)
    else:
        faults[path] = {"delay": delay, "fail": float(fail)}
    return {"faults": faults}


@api.get("/_test/tester-only/{enabled}")
async def fixture_tester_only(enabled: bool):
    management_security.TESTER_ONLY_TESTS = enabled
    return {"testerOnlyTests": enabled}


test_key_path = fixture_path.parent / "test-signing.pem"
test_key = (
    serialization.load_pem_private_key(test_key_path.read_bytes(), password=None)
    if test_key_path.exists()
    else rsa.generate_private_key(public_exponent=65537, key_size=2048)
)
test_telemetry = Telemetry("management", "local")
test_signer = TestPermitSigner(
    issuer=origin,
    audience="caller-browser-test-fixture",
    key_id="fixture-test-key",
    signing_key=test_key,
    verification_keys={"fixture-test-key": test_key.public_key()},
    utc_now=lambda: datetime.now(UTC),
    max_clock_skew_seconds=0.1,
)


async def active_settings():
    publication = await repository.read_active()
    return dict((publication[0] if publication else {}).get("conversationSettings") or {})


api.state.test_console_services = ConsoleServices(
    guard=guard, test_role=test_role, active_settings=active_settings
)


class FixtureWorkload:
    async def require_runtime(self, request):
        if request.headers.get("authorization") != "Bearer synthetic-browser-workload":
            raise HTTPException(401, "fixture_workload_required")
        return "owned-browser-runtime"


class FixtureCredential:
    async def get_token(self, *scopes):
        from azure.core.credentials import AccessToken

        return AccessToken("synthetic-browser-workload", int(time.time()) + 900)


async def fixture_socket(url, **options):
    if url != "wss://runtime.fixture.example/v1/conversations/voice" or not fixture.get(
        "runtime_port"
    ):
        raise ValueError("owned_runtime_binding_required")
    options["ssl"] = ssl.create_default_context(cafile=str(fixture_path.parent / "certificate.pem"))
    return await direct_socket(
        f"wss://127.0.0.1:{fixture['runtime_port']}/v1/conversations/voice", **options
    )


class FixtureRuntimeTransport(httpx.AsyncBaseTransport):
    """Sends the console's text requests to the owned runtime process over verified TLS."""

    def __init__(self):
        self._inner = None

    async def handle_async_request(self, request):
        if request.url.host != "runtime.fixture.example" or not fixture.get("runtime_port"):
            raise ValueError("owned_runtime_binding_required")
        if self._inner is None:
            # Built on first use: the certificate is created after this module is imported.
            context = ssl.create_default_context(
                cafile=str(fixture_path.parent / "certificate.pem")
            )
            self._inner = httpx.AsyncHTTPTransport(verify=context, trust_env=False)
        request.url = request.url.copy_with(host="127.0.0.1", port=fixture["runtime_port"])
        return await self._inner.handle_async_request(request)


async def fixture_revision():
    current = await repository.read_active()
    document = current[0]
    welcome = next(agent["agentId"] for agent in document["agents"] if agent["role"] == "welcome")
    idle = (document.get("conversationSettings") or {}).get("idleTimeoutSeconds", 120)
    return ActiveRevision(document["id"], document["revisionNumber"], welcome, int(idle))


test_relay = (
    TestSessionClient(
        endpoint="https://runtime.fixture.example",
        audience=str(uuid5(fixture_namespace, "runtime")),
        approved_hosts=frozenset({"runtime.fixture.example"}),
        credential=FixtureCredential(),
        utc_now=lambda: datetime.now(UTC),
        monotonic=time.monotonic,
        max_clock_skew_seconds=0.1,
        socket_factory=fixture_socket,
        telemetry=test_telemetry,
        permits=TurnPermits(test_signer, lambda: datetime.now(UTC)),
        active_revision=fixture_revision,
        http_transport=FixtureRuntimeTransport(),
    )
    if fixture.get("runtime_port")
    else None
)
api.include_router(
    runtime_router(repository=repository, identity=FixtureWorkload(), telemetry=test_telemetry)
)
api.include_router(
    test_router(
        identity=identity,
        signer=test_signer,
        test_role=test_role,
        telemetry=test_telemetry,
        monotonic=time.monotonic,
        max_clock_skew_seconds=0.1,
        relay=test_relay,
    )
)
# The runtime status double: "up", "voice-down" or "down"; switching it expires the cache.
status_double = {"mode": "up", "offset": 0.0}
STATUS_VOICES = [
    {"id": "marin", "name": "Marin", "language": "English", "accent": None, "presentation": None},
    {
        "id": "quartz",
        "name": "Quartz",
        "language": "English",
        "accent": "Australian",
        "presentation": "Feminine",
    },
    {
        "id": "vesper",
        "name": "Vesper",
        "language": "English",
        "accent": "British",
        "presentation": "Masculine",
    },
    {
        "id": "nuri",
        "name": "Nuri",
        "language": "German",
        "accent": None,
        "presentation": "Feminine",
    },
    {
        "id": "brise",
        "name": "Brise",
        "language": "French",
        "accent": None,
        "presentation": "Feminine",
    },
]


async def fixture_status():
    if status_double["mode"] == "down":
        raise PermissionError("runtime_status_unavailable")
    checked = "2026-11-11T10:00:00Z"
    return {
        "text": {
            "endpointHost": "caller-fixture-foundry.openai.azure.com",
            "deployment": "gpt-fixture-text",
            "reachable": True,
            "checkedAt": checked,
            "lastCallOutcome": "success",
            "lastCallAt": "2026-11-11T09:58:12Z",
        },
        "voice": {
            "endpointHost": "caller-fixture-foundry.openai.azure.com",
            "region": "swedencentral",
            "model": "gpt-live-1",
            "reachable": status_double["mode"] != "voice-down",
            "checkedAt": checked,
            "lastCallOutcome": None,
            "lastCallAt": None,
        },
        "supportedVoices": STATUS_VOICES,
        "defaults": {"voice": "marin", "maxOutputTokens": 1024, "idleTimeoutSeconds": 120},
        "limits": {"maxOutputTokens": 1024, "idleTimeoutSeconds": {"min": 30, "max": 120}},
    }


api.include_router(
    status_router(
        identity=identity,
        fetch=fixture_status,
        test_role=test_role,
        telemetry=test_telemetry,
        monotonic=lambda: time.monotonic() + status_double["offset"],
        on_status=validator.remember_status,
    )
)


@api.get("/_test/status/{mode}")
async def fixture_status_mode(mode: str):
    if mode not in {"up", "voice-down", "down"}:
        raise HTTPException(400, "unknown_fixture_status")
    status_double["mode"] = mode
    status_double["offset"] = float(status_double["offset"]) + 120
    return {"mode": mode}


@api.get("/_test/login/{name}")
async def fixture_login(
    name: str,
    target: str = "/communities",
    roles: str = "editor+tester",
    session_seconds: int = 900,
    token_seconds: int = 3600,
):
    if name not in fixture["identities"]:
        raise HTTPException(404, "unknown_fixture_manager")
    if management_security.return_path(target) is None and target != "/login":
        raise HTTPException(400, "unknown_fixture_target")
    if not 1 <= session_seconds <= 900 or not 1 <= token_seconds <= 7200:
        raise HTTPException(400, "invalid_fixture_expiry")
    actor = str(uuid5(fixture_namespace, "manager/" + name))
    principal = Principal(
        actor,
        policy.tenant_id,
        int(time.time()) + token_seconds,
        tuple(fixture_roles(roles)),
        "Fixture " + name,
        name + "@fixture.example",
    )
    ticket = sessions.issue(principal)
    if session_seconds < 900:
        # A short fixture session for the expiry warning; the store keeps the same cookie.
        digest = sessions.digest(ticket.cookie)
        stored = sessions._sessions[digest]
        sessions._sessions[digest] = ServerSession(
            stored.principal, time.time() + session_seconds, stored.csrf_digest
        )
    response = RedirectResponse(target, status_code=303)
    identity.sessions.set_cookie(response, ticket)
    response.headers["Cache-Control"] = "no-store"
    return response


app = create_app(settings, editor=EditorServices(api=api, guard=guard, reviewed=reviewed))


@asynccontextmanager
async def seed_editor():
    await repository.initialize(fixture["graph"], actor_id="test-bootstrap")
    runtime_process = None
    try:
        if fixture.get("runtime_port"):
            current = await repository.read_active()
            (fixture_path.parent / "runtime-initial.json").write_text(
                json.dumps(runtime_projection(verified_revision(current[0])))
            )
            runtime_process = await asyncio.create_subprocess_exec(
                str(backend_path / ".venv/Scripts/python.exe")
                if os.name == "nt"
                else str(backend_path / ".venv/bin/python"),
                "-B",
                "-c",
                "import sys; from tests.integration.test_conversation "
                "import run_owned_browser_runtime; run_owned_browser_runtime(sys.argv[1])",
                str(fixture_path),
                cwd=backend_path,
            )
            trust = ssl.create_default_context(cafile=str(fixture_path.parent / "certificate.pem"))
            async with (
                asyncio.timeout_at(fixture["readiness_deadline"]),
                httpx.AsyncClient(verify=trust, trust_env=False, timeout=1) as client,
            ):
                while True:
                    if runtime_process.returncode is not None:
                        raise RuntimeError("owned_browser_runtime_exited")
                    try:
                        ready = await client.get(
                            f"https://127.0.0.1:{fixture['runtime_port']}/readyz"
                        )
                        if ready.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    try:
                        async with asyncio.timeout(0.05):
                            await runtime_process.wait()
                    except TimeoutError:
                        pass
        yield
    finally:
        if test_relay is not None:
            await test_relay.close()
        if runtime_process is not None and runtime_process.returncode is None:
            runtime_process.terminate()
            try:
                async with asyncio.timeout(10):
                    await runtime_process.wait()
            except TimeoutError:
                runtime_process.kill()
                await runtime_process.wait()
        test_telemetry.close()


app.register_lifespan_task(seed_editor)
browser_sessions = {}
for name in fixture["identities"]:
    actor = str(uuid5(fixture_namespace, "manager/" + name))
    ticket = sessions.issue(
        Principal(
            actor,
            policy.tenant_id,
            int(time.time()) + 3600,
            (policy.manager_role, test_role),
            "Fixture " + name,
            name + "@fixture.example",
        )
    )
    browser_sessions[name] = [
        {
            "name": identity.cookie_name,
            "value": ticket.cookie,
            "url": origin,
            "secure": True,
            "httpOnly": True,
            "sameSite": "Strict",
        }
    ]
(fixture_path.parent / "sessions.json").write_text(json.dumps(browser_sessions))


def prepare_tls():
    test_key_path.write_bytes(
        test_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (fixture_path.parent / "test-public.pem").write_bytes(
        test_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    (fixture_path.parent / "certificate.pem").write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
    )
    (fixture_path.parent / "private-key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def compile_frontend():
    from reflex.reflex import export

    prepare_tls()
    export.callback(
        zip=False,
        frontend_only=True,
        backend_only=False,
        zip_dest_dir=str(fixture_path.parent),
        upload_db_file=False,
        env="prod",
        backend_excluded_dirs=(),
        ssr=True,
    )


def serve():
    return editor_asgi(app, additional_paths=frozenset({"/v1/console/tickets"}))


def run_server():
    from granian import Granian
    from granian.constants import Interfaces, Loops

    Granian(
        "tests.editor_app:serve",
        interface=Interfaces.ASGI,
        factory=True,
        address="127.0.0.1",
        port=fixture["port"],
        loop=Loops.asyncio,
        ssl_cert=fixture_path.parent / "certificate.pem",
        ssl_key=fixture_path.parent / "private-key.pem",
    ).serve()
