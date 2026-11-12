"""Status page at `/status` (editor or tester), inside the authenticated frame.

Shows the conversation service, then the text and voice models with their Foundry service host,
deployment, reachability and check times. No secret, key, token or full URL is ever shown.
"""

from typing import Any, cast

import reflex as rx

from management_app.components.frame import frame, page_content
from management_app.state.status import StatusState
from management_app.theme import COLORS


def _card(card: Any) -> rx.Component:
    reachable = card["state"] == "Reachable"
    return cast(
        rx.Component,
        rx.card(
            rx.vstack(
                rx.hstack(
                    rx.heading(card["title"], size="4", as_="h2"),
                    rx.badge(
                        rx.cond(reachable, "\u25cf ", "\u25b2 "),
                        card["state"],
                        color_scheme=rx.cond(reachable, "green", "red"),
                        data_testid="status-state",
                    ),
                    justify="between",
                    width="100%",
                    align="center",
                ),
                rx.el.dl(
                    rx.foreach(
                        card["fields"].to(list[dict[str, str]]),
                        lambda field: rx.fragment(
                            rx.el.dt(field["label"], color=COLORS["text-muted"]),
                            rx.el.dd(field["value"], margin="0 0 8px 0"),
                        ),
                    ),
                    margin="0",
                ),
                spacing="2",
                width="100%",
            ),
            data_testid=card["id"],
            width="100%",
        ),
    )


def status_content() -> rx.Component:
    return cast(
        rx.Component,
        rx.vstack(
            rx.hstack(
                rx.heading("Status", size="7"),
                rx.button(
                    "Refresh",
                    on_click=StatusState.refresh,
                    variant="outline",
                    loading=StatusState.loading,
                ),
                justify="between",
                width="100%",
                align="center",
            ),
            page_content(
                loading=cast(rx.Var[bool], StatusState.loading),
                error=cast(rx.Var[str], StatusState.error),
                on_retry=StatusState.load,
                content=rx.grid(
                    rx.foreach(StatusState.cards, _card),
                    columns=rx.breakpoints(initial="1", md="3"),
                    spacing="4",
                    width="100%",
                ),
            ),
            spacing="4",
            width="100%",
            max_width="1100px",
        ),
    )


def status_page() -> rx.Component:
    return frame("status", status_content)
