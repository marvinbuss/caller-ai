"""Status page at `/status` (editor or tester), inside the authenticated frame."""

import reflex as rx

from management_app.components.frame import frame


def status_content() -> rx.Component:
    return rx.heading("Status", size="7")


def status_page() -> rx.Component:
    return frame("status", status_content)
