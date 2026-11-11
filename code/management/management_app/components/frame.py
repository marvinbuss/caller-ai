"""The one authenticated frame every page renders: session gate, sidebar, top bar and content.

The browser asks `/auth/session?page=<id>` when a page opens; the answer (or a 401) decides what
is shown. Before it arrives only a neutral loading state renders; without a session the login page
renders; a session without either role sees "no access" with sign-out; a page the roles do not
allow shows "role required" and none of its content. The server computes the allowed pages and
enforces the same roles on every data call, so the gate only hides, it never grants. The sidebar
lists the allowed pages in a fixed order, marks the current one with `aria-current="page"` and
collapses behind a menu button below 768 px. Colors come from `management_app.theme`.
"""

from collections.abc import Callable
from typing import Any, cast

import reflex as rx
from reflex_base.utils.imports import ImportDict, ImportVar

from management_app.security import PAGE_ROUTES
from management_app.theme import COLORS, FONT_FAMILY, NARROW_BREAKPOINT_PX, SIDEBAR_WIDTH

# Sidebar order and labels (contract ui-shell.md).
SIDEBAR: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "Dashboard", "layout-dashboard"),
    ("communities", "Community and Agent", "network"),
    ("test", "Test", "messages-square"),
    ("status", "Status", "activity"),
    ("settings", "Settings", "settings"),
)
REDIRECTS: dict[str, str] = {
    "/configuration": "/communities",
    "/configuration/communities/[community_id]": "/communities",
    "/configuration/test": "/test",
}
LOGIN_ROUTE = "/login"

