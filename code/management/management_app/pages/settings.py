"""Settings page at `/settings` (editor role), inside the authenticated frame."""

from typing import cast

import reflex as rx

from management_app.components.frame import frame


def settings_content() -> rx.Component:
    return cast(rx.Component, rx.heading("Settings", size="7"))


def settings_page() -> rx.Component:
    return frame("settings", settings_content)
