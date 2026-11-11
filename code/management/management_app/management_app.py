"""The management frontend: one authenticated frame around every page, plus its health API.

`create_app` always serves the login page and the dashboard route with the health API. When an
`EditorServices` bundle is passed it also registers the Community and Agent, Test, Status and
Settings pages behind the Reflex socket guard, and redirects the old `/configuration` routes.
Reflex registers states in a context that is forked while the app is built, so the pages and
states are imported before that fork; moving those imports later breaks the frontend export.
"""

import os
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, cast

import reflex as rx
from fastapi import FastAPI
from reflex_base.registry import RegistrationContext
from reflex_base.utils.exceptions import ReflexRuntimeError
from starlette.types import ASGIApp

from management_app.health import create_health_api, lifecycle
from management_app.services.ports import ReadinessPort
from management_app.settings import Settings

if TYPE_CHECKING:
    from management_app.composition import (
        ManagedApplication,
        ManagementAuthority,
        ManagementRequest,
        ManagementResourcesFactory,
    )
    from management_app.state.editor_draft import EditorServices


def index() -> rx.Component:
    from management_app.components.frame import frame
    from management_app.pages.dashboard import dashboard_content

    return frame("dashboard", dashboard_content)


def create_app(
    settings: Settings | None = None,
    *,
    dependencies: ReadinessPort | None = None,
    editor: "EditorServices | None" = None,
) -> rx.App:
    from management_app.components.frame import REDIRECTS, redirect_page
    from management_app.pages.login import login_page

    configuration = settings or Settings()
    api = editor.api if editor is not None else create_health_api(configuration, dependencies)
    if editor is not None:
        api.state.editor_services = editor
        # States must register in the base context, so import them before the fork below.
        from management_app.pages.community_agent import community_agent_page
        from management_app.pages.settings import settings_page
        from management_app.pages.status import status_page
        from management_app.pages.test_console import test_console_page
        from management_app.state.configuration_editor import ConfigurationEditorState
        from management_app.state.test_console import TestConsoleState

    registration = RegistrationContext.ensure_context()
    context: AbstractContextManager[object]
    try:
        _registered_app = registration.app
    except ReflexRuntimeError:
        context = nullcontext()
    else:
        context = registration.fork()
    with context:
        application = rx.App(
            api_transformer=api,
            style={"letter_spacing": "0"},
        )
        application.app_wraps[(5, "Overlay")] = _signed_in_overlay
        application.register_lifespan_task(lifecycle, api=api, settings=configuration)
        application.add_page(index, route="/", title="Dashboard | Caller AI")
        application.add_page(login_page, route="/login", title="Sign in | Caller AI")
        if editor is not None:
            application.add_page(
                community_agent_page,
                route="/communities",
                title="Community and Agent | Caller AI",
                on_load=ConfigurationEditorState.enter_view,
            )
            application.add_page(
                test_console_page,
                route="/test",
                title="Test | Caller AI",
                on_load=TestConsoleState.enter_view,
            )
            application.add_page(status_page, route="/status", title="Status | Caller AI")
            application.add_page(settings_page, route="/settings", title="Settings | Caller AI")
            for old, new in REDIRECTS.items():
                application.add_page(redirect_page(new), route=old, title="Caller AI")
    return application


def _signed_in_overlay(stateful: bool) -> rx.Component | None:
    """Connection notices only for signed-in users; the login page has no socket by design."""
    from reflex.app import default_overlay_component

    from management_app.components.frame import gate

    if not stateful:
        return None
    return rx.fragment(gate("signed_in", default_overlay_component()))


async def create_managed_app(
    settings: Settings | None = None,
    *,
    resources: "ManagementResourcesFactory",
    authority: "ManagementAuthority | None",
    request: "ManagementRequest | None" = None,
) -> "ManagedApplication":
    """Explicit reviewed-resource composition; no setting or fixture enables it by default."""
    if authority is None or resources is None:
        raise ValueError("explicit_management_composition_authority_required")
    from management_app.composition import compose_management

    return await compose_management(
        settings or Settings(), request=request, resources=resources, authority=authority
    )


def editor_asgi(application: rx.App, *, additional_paths: frozenset[str] = frozenset()) -> ASGIApp:
    from management_app.reflex_security import ProtectedReflexBoundary, install_reflex_security
    from management_app.state.editor_draft import EditorServices

    api = application.api_transformer
    if not isinstance(api, FastAPI) or not isinstance(
        getattr(api.state, "editor_services", None), EditorServices
    ):
        raise RuntimeError("editor_services_required")
    RegistrationContext.set(application._registration_context)
    asgi = application()
    install_reflex_security(application, guard=api.state.editor_services.guard)
    return ProtectedReflexBoundary(asgi, additional_paths=additional_paths)


def create_app_from_environment() -> ASGIApp:
    """Container entrypoint: local mode is the health-only shell, live mode composes at start."""
    settings = Settings()
    if settings.mode != "live":
        return app()
    from granian.utils.proxies import wrap_asgi_with_proxy_headers

    from management_app.live import LiveManagementApplication

    # The ingress terminates TLS and is the only way in; the scheme checks need its forwarded proto.
    return cast(
        ASGIApp,
        wrap_asgi_with_proxy_headers(LiveManagementApplication(settings), trusted_hosts="*"),
    )


def export_app() -> rx.App:
    """The frontend export registers every page through a build-only stub (research R-12)."""
    from management_app.frontend_export import export_editor

    return create_app(editor=export_editor())


app = export_app() if os.environ.get("CALLER_FRONTEND_EXPORT") == "1" else create_app()
