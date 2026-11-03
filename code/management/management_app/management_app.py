"""Foundation shell plus an explicit authenticated editor composition; live calls stay off."""

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
    from management_app.state.configuration_editor import EditorServices


class ShellState(rx.State):
    """A server-backed event verifies the actual Reflex connection."""

    status: str = "Ready"

    @rx.event
    def refresh_status(self) -> None:
        self.status = "Updated"


def index() -> rx.Component:
    return cast(
        rx.Component,
        rx.box(
            rx.el.header(
                rx.hstack(
                    rx.icon("headset", size=22, color="#197767"),
                    rx.text("CALLER / CONTROL", size="2", weight="bold"),
                    rx.spacer(),
                    rx.badge("Local", color_scheme="teal", variant="soft"),
                    align="center",
                    width="100%",
                    max_width="960px",
                    margin="0 auto",
                ),
                padding=["18px 20px", "18px 32px"],
                border_bottom="1px solid #dce1df",
                background="#f4f7f6",
                role="banner",
            ),
            rx.el.main(
                rx.heading("Caller AI", size="7", margin_bottom="32px"),
                rx.hstack(
                    rx.heading("System status", size="4"),
                    rx.spacer(),
                    rx.tooltip(
                        rx.icon_button(
                            rx.icon("refresh-cw", size=16),
                            aria_label="Refresh status",
                            on_click=ShellState.refresh_status,
                            variant="outline",
                            color_scheme="gray",
                            width="36px",
                            height="36px",
                        ),
                        content="Refresh status",
                    ),
                    align="center",
                    width="100%",
                    margin_bottom="20px",
                ),
                rx.grid(
                    *[
                        rx.box(
                            rx.text(label, size="2", color="#58615c", margin_bottom="8px"),
                            rx.text(value, size="3", weight="medium"),
                            padding="20px 0",
                            border_top="1px solid #dce1df",
                            min_width="0",
                        )
                        for label, value in [
                            ("Application", "Management"),
                            ("Operating mode", "Local"),
                            ("Live calls", "Unavailable"),
                            ("Configuration", "Unavailable"),
                        ]
                    ],
                    columns=rx.breakpoints(initial="1", sm="2", md="4"),
                    spacing="6",
                    width="100%",
                ),
                rx.hstack(
                    rx.icon("circle-check", size=16, color="#197767"),
                    rx.text(
                        ShellState.status, size="2", data_testid="connection-status", role="status"
                    ),
                    align="center",
                    margin_top="24px",
                    min_height="24px",
                ),
                padding=["28px 20px", "40px 32px"],
                max_width="960px",
                width="100%",
                margin="0 auto",
                role="main",
            ),
            min_height="100vh",
            background="#ffffff",
            color="#242925",
            font_family="Trebuchet MS, sans-serif",
            letter_spacing="0",
        ),
    )


def create_app(
    settings: Settings | None = None,
    *,
    dependencies: ReadinessPort | None = None,
    editor: "EditorServices | None" = None,
) -> rx.App:
    configuration = settings or Settings()
    api = editor.api if editor is not None else create_health_api(configuration, dependencies)
    if editor is not None:
        api.state.editor_services = editor
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
            theme=rx.theme(
                appearance="light", accent_color="teal", gray_color="gray", radius="small"
            ),
            style={"letter_spacing": "0"},
        )
        application.register_lifespan_task(lifecycle, api=api, settings=configuration)
        application.add_page(index, title="Caller AI")
        if editor is not None:
            from management_app.pages.communities import configuration_page
            from management_app.pages.test_console import test_console_page
            from management_app.state.configuration_editor import ConfigurationEditorState
            from management_app.state.test_console import TestConsoleState

            application.add_page(
                configuration_page,
                route="/configuration",
                title="Configuration | Caller AI",
                on_load=ConfigurationEditorState.enter_view,
            )
            application.add_page(
                configuration_page,
                route="/configuration/communities/[community_id]",
                title="Community | Caller AI",
                on_load=ConfigurationEditorState.enter_view,
            )
            application.add_page(
                test_console_page,
                route="/configuration/test",
                title="Test | Caller AI",
                on_load=TestConsoleState.enter_view,
            )
    return application


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
    from management_app.security import ProtectedReflexBoundary, install_reflex_security
    from management_app.state.configuration_editor import EditorServices

    api = application.api_transformer
    if not isinstance(api, FastAPI) or not isinstance(
        getattr(api.state, "editor_services", None), EditorServices
    ):
        raise RuntimeError("editor_services_required")
    RegistrationContext.set(application._registration_context)
    asgi = application()
    install_reflex_security(application, guard=api.state.editor_services.guard)
    return ProtectedReflexBoundary(asgi, additional_paths=additional_paths)


app = create_app()
