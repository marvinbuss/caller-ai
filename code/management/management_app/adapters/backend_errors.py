"""Maps the backend's public error vocabulary to the browser-facing close codes of the relay."""

import re
from typing import Any

# The backend's public error vocabulary (see docs/conversation-endpoints.md) with the browser
# close code each public code maps to; the backend's own 4xxx close codes map back to the code.
PUBLIC_CODES = {
    "unauthorized": 1008,
    "forbidden": 1008,
    "settings_not_supported": 1008,
    "invalid_request": 1008,
    "limit_exceeded": 1009,
    "capacity_unavailable": 1013,
    "upstream_unavailable": 1011,
    "internal_error": 1011,
}
BACKEND_CLOSE_CODES = {
    4401: "unauthorized",
    4403: "forbidden",
    4422: "settings_not_supported",
    4400: "invalid_request",
    4413: "limit_exceeded",
    4503: "capacity_unavailable",
    4502: "upstream_unavailable",
    4500: "internal_error",
}


class BackendRefusal(PermissionError):
    """The backend refused or ended the session with one public error code and reason."""

    def __init__(self, code: str, reason: str | None) -> None:
        super().__init__(code)
        self.code, self.reason = code, reason

    @classmethod
    def from_message(cls, value: dict[str, Any]) -> "BackendRefusal":
        reason = value.get("reason")
        if (
            value.get("type") != "error"
            or value.get("code") not in PUBLIC_CODES
            or not set(value) <= {"type", "code", "reason"}
            or (reason is not None and re.fullmatch(r"[a-z][a-z_]{0,63}", str(reason)) is None)
        ):
            return cls("internal_error", None)
        return cls(value["code"], reason)

    def browser_close(self) -> tuple[int, str]:
        # Why: the browser recognizes `manager_busy` to show that another test is running.
        reason = "manager_busy" if self.reason == "manager_busy" else self.code
        return PUBLIC_CODES[self.code], reason