FRAME_CODE = """
const callerSessionStore = {value: undefined, listeners: new Set(), sequence: 0};
function callerPublishSession(value) {
    callerSessionStore.value = value;
    for (const listener of callerSessionStore.listeners) listener(value);
}
function callerValidSession(value) {
    if (!value || typeof value !== "object" || !Array.isArray(value.pages) ||
        typeof value.csrf !== "string") return null;
    return value;
}
function callerLoadSession(page) {
    if (typeof window === "undefined") return Promise.resolve(undefined);
    const sequence = ++callerSessionStore.sequence;
    const query = page ? "?page=" + encodeURIComponent(page) : "";
    return fetch("/auth/session" + query, {
        credentials: "same-origin", cache: "no-store", headers: {Accept: "application/json"}
    }).then(response => response.ok ? response.json() : null).catch(() => null).then(value => {
        const session = callerValidSession(value);
        if (sequence === callerSessionStore.sequence) callerPublishSession(session);
        return session;
    });
}
function useCallerSession() {
    const [session, setSession] = useState(callerSessionStore.value);
    useEffect(() => {
        callerSessionStore.listeners.add(setSession);
        setSession(callerSessionStore.value);
        return () => { callerSessionStore.listeners.delete(setSession); };
    }, []);
    return session;
}
function callerSessionStatus(session, page) {
    if (session === undefined) return "loading";
    if (session === null) return "anonymous";
    if (!session.pages.length) return "no_access";
    if (page && !session.pages.includes(page)) return "role_required";
    return "allowed";
}
function CallerSessionProbe({page}) {
    useEffect(() => { callerLoadSession(page); }, [page]);
    return null;
}
function CallerSessionGate({when, page, children}) {
    const session = useCallerSession();
    const status = callerSessionStatus(session, page);
    let shown = status === when;
    if (when === "signed_in") shown = status === "allowed" || status === "role_required";
    if (when === "has_page") shown = !!session && session.pages.includes(page);
    return shown ? createElement(Fragment, null, children) : null;
}
function CallerSessionText({field}) {
    const session = useCallerSession();
    if (!session) return null;
    const value = session[field];
    return Array.isArray(value) ? value.join(", ") : (typeof value === "string" ? value : "");
}
function callerReturnPath() {
    const allowed = new Set(%(routes)s);
    const path = window.location.pathname.replace(/\\/+$/, "") || "/";
    return allowed.has(path) ? path : "/";
}
function CallerSignIn({label}) {
    const [href, setHref] = useState("/auth/login");
    useEffect(() => {
        setHref("/auth/login?returnTo=" + encodeURIComponent(callerReturnPath()));
    }, []);
    return createElement("a", {
        href, className: "caller-button caller-button-primary", "data-testid": "sign-in"
    }, label);
}
function CallerLoginNotice() {
    const [message, setMessage] = useState("");
    useEffect(() => {
        const query = new URLSearchParams(window.location.search);
        if (query.get("signin") === "failed") {
            setMessage("Sign-in did not complete. Try again.");
        } else if (query.get("expired") === "1") {
            setMessage("Your session ended. Unpublished changes were discarded.");
        }
    }, []);
    if (!message) return null;
    return createElement("p", {role: "alert", className: "caller-notice"}, message);
}
function callerSignOut() {
    const session = callerSessionStore.value;
    const done = () => {
        try { window.sessionStorage.clear(); } catch (error) {}
        callerPublishSession(null);
        window.location.assign("%(login)s");
    };
    if (!session) return done();
    fetch("/auth/logout", {
        method: "POST", credentials: "same-origin",
        headers: {"X-CSRF-Token": session.csrf}
    }).catch(() => null).then(done);
}
function CallerSignOut({label}) {
    return createElement("button", {
        type: "button", onClick: callerSignOut, className: "caller-button",
        "data-testid": "sign-out"
    }, label);
}
function CallerRedirect({to}) {
    useEffect(() => { window.location.replace(to); }, [to]);
    return createElement("p", {role: "status"}, "Redirecting");
}
function CallerMenu({label, children}) {
    const [open, setOpen] = useState(false);
    return createElement(Fragment, null,
        createElement("button", {
            type: "button", className: "caller-menu-button", "aria-expanded": open,
            "aria-controls": "caller-sidebar", "aria-label": label,
            onClick: () => setOpen(!open)
        }, createElement("span", {"aria-hidden": true}, "\\u2630")),
        createElement("nav", {
            id: "caller-sidebar", className: "caller-sidebar", "data-open": open,
            "aria-label": "Main"
        }, children));
}
function CallerDelayed({active, delay = 1000, children}) {
    const [late, setLate] = useState(false);
    useEffect(() => {
        if (!active) { setLate(false); return; }
        const timer = setTimeout(() => setLate(true), delay);
        return () => clearTimeout(timer);
    }, [active, delay]);
    return active && late ? createElement(Fragment, null, children) : null;
}
"""

FRAME_CSS = f"""
.caller-frame {{ display: grid; grid-template-columns: {SIDEBAR_WIDTH} minmax(0, 1fr);
  grid-template-rows: auto 1fr; min-height: 100vh; }}
.caller-topbar {{ grid-column: 1 / -1; }}
.caller-sidebar {{ background: {COLORS["surface-muted"]};
  border-right: 1px solid {COLORS["divider"]}; padding: 16px 12px; }}
.caller-sidebar a {{ display: flex; align-items: center; gap: 10px; padding: 10px 12px;
  border-radius: 6px; color: {COLORS["text"]}; text-decoration: none; min-height: 40px; }}
.caller-sidebar a[aria-current="page"] {{ background: {COLORS["brand"]};
  color: {COLORS["on-brand"]}; font-weight: 600; }}
.caller-menu-button {{ display: none; }}
.caller-frame a:focus-visible, .caller-frame button:focus-visible,
.caller-frame select:focus-visible, .caller-frame input:focus-visible,
.caller-frame textarea:focus-visible, .caller-login a:focus-visible {{
  outline: 3px solid {COLORS["focus"]}; outline-offset: 2px; }}
.caller-button {{ display: inline-flex; align-items: center; gap: 8px; min-height: 40px;
  padding: 8px 16px; border-radius: 6px; border: 1px solid {COLORS["border"]};
  background: {COLORS["surface"]}; color: {COLORS["text"]}; font: inherit;
  text-decoration: none; cursor: pointer; }}
.caller-button-primary {{ background: {COLORS["brand"]}; border-color: {COLORS["brand"]};
  color: {COLORS["on-brand"]}; font-weight: 600; }}
.caller-notice {{ color: {COLORS["danger"]}; background: {COLORS["danger-fill"]};
  padding: 10px 12px; border-radius: 6px; }}
@media (max-width: {NARROW_BREAKPOINT_PX - 1}px) {{
  .caller-frame {{ grid-template-columns: minmax(0, 1fr); }}
  .caller-menu-button {{ display: inline-flex; align-items: center; justify-content: center;
    width: 40px; height: 40px; border: 1px solid {COLORS["border"]}; border-radius: 6px;
    background: {COLORS["surface"]}; color: {COLORS["text"]}; font-size: 20px; }}
  .caller-sidebar {{ display: none; border-right: none;
    border-bottom: 1px solid {COLORS["divider"]}; }}
  .caller-sidebar[data-open="true"] {{ display: block; grid-column: 1 / -1; }}
}}
"""


