"""Community routing metadata and safe enabled entry-agent selection."""

from typing import Any, cast

import reflex as rx
from reflex.event import EventHandler
from reflex.vars.object import ObjectVar

from management_app.pages.agents import ERRORS, field_control
from management_app.state.configuration_editor import ConfigurationEditorState as Editor

EDIT_COMMUNITY = cast(EventHandler, Editor.edit_community)
DELETE_COMMUNITY = cast(EventHandler, Editor.delete_community)
ENABLE_COMMUNITY = cast(EventHandler, Editor.set_community_enabled)


def community_row(community: ObjectVar[dict[str, Any]]) -> rx.Component:
    identifier = community["communityId"].to(str)
    prefix = "communities[" + community["index"].to(str) + "]."
    return cast(
        rx.Component,
        rx.el.div(
            rx.flex(
                rx.heading(community["name"].to(str), size="4"),
                rx.spacer(),
                rx.alert_dialog.root(
                    rx.alert_dialog.trigger(
                        rx.icon_button(
                            rx.icon("trash-2", size=16),
                            aria_label="Delete community",
                            title="Delete community",
                            color_scheme="red",
                            variant="ghost",
                        )
                    ),
                    rx.alert_dialog.content(
                        rx.alert_dialog.title("Delete community?"),
                        rx.alert_dialog.description(
                            "The community, its agents and related transfer paths will be removed."
                        ),
                        rx.flex(
                            rx.alert_dialog.cancel(rx.button("Cancel", variant="soft")),
                            rx.alert_dialog.action(
                                rx.button(
                                    "Delete community",
                                    color_scheme="red",
                                    on_click=DELETE_COMMUNITY(identifier),
                                )
                            ),
                            justify="end",
                            gap="10px",
                            margin_top="20px",
                        ),
                    ),
                ),
                align="center",
            ),
            rx.grid(
                field_control(
                    "Name",
                    rx.el.input(
                        value=community["name"].to(str),
                        on_change=EDIT_COMMUNITY(identifier, "name"),
                        id=identifier + "-name",
                        aria_invalid=ERRORS.contains(prefix + "name"),
                        aria_describedby=rx.cond(
                            ERRORS.contains(prefix + "name"), identifier + "-name-error", ""
                        ),
                        width="100%",
                        padding="9px 10px",
                        border="1px solid #b8c3bd",
                        border_radius="4px",
                    ),
                    identifier + "-name",
                    prefix + "name",
                ),
                field_control(
                    "Entry agent",
                    rx.el.select(
                        rx.foreach(
                            community["entryOptions"].to(list[dict[str, str]]),
                            lambda item: rx.el.option(item["name"], value=item["id"]),
                        ),
                        id=identifier + "-entry",
                        value=community["entryAgentId"].to(str),
                        on_change=EDIT_COMMUNITY(identifier, "entryAgentId"),
                        aria_invalid=ERRORS.contains(prefix + "entryAgentId"),
                        aria_describedby=rx.cond(
                            ERRORS.contains(prefix + "entryAgentId"),
                            identifier + "-entry-error",
                            "",
                        ),
                        width="100%",
                        padding="9px 10px",
                        border="1px solid #b8c3bd",
                        border_radius="4px",
                    ),
                    identifier + "-entry",
                    prefix + "entryAgentId",
                ),
                columns=rx.breakpoints(initial="1", md="2"),
                spacing="4",
                width="100%",
            ),
            field_control(
                "Routing description",
                rx.el.textarea(
                    value=community["routingDescription"].to(str),
                    rows=2,
                    id=identifier + "-description",
                    aria_invalid=ERRORS.contains(prefix + "routingDescription"),
                    aria_describedby=rx.cond(
                        ERRORS.contains(prefix + "routingDescription"),
                        identifier + "-description-error",
                        "",
                    ),
                    on_change=EDIT_COMMUNITY(identifier, "routingDescription"),
                    width="100%",
                    padding="10px",
                    border="1px solid #b8c3bd",
                    border_radius="4px",
                ),
                identifier + "-description",
                prefix + "routingDescription",
            ),
            rx.el.label(
                rx.el.input(
                    type="checkbox",
                    checked=community["enabled"].to(bool),
                    on_change=ENABLE_COMMUNITY(identifier),
                ),
                "Enabled",
                display="flex",
                align_items="center",
                gap="8px",
            ),
            role="group",
            aria_label="Community " + community["name"].to(str),
            border_top="1px solid #dce1df",
            padding="20px 0",
            display="flex",
            flex_direction="column",
            gap="14px",
            min_width="0",
        ),
    )


