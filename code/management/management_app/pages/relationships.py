"""Policy-filtered handover paths and allowlisted context-field editing."""

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


def relationships() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.section(
            rx.heading("Relationships", size="5"),
            rx.el.form(
                rx.el.label("Source agent", html_for="relationship-source"),
                rx.el.select(
                    rx.el.option("Select source agent", value=""),
                    rx.foreach(
                        Editor.relationship_sources,
                        lambda item: rx.el.option(item["name"], value=item["id"]),
                    ),
                    id="relationship-source",
                    value=Editor.relationship_source,
                    on_change=Editor.select_relationship_source,
                    required=True,
                    width="100%",
                    padding="9px 10px",
                    border="1px solid #b8c3bd",
                    border_radius="4px",
                ),
                rx.el.label("Destination agent", html_for="relationship-target"),
                rx.el.select(
                    rx.el.option("Select destination agent", value=""),
                    rx.foreach(
                        Editor.relationship_destinations,
                        lambda item: rx.el.option(item["name"], value=item["id"]),
                    ),
                    id="relationship-target",
                    name="target",
                    required=True,
                    width="100%",
                    padding="9px 10px",
                    border="1px solid #b8c3bd",
                    border_radius="4px",
                ),
                rx.button(
                    rx.icon("plus", size=16), "Add relationship", type="submit", variant="outline"
                ),
                on_submit=Editor.add_relationship,
                aria_label="New relationship",
                display="flex",
                flex_direction="column",
                gap="8px",
                max_width="560px",
                width="100%",
                margin="16px 0",
            ),
            rx.grid(
                rx.foreach(Editor.relationship_rows, relationship_row),
                columns=rx.breakpoints(initial="1", lg="2"),
                spacing="5",
                width="100%",
            ),
            width="100%",
            min_width="0",
        ),
    )
