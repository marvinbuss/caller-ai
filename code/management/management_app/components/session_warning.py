"""Session expiry warning with "Stay signed in", shown from `expiresAt` minus 5 minutes.

"Stay signed in" extends the session through `POST /auth/session/extend` (CSRF protected,
capped by the sign-in token's expiry). When the sign-in token itself is about to end, the browser
signs in again with `returnTo`. Open drafts live in the server-side state of this tab, whose
identity is kept in the tab's session storage and bound to the signed-in actor, so they come back
for the same user only; sign-out and an ended session clear the tab's session storage, which
discards them. Once the session has ended the browser goes to the login page, which says that
unpublished changes were discarded, so nothing unsaved is ever reported as saved.
"""

from typing import Any, cast

import reflex as rx
from reflex_base.utils.imports import ImportDict, ImportVar

from management_app.components.frame import _frame_code

WARNING_SECONDS = 300
WARNING_CODE = """
function callerResign() {
    window.location.assign("/auth/login?returnTo=" + encodeURIComponent(callerReturnPath()));
}
function CallerSessionWarning({onResign, warningSeconds = 300}) {
    const session = useCallerSession();
    const [now, setNow] = useState(() => Date.now());
    const [busy, setBusy] = useState(false);
    const [failed, setFailed] = useState("");
    useEffect(() => {
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, []);
    if (!session) return null;
    const expires = Date.parse(session.expiresAt);
    const token = Date.parse(session.tokenExpiresAt);
    if (!Number.isFinite(expires)) return null;
    if (now >= expires) {
        // A new tab identity drops this tab's server-side drafts: they were never published.
        try { window.sessionStorage.clear(); } catch (error) {}
        window.location.assign("/login?expired=1");
        return null;
    }
    if (now < expires - warningSeconds * 1000) return null;
    const renewable = Number.isFinite(token) && token - now > 60000 && expires < token - 1000;
    const stay = () => {
        setBusy(true);
        setFailed("");
        if (!renewable) {
            if (onResign) onResign(); else callerResign();
            return;
        }
        fetch("/auth/session/extend", {
            method: "POST", credentials: "same-origin",
            headers: {"X-CSRF-Token": session.csrf, Accept: "application/json"}
        }).then(response => response.ok ? response.json() : Promise.reject(response.status))
          .then(value => { callerPublishSession(callerValidSession(value)); })
          .catch(() => setFailed("The session could not be extended."))
          .finally(() => setBusy(false));
    };
    const minutes = Math.max(0, Math.ceil((expires - now) / 60000));
    return createElement("div", {
        role: "alertdialog", "aria-modal": false, "aria-labelledby": "caller-session-title",
        "data-testid": "session-warning", className: "caller-session-warning"
    },
        createElement("p", {id: "caller-session-title"},
            "Your session ends in " + minutes + (minutes === 1 ? " minute." : " minutes.")),
        failed ? createElement("p", {role: "alert"}, failed) : null,
        createElement("button", {
            type: "button", onClick: stay, disabled: busy,
            className: "caller-button caller-button-primary"
        }, "Stay signed in"));
}
"""

WARNING_CSS = """
.caller-session-warning { position: fixed; right: 16px; bottom: 16px; z-index: 50;
  max-width: 360px; padding: 16px; border-radius: 8px; background: #ffffff;
  border: 2px solid #8a4b00; box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
  display: grid; gap: 10px; }
"""


class SessionWarning(rx.Component):
    tag = "CallerSessionWarning"
    warning_seconds: rx.Var[int]
    on_resign: rx.EventHandler

    def add_imports(self) -> ImportDict:
        return {
            "react": [
                ImportVar(tag=symbol)
                for symbol in ("createElement", "Fragment", "useEffect", "useState")
            ]
        }

    def add_custom_code(self) -> list[str]:
        return [_frame_code(), WARNING_CODE]

    @classmethod
    def get_event_triggers(cls) -> dict[str, Any]:
        return super().get_event_triggers() | {"on_resign": lambda: []}


def session_warning(on_resign: Any = None) -> rx.Component:
    properties: dict[str, Any] = {"warning_seconds": WARNING_SECONDS}
    if on_resign is not None:
        properties["on_resign"] = on_resign
    return cast(
        rx.Component,
        rx.fragment(rx.el.style(WARNING_CSS), SessionWarning.create(**properties)),
    )
