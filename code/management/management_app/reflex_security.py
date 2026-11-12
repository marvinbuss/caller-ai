"""Reflex socket and event authorization plus the reviewed-ingress allow-list.

`ReflexEventGuard` binds each Socket.IO connection to the browser session cookie and a client
token when it connects (same origin only, bounded owner and connection tables) and re-checks that
binding on every event, so a revoked or foreign session stops at once. `SecureEventNamespace` and
`EventAuthorizationMiddleware` apply the guard to the socket and to event processing, and
`ProtectedReflexBoundary` rejects any mutating HTTP route that was not reviewed, because Reflex
mounts mutating routes of its own. Cookie and authorization headers never reach Reflex handlers.
"""

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from reflex import constants
from reflex.app import EventNamespace
from reflex.middleware.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from management_app.security import COOKIE, IdentityService, unauthorized

if TYPE_CHECKING:
    from reflex.app import App
    from reflex.event import Event
    from reflex.state import BaseState, StateUpdate


class ReflexEventGuard:
    def __init__(self, *, identity: IdentityService) -> None:
        self.identity = identity
        self._owners: dict[str, str] = {}
        self._connections: dict[str, tuple[str, str]] = {}
        self._session_handles: dict[str, str] = {}

    def connect(self, sid: str, environ: dict[str, Any]) -> dict[str, Any]:
        scope = dict(environ.get("asgi.scope", {}))
        scope["type"] = "http"
        scope["method"] = "GET"
        request = Request(scope)
        if request.headers.get("origin") != self.identity.policy.origin:
            # Browsers always send Origin on socket upgrades; a mismatch is a cross-site attempt.
            raise HTTPException(403, "csrf_validation_failed")
        session = self.identity.browser_session(request, mutation=False)
        query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=16)
        tokens = query.get("token", [])
        if len(tokens) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", tokens[0]):
            raise unauthorized()
        token_hash = self.identity.sessions.digest(tokens[0])
        owner = self._owners.get(token_hash)
        if owner is not None and owner != session.principal.actor_id:
            # A client token belongs to its first actor for good; no other actor may reuse it.
            raise unauthorized()
        if (owner is None and len(self._owners) >= 5000) or len(self._connections) >= 2000:
            # Fixed caps keep a flood of sockets from growing server memory.
            raise HTTPException(503, "identity_capacity_unavailable")
        self._owners[token_hash] = session.principal.actor_id
        self._connections[sid] = (
            self.identity.sessions.digest(request.cookies[COOKIE]),
            token_hash,
        )
        self._session_handles[sid] = request.cookies[COOKIE]
        allowed_headers = {b"origin", b"user-agent", b"accept-language"}
        # Cookie and authorization never reach Reflex handlers; the guard keeps them server-side.
        scope["headers"] = [
            (name, value)
            for name, value in scope.get("headers", [])
            if name.lower() in allowed_headers
        ]
        cleaned = {key: value for key, value in environ.items() if not key.startswith("HTTP_")}
        cleaned["asgi.scope"] = scope
        return cleaned

    def authorize(self, sid: str, token: str) -> str:
        # Checked on every event, not just at connect, so a revoked session stops at once.
        binding = self._connections.get(sid)
        if binding is None or binding[1] != self.identity.sessions.digest(token):
            raise unauthorized()
        session = self.identity.sessions.resolve_digest(binding[0])
        if self._owners.get(binding[1]) != session.principal.actor_id:
            raise unauthorized()
        return session.principal.actor_id

    def disconnect(self, sid: str) -> None:
        self._connections.pop(sid, None)
        self._session_handles.pop(sid, None)

    def request_headers(self, sid: str, token: str, *, mutation: bool) -> dict[str, str]:
        self.authorize(sid, token)
        cookie = self._session_handles[sid]
        headers = {"Cookie": COOKIE + "=" + cookie, "Origin": self.identity.policy.origin}
        if mutation:
            headers["X-CSRF-Token"] = self.identity.sessions.csrf_for(cookie)
        return headers


