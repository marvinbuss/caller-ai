"""Owned HTTPS browser-test host; fixture sessions cannot be loaded by the shipped app."""

import json
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from reflex.config import get_config
from starlette.responses import RedirectResponse

from management_app.adapters.audit_repository import LocalAuditRepository
from management_app.adapters.configuration_repository import LocalConfigurationRepository
from management_app.api.configuration import create_router
from management_app.health import create_health_api
from management_app.management_app import create_app, editor_asgi
from management_app.models.capabilities import ApprovedCapabilities
from management_app.security import (
    EntraPolicy,
    IdentityService,
    Principal,
    ReflexEventGuard,
    SessionStore,
    TokenVerifier,
    create_auth_router,
)
from management_app.services.audit import AuditService
from management_app.services.configuration_validation import ConfigurationValidator
from management_app.settings import Settings
from management_app.state.configuration_editor import EditorServices
from tests.conftest import capability_graph_document

get_config().app_module_import = "tests.editor_app"
fixture_path = Path(os.environ["CALLER_EDITOR_TEST_FIXTURE"])
fixture = json.loads(fixture_path.read_text())
settings = Settings()
if fixture["mode"] != "local" or settings.mode != "local":
    raise ValueError("test_identity_live_mode_prohibited")

origin = fixture["origin"]
policy = EntraPolicy(
    tenant_id=str(uuid5(NAMESPACE_URL, "caller-test-tenant")),
    client_id=str(uuid5(NAMESPACE_URL, "caller-test-client")),
    api_audience=str(uuid5(NAMESPACE_URL, "caller-test-api")),
    origin=origin,
    manager_role="Configuration.Manager",
    manager_scope="Configuration.ReadWrite",
)


class NoNetworkKeys:
    async def signing_keys(self):
        raise RuntimeError("test_host_has_no_identity_discovery")


class NoOidc:
    async def begin(self, state):
        raise RuntimeError("test_host_has_no_live_oidc")

    async def finish(self, flow, response):
        raise RuntimeError("test_host_has_no_live_oidc")


sessions = SessionStore(policy=policy)
reviewed = ApprovedCapabilities.from_reviewed(capability_graph_document())
identity = IdentityService(
    policy=policy, verifier=TokenVerifier(policy=policy, keys=NoNetworkKeys()), sessions=sessions
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
api.include_router(create_auth_router(identity=identity, oidc=NoOidc()))


@api.get("/_test/login/{name}")
async def fixture_login(name: str):
    if name not in fixture["identities"]:
        raise HTTPException(404, "unknown_fixture_manager")
    actor = str(uuid5(NAMESPACE_URL, "caller-test-manager/" + name))
    ticket = sessions.issue(
        Principal(actor, policy.tenant_id, int(time.time()) + 3600, (policy.manager_role,))
    )
    response = RedirectResponse("/configuration", status_code=303)
    identity.sessions.set_cookie(response, ticket)
    response.headers["Cache-Control"] = "no-store"
    return response


app = create_app(settings, editor=EditorServices(api=api, guard=guard, reviewed=reviewed))


@asynccontextmanager
async def seed_editor():
    await repository.initialize(fixture["graph"], actor_id="test-bootstrap")
    yield


app.register_lifespan_task(seed_editor)
browser_sessions = {}
for name in fixture["identities"]:
    actor = str(uuid5(NAMESPACE_URL, "caller-test-manager/" + name))
    ticket = sessions.issue(
        Principal(actor, policy.tenant_id, int(time.time()) + 3600, (policy.manager_role,))
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
    return editor_asgi(app)


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
