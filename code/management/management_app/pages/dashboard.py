"""Dashboard: the landing page after sign-in.

It shows a welcome heading and one card per page the roles allow. The cards sit in a responsive
grid so widgets can be added later without changing the frame.
"""

from typing import cast

import reflex as rx

from management_app.components.frame import SIDEBAR, gate
from management_app.security import PAGE_ROUTES
from management_app.theme import COLORS

DESCRIPTIONS = {
    "communities": "Shape communities, agents, handovers and tools, then publish.",
    "test": "Run text and voice tests against the published configuration.",
    "status": "See the connected Foundry service and the model deployments.",
    "settings": "Choose the voice, reply length limit and idle timeout.",
}


def dashboard_content() -> rx.Component:
    cards = [
        gate(
            "has_page",
            rx.link(
                rx.vstack(
                    rx.hstack(
                        rx.icon(icon, size=20, color=COLORS["brand"], aria_hidden="true"),
                        rx.text(label, weight="bold", size="4"),
                        align="center",
                    ),
                    rx.text(DESCRIPTIONS[page], color=COLORS["text-muted"], size="2"),
                    spacing="2",
                    align="start",
                ),
                href=PAGE_ROUTES[page],
                display="block",
                padding="16px",
                border=f"1px solid {COLORS['border']}",
                border_radius="8px",
                color=COLORS["text"],
                text_decoration="none",
                data_testid="dashboard-" + page,
            ),
            page=page,
        )
        for page, label, icon in SIDEBAR
        if page != "dashboard"
    ]
    return cast(
        rx.Component,
        rx.vstack(
            rx.heading("Welcome to Caller AI", size="7"),
            rx.text("Choose where to start.", color=COLORS["text-muted"]),
            rx.grid(
                *cards,
                columns=rx.breakpoints(initial="1", sm="2", lg="3"),
                spacing="4",
                width="100%",
                data_testid="dashboard-widgets",
            ),
            spacing="5",
            align="start",
            width="100%",
        ),
    )
