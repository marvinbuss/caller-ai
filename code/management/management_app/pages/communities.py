"""Settings panel rows for a custom community and its connections.

A custom community shows its name, routing description, entry agent (only an enabled agent of
that community can be chosen), whether Routing offers it, and its unreachable or invalid state.
Deleting it asks for confirmation that names the connections removed with it. Every change is a
server event and the server decides whether it is allowed.
"""

from typing import Any, cast

import reflex as rx
from reflex.event import EventHandler
from reflex.vars.object import ObjectVar

from management_app.pages.agents import ERRORS, field_control
from management_app.state.configuration_editor import ConfigurationEditorState as Editor

EDIT_COMMUNITY = cast(EventHandler, Editor.edit_community)
DELETE_COMMUNITY = cast(EventHandler, Editor.delete_community)
ENABLE_COMMUNITY = cast(EventHandler, Editor.set_community_enabled)
ROUTABLE = cast(EventHandler, Editor.set_routable)
DELETE_CONNECTION = cast(EventHandler, Editor.delete_community_connection)


def community_row(community: ObjectVar[dict[str, Any]]) -> rx.Component:
    identifier = community["communityId"].to(str)
    prefix = "communities[" + community["index"].to(str) + "]."
    return cast(
        rx.Component,
        rx.el.div(
            rx.flex(
                rx.heading(community["name"].to(str), size="4", overflow_wrap="anywhere"),
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
                            rx.cond(
                                community["removedConnections"].to(str) != "",
                                "The community, its agents and these connections will be removed: "
                                + community["removedConnections"].to(str),
                                "The community and its agents will be removed.",
                            )
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
            rx.grid(
                rx.el.label(
                    rx.el.input(
                        type="checkbox",
                        checked=community["routable"].to(bool),
                        on_change=ROUTABLE(identifier),
                    ),
                    "Routable from Routing",
                    display="flex",
                    align_items="center",
                    gap="8px",
                ),
                columns="1",
                width="100%",
            ),
            rx.cond(
                community["unreachable"].to(bool),
                rx.text(
                    "\u26a0 Unreachable: no caller can reach this community. Make it routable or "
                    "connect it from a reachable community.",
                    color="#8a4b00",
                    role="note",
                ),
            ),
            rx.cond(
                community["invalid"].to(bool),
                rx.text(
                    "\u26d4 Invalid: the entry agent must be an enabled agent of this community.",
                    color="#a12622",
                    role="note",
                ),
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


def community_connection_row(connection: ObjectVar[dict[str, Any]]) -> rx.Component:
    return cast(
        rx.Component,
        rx.el.li(
            rx.flex(
                rx.text(connection["source"].to(str), overflow_wrap="anywhere", min_width="0"),
                rx.icon("arrow-right", size=16),
                rx.text(connection["target"].to(str), overflow_wrap="anywhere", min_width="0"),
                rx.spacer(),
                rx.alert_dialog.root(
                    rx.alert_dialog.trigger(
                        rx.icon_button(
                            rx.icon("trash-2", size=16),
                            variant="ghost",
                            color_scheme="red",
                            title="Delete connection",
                            aria_label="Delete connection "
                            + connection["source"].to(str)
                            + " to "
                            + connection["target"].to(str),
                        )
                    ),
                    rx.alert_dialog.content(
                        rx.alert_dialog.title("Delete connection?"),
                        rx.alert_dialog.description(
                            connection["source"].to(str) + " to " + connection["target"].to(str)
                        ),
                        rx.flex(
                            rx.alert_dialog.cancel(rx.button("Cancel", variant="soft")),
                            rx.alert_dialog.action(
                                rx.button(
                                    "Delete connection",
                                    color_scheme="red",
                                    on_click=DELETE_CONNECTION(connection["id"].to(str)),
                                )
                            ),
                            justify="end",
                            gap="10px",
                            margin_top="20px",
                        ),
                    ),
                ),
                align="center",
                gap="8px",
                wrap="wrap",
                width="100%",
            ),
            border_bottom="1px solid #dce1df",
            padding="8px 0",
        ),
    )
