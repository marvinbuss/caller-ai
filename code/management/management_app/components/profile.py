"""Top-bar User Profile, User Settings ("coming soon") and Sign out.

The profile shows the display name, sign-in account and management roles from `/auth/session`,
read-only. User Settings has no controls in this release.
"""

from typing import cast

import reflex as rx

from management_app.components.frame import SessionText, SignOut
from management_app.theme import COLORS


def _row(label: str, field: str, test_id: str) -> rx.Component:
    return cast(
        rx.Component,
        rx.el.div(
            rx.el.dt(label, color=COLORS["text-muted"], font_size="13px"),
            rx.el.dd(
                SessionText.create(field=field),
                margin="2px 0 0",
                overflow_wrap="anywhere",
                data_testid=test_id,
            ),
        ),
    )


def profile_menu() -> rx.Component:
    return cast(
        rx.Component,
        rx.hstack(
            rx.dialog.root(
                rx.dialog.trigger(
                    rx.button(
                        rx.icon("circle-user", size=16, aria_hidden="true"),
                        "User Profile",
                        variant="outline",
                        color_scheme="gray",
                        data_testid="open-profile",
                    )
                ),
                rx.dialog.content(
                    rx.dialog.title("User Profile"),
                    rx.el.dl(
                        _row("Name", "displayName", "profile-name"),
                        _row("Account", "account", "profile-account"),
                        _row("Roles", "roles", "profile-roles"),
                        display="grid",
                        gap="12px",
                    ),
                    rx.flex(
                        rx.dialog.close(rx.button("Close", variant="soft")),
                        justify="end",
                        margin_top="16px",
                    ),
                    max_width="420px",
                ),
            ),
            rx.dialog.root(
                rx.dialog.trigger(
                    rx.button(
                        rx.icon("sliders-horizontal", size=16, aria_hidden="true"),
                        "User Settings",
                        variant="outline",
                        color_scheme="gray",
                        data_testid="open-user-settings",
                    )
                ),
                rx.dialog.content(
                    rx.dialog.title("User Settings"),
                    rx.dialog.description(
                        "Coming soon. Personal preferences will be added in a later release."
                    ),
                    rx.flex(
                        rx.dialog.close(rx.button("Close", variant="soft")),
                        justify="end",
                        margin_top="16px",
                    ),
                    max_width="420px",
                ),
            ),
            SignOut.create(label="Sign out"),
            spacing="2",
            wrap="wrap",
            justify="end",
        ),
    )
