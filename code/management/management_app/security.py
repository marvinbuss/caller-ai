"""Entra ID token verification, server-owned browser sessions and the start-permit signer.

`TokenVerifier` accepts only RS256 tokens whose key id matches the tenant's fixed key set (no
`jku`, `x5u`, `jwk` or `crit` headers), then checks issuer, audience, expiry and role.
`SessionStore` keeps bounded server-side sessions behind an opaque `__Host-` cookie with a CSRF
token, and `TestPermitSigner` mints the short-lived signed permit the backend verifies. Every
failure is a plain 401, 403 or 503 without detail, and nothing here reads identity from headers,
query strings or the environment.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, Self
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import APIRouter, HTTPException, Request, WebSocket
from jwt.utils import base64url_decode
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from starlette.responses import JSONResponse, RedirectResponse, Response

from management_app.models.test_session import StartPermit, utc

COOKIE = "__Host-caller-session"
OPAQUE = re.compile(r"^[A-Za-z0-9_-]{43}$")
SESSION_SECONDS = 900
# Tester-only test access; turned on only by a reviewed change that records the security review.
TESTER_ONLY_TESTS: bool = False
# Sidebar order; `returnTo` accepts exactly these paths.
PAGE_ROUTES: dict[str, str] = {
    "dashboard": "/",
    "communities": "/communities",
    "test": "/test",
    "status": "/status",
    "settings": "/settings",
}


def allowed_pages(*, editor: bool, tester: bool, tester_only_tests: bool) -> list[str]:
    """Pages a role mix opens; the server enforces the same checks on every data call."""
    allowed = set()
    if editor or tester:
        allowed |= {"dashboard", "status"}
    if editor:
        allowed |= {"communities", "settings"}
    if tester and (editor or tester_only_tests):
        allowed.add("test")
    return [page for page in PAGE_ROUTES if page in allowed]


def return_path(value: str | None) -> str | None:
    """An exact page route, or nothing: no scheme, host, `//`, `\\`, query or fragment."""
    if not isinstance(value, str) or value not in PAGE_ROUTES.values():
        return None
    return value


if TYPE_CHECKING:
    from management_app.observability import Telemetry


def unauthorized() -> HTTPException:
    return HTTPException(401, "invalid_identity", headers={"WWW-Authenticate": "Bearer"})


class EntraPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    tenant_id: str
    client_id: str
    api_audience: str
    origin: str
    manager_role: str
    manager_scope: str

    @field_validator("tenant_id", "client_id", "api_audience")
    @classmethod
    def opaque_guid(cls, value: str) -> str:
        return str(UUID(value))

    @model_validator(mode="after")
    def safe_boundaries(self) -> Self:
        parts = urlsplit(self.origin)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
            or (parts.port is not None and not 1 <= parts.port <= 65535)
        ):
            raise ValueError("https_management_origin_required")
        if not (
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,127}", self.manager_role)
            and re.fullmatch(r"[A-Za-z.][A-Za-z0-9_.]{0,127}", self.manager_scope)
        ):
            raise ValueError("invalid_permission_configuration")
        return self

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"


class SigningKeys(Protocol):
    async def signing_keys(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class Principal:
    actor_id: str
    tenant_id: str
    expires_at: int
    roles: tuple[str, ...]
    # Shown only to the signed-in user; never logged.
    display_name: str = field(default="", repr=False)
    account: str = field(default="", repr=False)


def _profile_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value if character.isprintable())[:256]


class TokenVerifier:
    def __init__(self, *, policy: EntraPolicy, keys: SigningKeys) -> None:
        self.policy = policy
        self.keys = keys

    async def _verify(
        self,
        token: str,
        *,
        audience: str,
        nonce: str | None,
        application_clients: frozenset[str] | None = None,
        application_role: str | None = None,
        application_only: bool = False,
        any_role: bool = False,
    ) -> Principal:
        if not isinstance(token, str) or not 1 <= len(token) <= 16384:
            raise unauthorized()
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if (
                header.get("alg") != "RS256"
                or not isinstance(kid, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", kid)
                or any(name in header for name in ("jku", "x5u", "crit", "jwk"))
            ):
                # Key-pointing or critical headers would let a token choose its own trust.
                raise ValueError("invalid_header")
        except Exception:
            raise unauthorized() from None
        try:
            async with asyncio.timeout(2):
                document = await self.keys.signing_keys()
        except Exception:
            raise HTTPException(503, "identity_authority_unavailable") from None
        try:
            candidates = document["keys"]
            if not isinstance(candidates, list) or len(candidates) > 32:
                raise ValueError("invalid_key_set")
            matching = [
                key for key in candidates if isinstance(key, dict) and key.get("kid") == kid
            ]
            if len(matching) != 1:
                raise ValueError("unknown_signing_key")
            key = matching[0]
            if (
                key.get("kty") != "RSA"
                or key.get("use") != "sig"
                or key.get("alg", "RS256") != "RS256"
                or any(name in key for name in ("d", "p", "q"))
            ):
                raise ValueError("invalid_signing_key")
            key_issuer = key.get("issuer", self.policy.issuer)
            if (
                not isinstance(key_issuer, str)
                or key_issuer.replace("{tenantid}", self.policy.tenant_id) != self.policy.issuer
            ):
                raise ValueError("invalid_key_issuer")
            public = jwt.PyJWK.from_dict(key, algorithm="RS256").key
            if not 2048 <= public.key_size <= 8192:
                raise ValueError("invalid_key_size")
            claims = jwt.decode(
                token,
                public,
                algorithms=["RS256"],
                audience=audience,
                issuer=self.policy.issuer,
                leeway=0,
                options={
                    "require": ["exp", "iat", "nbf", "iss", "aud", "tid", "oid", "sub"],
                    "strict_aud": True,
                },
            )
            if (
                claims["tid"] != self.policy.tenant_id
                or claims.get("ver") != "2.0"
                or any(type(claims[name]) is not int for name in ("exp", "iat", "nbf"))
            ):
                raise ValueError("invalid_claims")
            actor = str(UUID(claims["oid"]))
            if nonce is not None:
                if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(
                    claims["nonce"], nonce
                ):
                    raise ValueError("invalid_nonce")
            elif application_clients is not None or application_only:
                if (
                    claims.get("idtyp") != "app"
                    or claims.get("scp")
                    or (
                        application_clients is not None
                        and claims.get("azp") not in application_clients
                    )
                ):
                    raise ValueError("invalid_application_identity")
            elif (
                not isinstance(claims.get("scp"), str)
                or self.policy.manager_scope not in claims["scp"].split()
            ):
                raise ValueError("invalid_scope")
        except Exception:
            raise unauthorized() from None
        roles = claims.get("roles")
        if any_role and roles is None:
            roles = []
        if (
            not isinstance(roles, list)
            or not all(isinstance(role, str) for role in roles)
            or (not any_role and (application_role or self.policy.manager_role) not in roles)
        ):
            raise HTTPException(403, "permission_denied")
        return Principal(
            actor,
            self.policy.tenant_id,
            claims["exp"],
            tuple(roles),
            _profile_text(claims.get("name")) if any_role else "",
            _profile_text(claims.get("preferred_username")) if any_role else "",
        )

    async def verify_access(self, token: str) -> Principal:
        return await self._verify(token, audience=self.policy.api_audience, nonce=None)

    async def verify_tester_application(self, token: str, *, role: str) -> Principal:
        """An app-only token for the management audience that holds exactly the tester role."""
        if role == self.policy.manager_role:
            raise unauthorized()
        return await self._verify(
            token,
            audience=self.policy.api_audience,
            nonce=None,
            application_only=True,
            application_role=role,
        )

    async def verify_identity(self, token: str, *, nonce: str) -> Principal:
        """Sign-in accepts any role mix; each page and API checks the roles it needs."""
        if not nonce:
            raise unauthorized()
        return await self._verify(token, audience=self.policy.client_id, nonce=nonce, any_role=True)


class RuntimeIdentity:
    """Bearer-only workload identity, distinct from manager API and browser audiences."""

    def __init__(
        self,
        *,
        verifier: TokenVerifier,
        audience: str,
        role: str,
        allowed_client_ids: frozenset[str],
    ) -> None:
        audience = str(UUID(audience))
        clients = frozenset(str(UUID(value)) for value in allowed_client_ids)
        if (
            not clients
            or role == verifier.policy.manager_role
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,127}", role)
        ):
            raise ValueError("distinct_runtime_identity_required")
        self._verifier, self._audience = verifier, audience
        self._role, self._clients = role, clients

    async def require_runtime(self, request: Request) -> str:
        values = request.headers.getlist("authorization")
        if len(values) != 1 or not values[0].startswith("Bearer "):
            raise unauthorized()
        token = values[0][7:]
        principal = await self._verifier._verify(
            token,
            audience=self._audience,
            nonce=None,
            application_clients=self._clients,
            application_role=self._role,
        )
        return principal.actor_id


@dataclass(frozen=True, slots=True)
class SessionTicket:
    cookie: str = field(repr=False)
    csrf: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True, slots=True)
class ServerSession:
    principal: Principal
    expires_at: float
    csrf_digest: str = field(repr=False)


class SessionStore:
    def __init__(
        self, *, policy: EntraPolicy, clock: Callable[[], float] = time.time, capacity: int = 1000
    ) -> None:
        if not 1 <= capacity <= 10000:
            raise ValueError("invalid_session_capacity")
        self.policy = policy
        self.clock = clock
        self.capacity = capacity
        self._csrf_key = secrets.token_bytes(32)
        self._sessions: dict[str, ServerSession] = {}

    @staticmethod
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("ascii")).hexdigest()

    def _purge(self) -> None:
        now = self.clock()
        self._sessions = {
            key: value for key, value in self._sessions.items() if value.expires_at > now
        }

    def issue(self, principal: Principal) -> SessionTicket:
        self._purge()
        if principal.tenant_id != self.policy.tenant_id or principal.expires_at <= self.clock():
            raise unauthorized()
        if len(self._sessions) >= self.capacity:
            raise HTTPException(503, "identity_capacity_unavailable")
        cookie = secrets.token_urlsafe(32)
        csrf = self._csrf(cookie)
        expires = min(self.clock() + SESSION_SECONDS, principal.expires_at)
        self._sessions[self.digest(cookie)] = ServerSession(principal, expires, self.digest(csrf))
        return SessionTicket(cookie, csrf, expires)

    def extend(self, cookie: str) -> SessionTicket:
        """Another 15 minutes, never past the sign-in token's own expiry."""
        session = self.resolve(cookie)
        expires = min(self.clock() + SESSION_SECONDS, session.principal.expires_at)
        expires = max(expires, session.expires_at)
        self._sessions[self.digest(cookie)] = ServerSession(
            session.principal, expires, session.csrf_digest
        )
        return SessionTicket(cookie, self._csrf(cookie), expires)

    def resolve(self, cookie: str) -> ServerSession:
        self._purge()
        if not OPAQUE.fullmatch(cookie):
            raise unauthorized()
        return self.resolve_digest(self.digest(cookie))

    def resolve_digest(self, digest: str) -> ServerSession:
        self._purge()
        session = self._sessions.get(digest)
        if session is None:
            raise unauthorized()
        return session

    def _csrf(self, cookie: str) -> str:
        value = hmac.new(
            self._csrf_key, self.digest(cookie).encode("ascii"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def csrf_for(self, cookie: str) -> str:
        self.resolve(cookie)
        return self._csrf(cookie)

    def revoke(self, cookie: str) -> None:
        if OPAQUE.fullmatch(cookie):
            self._sessions.pop(self.digest(cookie), None)

    def set_cookie(self, response: Response, ticket: SessionTicket) -> None:
        response.set_cookie(
            COOKIE,
            ticket.cookie,
            max_age=max(0, int(ticket.expires_at - self.clock())),
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )


class TestPermitSigner:
    __test__ = False

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_id: str,
        signing_key: RSAPrivateKey,
        verification_keys: Mapping[str, RSAPublicKey],
        utc_now: Callable[[], datetime],
        max_clock_skew_seconds: float,
    ) -> None:
        if (
            not issuer
            or len(issuer) > 512
            or not audience
            or len(audience) > 512
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", key_id) is None
            or not isinstance(signing_key, RSAPrivateKey)
            or not 2048 <= signing_key.key_size <= 8192
            or not 1 <= len(verification_keys) <= 4
            or key_id not in verification_keys
            or utc_now is None
            or type(max_clock_skew_seconds) not in (int, float)
            or not math.isfinite(max_clock_skew_seconds)
            or not 0 <= max_clock_skew_seconds <= 1
        ):
            raise ValueError("invalid_test_signing_configuration")
        for name, key in verification_keys.items():
            if (
                not isinstance(name, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", name) is None
                or not isinstance(key, RSAPublicKey)
                or not 2048 <= key.key_size <= 8192
            ):
                raise ValueError("invalid_test_signing_configuration")
        if verification_keys[key_id].public_numbers() != signing_key.public_key().public_numbers():
            raise ValueError("invalid_test_signing_configuration")
        self.issuer, self.audience, self.key_id = issuer, audience, key_id
        self._key, self._keys, self._utc_now = signing_key, dict(verification_keys), utc_now
        self._skew = timedelta(seconds=max_clock_skew_seconds)
        self._observed: datetime | None = None

    def _time(self) -> datetime:
        now = utc(self._utc_now())
        if self._observed is not None and now < self._observed:
            raise PermissionError("invalid_test_permit")
        self._observed = now
        return now

    def _current(self, permit: StartPermit) -> None:
        now = self._time()
        if (
            permit.issuer != self.issuer
            or permit.audience != self.audience
            or permit.issued_at > now + self._skew
            or now + self._skew < permit.admission_started_at
            or now + self._skew >= min(permit.admission_deadline_utc, permit.auth_expires_at)
            or (now + self._skew).timestamp() >= permit.expires_epoch
        ):
            raise PermissionError("invalid_test_permit")

    def sign(self, permit: StartPermit) -> str:
        try:
            self._current(permit)
            raw = jwt.encode(
                permit.model_dump(mode="json", by_alias=True),
                self._key,
                algorithm="RS256",
                headers={"kid": self.key_id, "typ": "JWT"},
            )
            if len(raw.encode("utf-8")) > 6144:
                raise ValueError("permit_limit")
            return raw
        except Exception:
            raise PermissionError("invalid_test_permit") from None

    def verify(
        self, raw: str, *, management_session_digest: str, tenant_id: str, actor_id: str
    ) -> StartPermit:
        try:
            if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > 6144:
                raise ValueError("invalid_permit")
            segments = raw.split(".")
            if len(segments) != 3:
                raise ValueError("invalid_permit")

            def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for name, value in pairs:
                    if name in result:
                        raise ValueError("duplicate_permit_field")
                    result[name] = value
                return result

            header = json.loads(base64url_decode(segments[0]), object_pairs_hook=unique)
            payload = json.loads(base64url_decode(segments[1]), object_pairs_hook=unique)
            if (
                not isinstance(header, dict)
                or set(header) != {"alg", "typ", "kid"}
                or header["alg"] != "RS256"
                or header["typ"] != "JWT"
                or not isinstance(header["kid"], str)
                or header["kid"] not in self._keys
            ):
                raise ValueError("invalid_permit")
            claims = jwt.decode(
                raw,
                self._keys[header["kid"]],
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "require": ["iss", "aud", "iat", "exp"],
                },
            )
            if not isinstance(payload, dict) or payload != claims:
                raise ValueError("invalid_permit")
            permit = StartPermit.model_validate_json(json.dumps(claims, allow_nan=False))
            self._current(permit)
            if (
                permit.tenant_id != tenant_id
                or permit.actor_id != actor_id
                or not hmac.compare_digest(
                    permit.management_session_digest, management_session_digest
                )
            ):
                raise ValueError("foreign_permit_session")
            return permit
        except Exception:
            raise PermissionError("invalid_test_permit") from None


class IdentityService:
    cookie_name = COOKIE

    def __init__(
        self, *, policy: EntraPolicy, verifier: TokenVerifier, sessions: SessionStore
    ) -> None:
        if verifier.policy != policy or sessions.policy != policy:
            raise ValueError("identity_policy_mismatch")
        self.policy = policy
        self.verifier = verifier
        self.sessions = sessions

    def browser_session(self, request: Request | WebSocket, *, mutation: bool) -> ServerSession:
        origin = request.headers.get("origin")
        if (origin is not None and origin != self.policy.origin) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            raise HTTPException(403, "csrf_validation_failed")
        cookie_header = request.headers.get("cookie", "")
        names = [part.partition("=")[0].strip() for part in cookie_header.split(";")]
        if names.count(COOKIE) != 1:
            raise unauthorized()
        session = self.sessions.resolve(request.cookies.get(COOKIE, ""))
        if mutation:
            csrf = request.headers.get("x-csrf-token", "")
            if (
                origin != self.policy.origin
                or not OPAQUE.fullmatch(csrf)
                or not hmac.compare_digest(self.sessions.digest(csrf), session.csrf_digest)
            ):
                raise HTTPException(403, "csrf_validation_failed")
        return session

    def require_test_manager(
        self, request: Request | WebSocket, *, mutation: bool, test_role: str
    ) -> ServerSession:
        if (
            request.scope.get("scheme") not in {"https", "wss"}
            or request.headers.getlist("origin") != [self.policy.origin]
            or request.headers.getlist("authorization")
            or len(request.headers.getlist("cookie")) != 1
            or mutation
            and len(request.headers.getlist("x-csrf-token")) != 1
        ):
            raise HTTPException(403, "test_permission_denied")
        session = self.browser_session(request, mutation=mutation)
        if (
            not isinstance(test_role, str)
            or not test_role
            or len(test_role) > 128
            or self.policy.manager_role not in session.principal.roles
            or test_role not in session.principal.roles
        ):
            raise HTTPException(403, "test_permission_denied")
        return session

    def display_name(self, request: Request) -> str:
        """The signed-in browser user's display name, or nothing for a bearer token."""
        if request.headers.getlist("authorization") or COOKIE not in request.cookies:
            return ""
        try:
            return self.sessions.resolve(request.cookies[COOKIE]).principal.display_name
        except HTTPException:
            return ""

    async def require_manager(self, request: Request) -> str:
        authorization = request.headers.getlist("authorization")
        if authorization:
            if len(authorization) != 1 or COOKIE in request.cookies:
                raise unauthorized()
            scheme, separator, token = authorization[0].partition(" ")
            if scheme.lower() != "bearer" or not separator or not token or token.strip() != token:
                raise unauthorized()
            return (await self.verifier.verify_access(token)).actor_id
        session = self.browser_session(
            request, mutation=request.method not in {"GET", "HEAD", "OPTIONS"}
        )
        if self.policy.manager_role not in session.principal.roles:
            # Sign-in accepts any role mix, so the editor role is checked here.
            raise HTTPException(403, "permission_denied")
        return session.principal.actor_id

    @staticmethod
    def _bearer(request: Request | WebSocket) -> str:
        authorization = request.headers.getlist("authorization")
        if len(authorization) != 1 or request.headers.getlist("cookie"):
            raise unauthorized()
        scheme, separator, token = authorization[0].partition(" ")
        if scheme.lower() != "bearer" or not separator or not token or token.strip() != token:
            raise unauthorized()
        return token

    async def require_reader(self, request: Request, *, reader_role: str | None) -> str:
        """A manager, or for a read only, an application that holds the tester role (FR-053)."""
        try:
            return await self.require_manager(request)
        except HTTPException as refused:
            if (
                reader_role is None
                or request.method not in {"GET", "HEAD"}
                or refused.status_code not in {401, 403}
                or not request.headers.getlist("authorization")
            ):
                raise
            try:
                token = self._bearer(request)
                principal = await self.verifier.verify_tester_application(token, role=reader_role)
            except HTTPException:
                raise refused from None
            return principal.actor_id

    def test_digest(self, request: Request | WebSocket) -> str:
        """The value a test permit binds: the session cookie, or the application's bearer token."""
        if request.headers.getlist("authorization"):
            return hashlib.sha256(b"bearer\0" + self._bearer(request).encode("ascii")).hexdigest()
        return self.sessions.digest(request.cookies[COOKIE])

    async def require_test_principal(
        self, request: Request | WebSocket, *, mutation: bool, test_role: str
    ) -> ServerSession:
        """A browser session with both roles, or an application token with the tester role."""
        if not request.headers.getlist("authorization"):
            return self.require_test_manager(request, mutation=mutation, test_role=test_role)
        if (
            request.scope.get("scheme") not in {"https", "wss"}
            or request.headers.getlist("origin") not in ([], [self.policy.origin])
            or not isinstance(test_role, str)
            or not test_role
        ):
            raise HTTPException(403, "test_permission_denied")
        try:
            token = self._bearer(request)
            principal = await self.verifier.verify_tester_application(token, role=test_role)
        except HTTPException as error:
            raise HTTPException(
                403 if error.status_code == 403 else 401, "invalid_identity"
            ) from None
        return ServerSession(principal, float(principal.expires_at), "")


@dataclass(frozen=True, slots=True)
class LoginFlow:
    url: str = field(repr=False)
    state: str = field(repr=False)
    private: Any = field(repr=False)


def _instant(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


class OidcFlow(Protocol):
    async def begin(self, state: str) -> LoginFlow: ...

    async def finish(self, flow: LoginFlow, response: dict[str, str]) -> tuple[str, str]: ...


def create_auth_router(
    *,
    identity: IdentityService,
    oidc: OidcFlow,
    operation_budget: float = 5,
    test_role: str | None = None,
    telemetry: "Telemetry | None" = None,
) -> APIRouter:
    if not 0 < operation_budget <= 5:
        raise ValueError("invalid_identity_operation_budget")
    router = APIRouter()
    pending: dict[str, tuple[float, LoginFlow, str]] = {}
    inflight = 0
    login_cookie = "__Host-caller-login"

    def record(name: str, **fields: str) -> None:
        if telemetry is not None:
            telemetry.record(name, **fields)

    def purge() -> None:
        for key, (expires, _flow, _target) in tuple(pending.items()):
            if expires <= time.monotonic():
                del pending[key]

    @router.get("/auth/login")
    async def login(returnTo: str | None = None) -> Response:  # noqa: N803
        nonlocal inflight
        purge()
        if len(pending) + inflight >= 100:
            raise HTTPException(503, "identity_capacity_unavailable")
        inflight += 1
        state, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        try:
            async with asyncio.timeout(operation_budget):
                flow = await oidc.begin(state)
            target = urlsplit(flow.url)
            query = parse_qs(target.query)
            if (
                target.scheme != "https"
                or target.netloc != "login.microsoftonline.com"
                or target.path != f"/{identity.policy.tenant_id}/oauth2/v2.0/authorize"
                or flow.state != state
                or query.get("state") != [state]
            ):
                raise ValueError("invalid_authorization_redirect")
        except Exception:
            raise HTTPException(503, "identity_authority_unavailable") from None
        finally:
            inflight -= 1
        pending[identity.sessions.digest(cookie)] = (
            time.monotonic() + 300,
            flow,
            return_path(returnTo) or "/",
        )
        response = RedirectResponse(flow.url, status_code=303)
        response.set_cookie(
            login_cookie, cookie, secure=True, httponly=True, samesite="none", max_age=300, path="/"
        )
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    @router.post("/auth/callback")
    async def callback(request: Request) -> Response:
        try:
            response = await finish_sign_in(request)
        except HTTPException as refused:
            record(
                "auth.sign_in",
                outcome="failure",
                reason="invalid_response"
                if refused.status_code == 401
                else "capacity"
                if refused.detail == "identity_capacity_unavailable"
                else "unavailable",
            )
            if refused.status_code != 401:
                raise
        else:
            record("auth.sign_in", outcome="success", reason="none")
            return response
        # A cancelled or failed sign-in returns to the login page and creates no session.
        failed = RedirectResponse("/login?signin=failed", status_code=303)
        failed.delete_cookie(login_cookie, secure=True, httponly=True, samesite="none", path="/")
        failed.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return failed

    async def finish_sign_in(request: Request) -> Response:
        deadline = asyncio.get_running_loop().time() + operation_budget
        purge()
        cookie = request.cookies.get(login_cookie, "")
        if not OPAQUE.fullmatch(cookie):
            raise unauthorized()
        item = pending.pop(identity.sessions.digest(cookie), None)
        if item is None:
            raise unauthorized()
        if (
            request.headers.get("content-type", "").split(";", 1)[0]
            != "application/x-www-form-urlencoded"
        ):
            raise unauthorized()
        body = bytearray()
        try:
            async with asyncio.timeout_at(deadline):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 16384:
                        raise HTTPException(413, "identity_response_too_large")
        except TimeoutError:
            raise HTTPException(503, "identity_response_timeout") from None
        try:
            fields = parse_qs(
                body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=8
            )
            if (
                not {"code", "state"} <= fields.keys()
                or any(len(values) != 1 for values in fields.values())
                or not hmac.compare_digest(fields["state"][0], item[1].state)
            ):
                raise ValueError("invalid_oidc_response")
            submitted = {
                key: values[0]
                for key, values in fields.items()
                if key in {"code", "state", "session_state"}
            }
            async with asyncio.timeout_at(deadline):
                token, verified_nonce = await oidc.finish(item[1], submitted)
                principal = await identity.verifier.verify_identity(token, nonce=verified_nonce)
            previous = request.cookies.get(COOKIE)
            if previous:
                identity.sessions.revoke(previous)
            ticket = identity.sessions.issue(principal)
        except HTTPException:
            raise
        except Exception:
            raise unauthorized() from None
        response = RedirectResponse(item[2], status_code=303)
        identity.sessions.set_cookie(response, ticket)
        response.delete_cookie(login_cookie, secure=True, httponly=True, samesite="none", path="/")
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    def profile(stored: ServerSession, cookie: str) -> dict[str, Any]:
        roles = stored.principal.roles
        editor = identity.policy.manager_role in roles
        tester = test_role is not None and test_role in roles
        return {
            "actor_ref": stored.principal.actor_id,
            "csrf": identity.sessions.csrf_for(cookie),
            "roles": [name for name, held in (("editor", editor), ("tester", tester)) if held],
            "pages": allowed_pages(
                editor=editor, tester=tester, tester_only_tests=TESTER_ONLY_TESTS
            ),
            "displayName": stored.principal.display_name,
            "account": stored.principal.account,
            "expiresAt": _instant(stored.expires_at),
            "tokenExpiresAt": _instant(stored.principal.expires_at),
        }

    @router.get("/auth/session")
    async def session(request: Request, page: str | None = None) -> Response:
        viewed = page if page in PAGE_ROUTES or page == "login" else None
        try:
            stored = identity.browser_session(request, mutation=False)
        except HTTPException:
            if viewed is not None:
                record("ui.page_view", page=viewed, outcome="login")
            raise
        body = profile(stored, request.cookies[COOKIE])
        if viewed is not None and viewed != "login":
            record(
                "ui.page_view",
                page=viewed,
                outcome="no_access"
                if not body["pages"]
                else "shown"
                if viewed in body["pages"]
                else "role_required",
            )
        response = JSONResponse(body)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/auth/session/extend")
    async def extend(request: Request) -> Response:
        try:
            identity.browser_session(request, mutation=True)
            ticket = identity.sessions.extend(request.cookies[COOKIE])
        except HTTPException as refused:
            record("auth.session_extend", outcome="failure")
            if refused.status_code == 401:
                raise HTTPException(401, "session_expired") from None
            raise
        record("auth.session_extend", outcome="success")
        stored = identity.sessions.resolve(ticket.cookie)
        response = JSONResponse(profile(stored, ticket.cookie))
        identity.sessions.set_cookie(response, ticket)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/auth/logout")
    async def logout(request: Request) -> Response:
        identity.browser_session(request, mutation=True)
        identity.sessions.revoke(request.cookies[COOKIE])
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
        return response

    return router