def communities() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.section(
            rx.heading("Communities", size="5"),
            rx.el.form(
                *[
                    rx.el.input(
                        name=name,
                        aria_label=label,
                        placeholder=label,
                        required=True,
                        padding="9px 10px",
                        border="1px solid #b8c3bd",
                        border_radius="4px",
                        min_width="0",
                    )
                    for name, label in [
                        ("name", "Community name"),
                        ("description", "Routing description"),
                        ("entry", "Entry agent name"),
                        ("prompt", "Entry agent prompt"),
                    ]
                ],
                rx.button(
                    rx.icon("plus", size=16), "Add community", type="submit", variant="outline"
                ),
                on_submit=Editor.add_community,
                aria_label="New community",
                display="grid",
                grid_template_columns=rx.breakpoints(initial="1fr", md="repeat(2, minmax(0, 1fr))"),
                gap="10px",
                margin="16px 0",
            ),
            rx.foreach(Editor.community_rows, community_row),
            width="100%",
            min_width="0",
        ),
    )


def configuration_page() -> rx.Component:
    from management_app.pages.agents import agents
    from management_app.pages.relationships import relationships
    from management_app.pages.tools import tools

    return cast(
        rx.Component,
        rx.box(
            rx.el.header(
                rx.flex(
                    rx.icon("headset", size=22, color="#197767"),
                    rx.text("CALLER / CONTROL", weight="bold"),
                    rx.spacer(),
                    rx.link("System status", href="/"),
                    align="center",
                    max_width="1200px",
                    margin="0 auto",
                    gap="10px",
                ),
                padding="20px",
                border_bottom="1px solid #dce1df",
                background="#f4f7f6",
                role="banner",
            ),
            rx.el.main(
                rx.flex(
                    rx.heading("Configuration", size="7"),
                    rx.spacer(),
                    rx.cond(
                        Editor.authorized,
                        rx.alert_dialog.root(
                            rx.alert_dialog.trigger(
                                rx.icon_button(
                                    rx.icon("rotate-ccw", size=16),
                                    aria_label="Reload configuration",
                                    title="Reload configuration",
                                    variant="outline",
                                )
                            ),
                            rx.alert_dialog.content(
                                rx.alert_dialog.title("Discard unsaved changes?"),
                                rx.alert_dialog.description("Reload the latest saved revision."),
                                rx.flex(
                                    rx.alert_dialog.cancel(rx.button("Cancel", variant="soft")),
                                    rx.alert_dialog.action(
                                        rx.button("Reload", on_click=Editor.load)
                                    ),
                                    justify="end",
                                    gap="10px",
                                    margin_top="20px",
                                ),
                            ),
                        ),
                    ),
                    rx.cond(
                        Editor.authorized,
                        rx.button(
                            rx.icon("save", size=16),
                            "Save configuration",
                            on_click=Editor.save,
                            disabled=Editor.busy,
                            min_height="38px",
                            flex_shrink="0",
                        ),
                    ),
                    align="center",
                    wrap="wrap",
                    gap="14px",
                ),
                rx.el.div(
                    rx.cond(
                        Editor.error != "",
                        rx.el.div(
                            Editor.error,
                            rx.el.ul(
                                rx.foreach(
                                    Editor.validation_issues,
                                    lambda item: rx.el.li(item["path"] + ": " + item["code"]),
                                )
                            ),
                            role="alert",
                            color="#a12622",
                        ),
                    ),
                    rx.el.span(Editor.status, role="status", color="#197767"),
                    min_height="28px",
                    padding="12px 0",
                ),
                rx.cond(
                    Editor.authorized,
                    rx.vstack(
                        communities(),
                        agents(),
                        tools(),
                        relationships(),
                        width="100%",
                        align="stretch",
                        spacing="8",
                    ),
                    rx.link("Sign in", href="/auth/login"),
                ),
                max_width="1200px",
                margin="0 auto",
                padding=["28px 20px", "36px 32px"],
                width="100%",
                role="main",
            ),
            min_height="100vh",
            background="#ffffff",
            color="#242925",
            font_family="Trebuchet MS, sans-serif",
            letter_spacing="0",
        ),
    )
