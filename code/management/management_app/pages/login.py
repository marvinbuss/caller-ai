"""Login page: the full Caller AI logo and one sign-in button, nothing else.

The button starts the organization's single sign-on with `returnTo` set to the page that was
requested, so a deep link returns there after sign-in. A failed or cancelled sign-in, or an
ended session, shows a short notice; no management content renders before sign-in.
"""

from typing import cast

import reflex as rx

from management_app.components.frame import LoginNotice, SignIn
from management_app.theme import COLORS


def login_view() -> rx.Component:
    return cast(
        rx.Component,
        rx.center(
            rx.vstack(
                rx.image(
                    src="/caller-ai-logo.svg",
                    alt="Caller AI",
                    width="240px",
                    height="auto",
                    data_testid="login-logo",
                ),
                rx.text(
                    "Sign in with your organization account to manage Caller AI.",
                    color=COLORS["text-muted"],
                    text_align="center",
                ),
                LoginNotice.create(),
                SignIn.create(label="Sign in"),
                spacing="5",
                align="center",
                max_width="420px",
                width="100%",
                padding="32px 24px",
                border=f"1px solid {COLORS['divider']}",
                border_radius="12px",
                background=COLORS["surface"],
            ),
            class_name="caller-login",
            role="main",
            min_height="100vh",
            padding="16px",
            background=COLORS["surface-muted"],
        ),
    )


def login_page() -> rx.Component:
    from management_app.components.frame import (
        FRAME_CSS,
        Redirect,
        SessionProbe,
        gate,
        message_page,
    )

    return cast(
        rx.Component,
        rx.box(
            rx.el.style(FRAME_CSS),
            SessionProbe.create(page=""),
            gate("loading", login_view()),
            gate("anonymous", login_view()),
            gate(
                "no_access",
                rx.center(
                    message_page(
                        "No access",
                        "Your account holds neither the editor nor the tester role.",
                    ),
                    min_height="100vh",
                ),
            ),
            gate("allowed", Redirect.create(to="/")),
            color=COLORS["text"],
        ),
    )