def _frame_code() -> str:
    routes = "[" + ", ".join(f'"{route}"' for route in PAGE_ROUTES.values()) + "]"
    return FRAME_CODE % {"routes": routes, "login": LOGIN_ROUTE}


class _FrameComponent(rx.Component):
    def add_imports(self) -> ImportDict:
        return {
            "react": [
                ImportVar(tag=symbol)
                for symbol in ("createElement", "Fragment", "useEffect", "useState")
            ]
        }

    def add_custom_code(self) -> list[str]:
        return [_frame_code()]


class SessionProbe(_FrameComponent):
    tag = "CallerSessionProbe"
    page: rx.Var[str]


class SessionGate(_FrameComponent):
    tag = "CallerSessionGate"
    when: rx.Var[str]
    page: rx.Var[str]


class SessionText(_FrameComponent):
    tag = "CallerSessionText"
    field: rx.Var[str]


class SignIn(_FrameComponent):
    tag = "CallerSignIn"
    label: rx.Var[str]


class LoginNotice(_FrameComponent):
    tag = "CallerLoginNotice"


class SignOut(_FrameComponent):
    tag = "CallerSignOut"
    label: rx.Var[str]


class Redirect(_FrameComponent):
    tag = "CallerRedirect"
    to: rx.Var[str]


class Menu(_FrameComponent):
    tag = "CallerMenu"
    label: rx.Var[str]


class Delayed(_FrameComponent):
    tag = "CallerDelayed"
    active: rx.Var[bool]
    delay: rx.Var[int]


def gate(when: str, *children: rx.Component, page: str = "") -> rx.Component:
    return SessionGate.create(*children, when=when, page=page)


def sidebar_entries(current: str) -> list[tuple[str, str, str, bool]]:
    """(page, route, label, current) in sidebar order; the gate hides disallowed entries."""
    return [(page, PAGE_ROUTES[page], label, page == current) for page, label, _icon in SIDEBAR]


def _sidebar(current: str) -> rx.Component:
    links = []
    for page, label, icon in SIDEBAR:
        attributes: dict[str, Any] = {"aria_current": "page"} if page == current else {}
        links.append(
            gate(
                "has_page",
                rx.link(
                    rx.icon(icon, size=18, aria_hidden="true"),
                    label,
                    href=PAGE_ROUTES[page],
                    data_testid="nav-" + page,
                    **attributes,
                ),
                page=page,
            )
        )
    return Menu.create(*links, label="Menu")