class SecureEventNamespace(EventNamespace):
    def __init__(self, *, delegate: "EventNamespace", guard: ReflexEventGuard) -> None:
        super().__init__(namespace=delegate.namespace, app=delegate.app)
        self.delegate = delegate
        self.guard = guard

    async def trigger_event(self, event: str, *args: Any) -> Any:
        sid = args[0] if args else ""
        if event == "disconnect":
            self.guard.disconnect(sid)
            return await self.delegate.trigger_event(event, *args)
        try:
            if event == "connect":
                auth = args[2] if len(args) > 2 else None
                bootstrap = (
                    auth.get(constants.CompileVars.CONNECT_AUTH_EVENT)
                    if isinstance(auth, dict)
                    else None
                )
                if bootstrap is not None and not self.valid_event(bootstrap):
                    return False
                cleaned = self.guard.connect(sid, args[1])
                return await self.delegate.trigger_event(
                    event, sid, cleaned, args[2] if len(args) > 2 else None
                )
            self.guard.authorize(sid, self.delegate.sid_to_token.get(sid, ""))
            if event == "event":
                data = args[1] if len(args) > 1 else None
                if not self.valid_event(data):
                    return {"error": "invalid_event"}
            return await self.delegate.trigger_event(event, *args)
        except Exception:
            if event == "connect":
                self.guard.disconnect(sid)
                return False
            return {"error": "authorization_required"}

    @staticmethod
    def valid_event(data: object) -> bool:
        return (
            isinstance(data, dict)
            and isinstance(data.get("name"), str)
            and isinstance(data.get("payload", {}), dict)
            and isinstance(data.get("router_data", {}), dict)
        )


class EventAuthorizationMiddleware(Middleware):
    def __init__(self, *, guard: ReflexEventGuard) -> None:
        self.guard = guard

    async def preprocess(
        self, app: "App", state: "BaseState", event: "Event"
    ) -> "StateUpdate | None":
        from reflex.state import StateUpdate

        try:
            sid = event.router_data.get(constants.RouteVar.SESSION_ID)
            token = event.router_data.get(constants.RouteVar.CLIENT_TOKEN)
            if sid is None:
                sid = state.router.session.session_id
            if token is None:
                token = state.router.session.client_token
            self.guard.authorize(sid, token)
        except Exception:
            return StateUpdate(delta={}, events=[], final=True)
        return None

    async def postprocess(
        self, app: "App", state: "BaseState", event: "Event", update: "StateUpdate"
    ) -> "StateUpdate":
        return update


def install_reflex_security(application: "App", *, guard: ReflexEventGuard) -> None:
    from reflex.app import EventNamespace

    if application.sio is None:
        raise RuntimeError("reflex_transport_not_initialized")
    delegates = [
        namespace
        for namespace in application.sio.namespace_handlers.values()
        if isinstance(namespace, EventNamespace)
    ]
    if len(delegates) != 1:
        raise RuntimeError("reflex_transport_unavailable")
    application.sio.register_namespace(SecureEventNamespace(delegate=delegates[0], guard=guard))
    application.add_middleware(EventAuthorizationMiddleware(guard=guard), index=0)


class ProtectedReflexBoundary:
    def __init__(self, app: ASGIApp, *, additional_paths: frozenset[str] = frozenset()) -> None:
        if not additional_paths <= {
            "/v1/runtime/capabilities/execute",
            "/v1/security/revocations",
            "/v1/console/tickets",
        }:
            raise ValueError("unreviewed_business_ingress")
        self.app = app
        self.allowed = {
            "/auth/callback",
            "/auth/logout",
            "/auth/session/extend",
            "/v2/configuration",
            "/_event",
            "/_event/",
        } | additional_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            # Reflex mounts mutating routes of its own; only reviewed paths may change state.
            if scope["path"] not in self.allowed:
                response = JSONResponse(
                    {"error": "unreviewed_ingress_prohibited"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
