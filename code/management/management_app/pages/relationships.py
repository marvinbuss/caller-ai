"""Policy-filtered handover paths and allowlisted context-field editing.

Each row is one handover path between two agents with the context fields it passes on. The
server offers only the sources and targets the relationship rules allow, and the context fields
are a fixed allowlist, so a manager can switch a listed field on or off but cannot add one.
Relationships that a lifecycle role requires are shown as required and cannot be removed.
"""

from typing import Any, cast

import reflex as rx
from reflex.event import EventHandler
from reflex.vars.object import ObjectVar

from management_app.state.configuration_editor import ConfigurationEditorState as Editor

REMOVE = cast(EventHandler, Editor.delete_relationship)
SET_FIELD = cast(EventHandler, Editor.set_context_field)


def relationship_row(relationship: ObjectVar[dict[str, Any]]) -> rx.Component:
    identifier = relationship["id"].to(str)
    label = relationship["source"].to(str) + " to " + relationship["target"].to(str)
    return cast(
        rx.Component,
        rx.el.fieldset(
            rx.el.legend(label, font_weight="600", font_size="16px", overflow_wrap="anywhere"),
            rx.flex(
                rx.cond(
                    relationship["required"].to(bool), rx.badge("Required", color_scheme="gray")
                ),
                rx.spacer(),
                rx.cond(
                    relationship["required"].to(bool),
                    rx.icon_button(
                        rx.icon("trash-2", size=16),
                        aria_label="Remove relationship",
                        disabled=True,
                        title="Required lifecycle relationship",
                        variant="ghost",
                        color_scheme="gray",
                    ),
                    rx.alert_dialog.root(
                        rx.alert_dialog.trigger(
                            rx.icon_button(
                                rx.icon("trash-2", size=16),
                                aria_label="Remove relationship",
                                title="Remove relationship",
                                variant="ghost",
                                color_scheme="red",
                            )
                        ),
                        rx.alert_dialog.content(
                            rx.alert_dialog.title("Remove relationship?"),
                            rx.alert_dialog.description(
                                "Only this configured transfer path will be removed."
                            ),
                            rx.flex(
                                rx.alert_dialog.cancel(rx.button("Cancel", variant="soft")),
                                rx.alert_dialog.action(
                                    rx.button(
                                        "Remove relationship",
                                        color_scheme="red",
                                        on_click=REMOVE(identifier),
                                    )
                                ),
                                justify="end",
                                gap="10px",
                                margin_top="20px",
                            ),
                        ),
                    ),
                ),
                align="center",
            ),
            rx.el.label(
                rx.el.input(
                    type="checkbox",
                    checked=relationship["summary"].to(bool),
                    on_change=SET_FIELD(identifier, "request_summary"),
                ),
                "Request summary",
                display="flex",
                align_items="center",
                gap="8px",
            ),
            rx.el.label(
                rx.el.input(
                    type="checkbox",
                    checked=relationship["reason"].to(bool),
                    on_change=SET_FIELD(identifier, "transfer_reason"),
                ),
                "Transfer reason",
                display="flex",
                align_items="center",
                gap="8px",
            ),
            rx.cond(
                relationship["error"].to(str) != "",
                rx.el.span("Invalid transfer fields", id=identifier + "-error", color="#a12622"),
            ),
            aria_label="Relationship " + label,
            aria_invalid=relationship["error"].to(str) != "",
            aria_describedby=rx.cond(
                relationship["error"].to(str) != "", identifier + "-error", ""
            ),
            border="1px solid #dce1df",
            border_radius="6px",
            padding="18px",
            margin="0",
            display="flex",
            flex_direction="column",
            gap="14px",
            min_width="0",
        ),
    )
