"""Reflex state for the test console: control metadata only.

`ConsoleControl` is a small state machine for one test (idle, permission, starting, ready, stopping,
ended, unavailable, busy, cleanup pending). It accepts browser-reported metadata only if it is under
8 KiB, keeps the same mode, session and revision, never moves the generation backwards and refuses
`ended` until cleanup is confirmed. Credentials, permits, text and audio never enter Reflex state;
any authorization failure moves the console to `unavailable` rather than showing stale controls.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import reflex as rx
from fastapi import FastAPI, HTTPException, Request
from reflex_base.registry import RegistrationContext

from management_app.models.test_session import ConsoleSnapshot, Mode
from management_app.reflex_security import ReflexEventGuard


@dataclass(slots=True)
class ConsoleControl:
    snapshot: ConsoleSnapshot = field(default_factory=ConsoleSnapshot)

    def start(self, mode: Mode) -> None:
        if (
            mode not in {"text", "speech"}
            or self.snapshot.status not in {"idle", "ended", "unavailable", "busy"}
            or (self.snapshot.session_id is not None and self.snapshot.cleanup_status != "complete")
        ):
            raise PermissionError("console_start_unavailable")
        self.snapshot = ConsoleSnapshot(
            status="permission" if mode == "speech" else "starting", mode=mode
        )

    def stop(self) -> None:
        if self.snapshot.status not in {"permission", "starting", "ready"}:
            return
        self.snapshot = self.snapshot.model_copy(update={"status": "stopping"})

    def accept(self, metadata: dict[str, Any]) -> None:
        try:
            encoded = json.dumps(
                metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
            if len(encoded.encode("utf-8")) > 8192:
                raise ValueError("console_control_limit")
            incoming = ConsoleSnapshot.model_validate_json(encoded)
        except (TypeError, ValueError):
            raise PermissionError("invalid_console_control") from None
        previous = self.snapshot
        allowed = {
            "idle": set(),
            "permission": {"permission", "starting", "stopping", "ended", "unavailable", "busy"},
            "starting": {
                "starting",
                "ready",
                "stopping",
                "ended",
                "unavailable",
                "busy",
                "cleanup_pending",
            },
            "ready": {"ready", "stopping", "ended", "unavailable", "cleanup_pending"},
            "stopping": {"stopping", "ended", "unavailable", "cleanup_pending"},
            "ended": set(),
            "unavailable": set(),
            "busy": set(),
            "cleanup_pending": {"ended", "cleanup_pending"},
        }
        if incoming.mode != previous.mode or incoming.status not in allowed[previous.status]:
            raise PermissionError("console_transition_unavailable")
        pinned = ("session_id", "revision_id", "revision_number", "expires_at")
        for name in pinned:
            current, replacement = getattr(previous, name), getattr(incoming, name)
            if current is not None and replacement is not None and current != replacement:
                raise PermissionError("console_identity_changed")
        if incoming.generation < previous.generation:
            raise PermissionError("stale_console_generation")
        if (
            previous.status == "cleanup_pending"
            and incoming.status == "ended"
            and incoming.cleanup_status != "complete"
        ):
            # "Ended" must mean confirmed cleanup: a new start is allowed only after it.
            raise PermissionError("console_cleanup_unconfirmed")
        retained = {
            name: getattr(previous, name)
            for name in (*pinned, "agent_id", "community_id", "idle_expires_at")
            if name not in incoming.model_fields_set and getattr(previous, name) is not None
        }
        self.snapshot = incoming.model_copy(update=retained)


@dataclass(frozen=True, slots=True)
class ConsoleServices:
    guard: ReflexEventGuard = field(repr=False)
    test_role: str

    def authorize(self, sid: str, token: str) -> None:
        headers = self.guard.request_headers(sid, token, mutation=True)
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "https",
                "path": "/test",
                "query_string": b"",
                "headers": [
                    (name.lower().encode("ascii"), value.encode("ascii"))
                    for name, value in headers.items()
                ],
                "server": ("console-control", 443),
            }
        )
        self.guard.identity.require_test_manager(request, mutation=True, test_role=self.test_role)


class TestConsoleState(rx.State):
    authorized: bool = False
    status: str = "idle"
    mode: str = "text"
    metadata: dict[str, Any] = {}
    _control: ConsoleControl = ConsoleControl()

    def _services(self) -> ConsoleServices:
        api = RegistrationContext.ensure_context().app.api_transformer
        if not isinstance(api, FastAPI) or not isinstance(
            getattr(api.state, "test_console_services", None), ConsoleServices
        ):
            raise PermissionError("console_unavailable")
        services: ConsoleServices = api.state.test_console_services
        services.authorize(self.router.session.session_id, self.router.session.client_token)
        return services

    def _publish(self) -> None:
        self.status = self._control.snapshot.status
        self.mode = self._control.snapshot.mode
        self.metadata = self._control.snapshot.model_dump(mode="json", by_alias=True)

    def _unavailable(self) -> None:
        self.authorized = False
        self._control = ConsoleControl(
            ConsoleSnapshot(status="unavailable", code="permission_denied")
        )
        self._publish()

    @rx.event
    def enter_view(self) -> None:
        try:
            self._services()
        except (HTTPException, PermissionError):
            self._unavailable()
            return
        self.authorized = True
        self._control = ConsoleControl()
        self._publish()

    @rx.event
    def begin(self, mode: str) -> None:
        try:
            self._services()
            if mode not in {"text", "speech"}:
                raise PermissionError("invalid_console_mode")
            self._control.start("speech" if mode == "speech" else "text")
            self._publish()
        except (HTTPException, PermissionError):
            self._unavailable()

    @rx.event
    def update_control(self, encoded: str) -> None:
        try:
            self._services()
            if len(encoded.encode("utf-8")) > 8192:
                raise PermissionError("console_control_limit")
            metadata = json.loads(encoded)
            if not isinstance(metadata, dict):
                raise PermissionError("invalid_console_control")
            self._control.accept(metadata)
            self._publish()
        except (HTTPException, PermissionError, ValueError, TypeError):
            self._unavailable()