def _top_bar() -> rx.Component:
    from management_app.components.profile import profile_menu

    return cast(
        rx.Component,
        rx.el.header(
            rx.link(
                rx.image(src="/caller-ai-mark.svg", alt="", width="32px", height="32px"),
                rx.text("Caller AI", weight="bold", size="4", color=COLORS["text"]),
                href="/",
                display="flex",
                align_items="center",
                gap="10px",
                text_decoration="none",
                aria_label="Caller AI",
            ),
            rx.spacer(),
            profile_menu(),
            class_name="caller-topbar",
            role="banner",
            display="flex",
            align_items="center",
            gap="12px",
            padding="10px 20px",
            border_bottom=f"1px solid {COLORS['divider']}",
            background=COLORS["surface"],
        ),
    )


def message_page(heading: str, text: str, *extra: rx.Component) -> rx.Component:
    return cast(
        rx.Component,
        rx.vstack(
            rx.heading(heading, size="6"),
            rx.text(text, color=COLORS["text-muted"]),
            *extra,
            spacing="4",
            align="start",
        ),
    )


def _loading() -> rx.Component:
    return cast(
        rx.Component,
        rx.center(
            rx.spinner(size="3"),
            rx.el.span("Loading", class_name="rx-sr-only"),
            role="status",
            aria_label="Loading",
            min_height="100vh",
            data_testid="frame-loading",
        ),
    )


def frame(page: str, content: Callable[[], rx.Component], *, on_resign: Any = None) -> rx.Component:
    """The whole page for `page`: gate, login, no access, role required or the frame."""
    from management_app.components.session_warning import session_warning
    from management_app.pages.login import login_view

    if page not in PAGE_ROUTES:
        raise ValueError("unknown_page")
    return cast(
        rx.Component,
        rx.box(
            rx.el.style(FRAME_CSS),
            SessionProbe.create(page=page),
            gate("loading", _loading()),
            gate("anonymous", login_view()),
            gate(
                "no_access",
                rx.center(
                    message_page(
                        "No access",
                        "Your account holds neither the editor nor the tester role. "
                        "Ask an administrator for access.",
                        SignOut.create(label="Sign out"),
                    ),
                    min_height="100vh",
                    padding="24px",
                    role="main",
                ),
            ),
            gate(
                "signed_in",
                rx.box(
                    _top_bar(),
                    _sidebar(page),
                    rx.el.main(
                        gate(
                            "role_required",
                            message_page(
                                "Role required",
                                "Your roles do not include access to this page.",
                            ),
                            page=page,
                        ),
                        gate("allowed", content(), page=page),
                        padding=["20px 16px", "28px 32px"],
                        min_width="0",
                        role="main",
                    ),
                    session_warning(on_resign),
                    class_name="caller-frame",
                ),
                page=page,
            ),
            min_height="100vh",
            background=COLORS["surface"],
            color=COLORS["text"],
            font_family=FONT_FAMILY,
            letter_spacing="0",
        ),
    )


def page_content(
    *,
    loading: rx.Var[bool],
    error: rx.Var[str],
    on_retry: Any,
    content: rx.Component,
) -> rx.Component:
    """A page's data area: a loading indicator after 1 second, or an error with Retry."""
    return cast(
        rx.Component,
        rx.box(
            Delayed.create(
                rx.hstack(
                    rx.spinner(size="2"),
                    rx.text("Loading"),
                    role="status",
                    data_testid="page-loading",
                    align="center",
                ),
                active=loading,
                delay=1000,
            ),
            rx.cond(
                error != "",
                rx.hstack(
                    rx.text(error, color=COLORS["danger"]),
                    rx.button("Retry", on_click=on_retry, variant="outline"),
                    role="alert",
                    data_testid="page-error",
                    align="center",
                    padding="12px",
                    background=COLORS["danger-fill"],
                    border_radius="6px",
                ),
            ),
            content,
            width="100%",
        ),
    )


def redirect_page(target: str) -> Callable[[], rx.Component]:
    def page() -> rx.Component:
        return Redirect.create(to=target)

    return page
