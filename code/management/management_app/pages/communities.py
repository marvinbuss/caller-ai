"""Community routing metadata and safe enabled entry-agent selection.

The community list page and the single-community page: each community shows its name, routing
description and entry agent (only an enabled agent of that community can be chosen), its graph
position and the connections to other communities. Deleting a community asks for confirmation
because it also removes its agents and the transfer paths that touch them. As with the other
editor pages, every change is a server event and the server decides whether it is allowed.
"""

from typing import Any, cast

import reflex as rx
from reflex.event import EventHandler
from reflex.vars.object import ObjectVar

from management_app.components.configuration_graph import (
    ConfigurationGraph,
    ConfigurationLeaveGuard,
)
from management_app.pages.agents import ERRORS, field_control
from management_app.state.configuration_editor import ConfigurationEditorState as Editor

EDIT_COMMUNITY = cast(EventHandler, Editor.edit_community)
DELETE_COMMUNITY = cast(EventHandler, Editor.delete_community)
ENABLE_COMMUNITY = cast(EventHandler, Editor.set_community_enabled)
SET_POSITION = cast(EventHandler, Editor.set_community_coordinate)
DELETE_CONNECTION = cast(EventHandler, Editor.delete_community_connection)


def community_row(community: ObjectVar[dict[str, Any]]) -> rx.Component:
    identifier = community["communityId"].to(str)
    prefix = "communities[" + community["index"].to(str) + "]."
    return cast(
        rx.Component,
        rx.el.div(
            rx.flex(
                rx.link(
                    rx.heading(community["name"].to(str), size="4", overflow_wrap="anywhere"),
                    href="#",
                    on_click=[rx.prevent_default, Editor.open_community(identifier)],
                    min_width="0",
                ),
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
            rx.grid(
                *[
                    field_control(
                        axis.upper() + " position",
                        rx.el.input(
                            type="number",
                            step="any",
                            min=-100000,
                            max=100000,
                            id=identifier + "-position-" + axis,
                            value=community["positionXText" if axis == "x" else "positionYText"].to(
                                str
                            ),
                            on_change=SET_POSITION(identifier, axis),
                            width="100%",
                            padding="9px 10px",
                            border="1px solid #b8c3bd",
                            border_radius="4px",
                        ),
                        identifier + "-position-" + axis,
                        "layout.communities[" + community["index"].to(str) + "]." + axis,
                    )
                    for axis in ("x", "y")
                ],
                columns="2",
                spacing="4",
                width="100%",
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


def community_connections() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.section(
            rx.heading("Community connections", size="4"),
            rx.el.form(
                *[
                    rx.el.label(
                        label,
                        rx.el.select(
                            rx.el.option("Select community", value=""),
                            rx.foreach(
                                Editor.community_rows,
                                lambda community: rx.el.option(
                                    community["name"].to(str),
                                    value=community["communityId"].to(str),
                                ),
                            ),
                            name=name,
                            required=True,
                            aria_label=label,
                            padding="9px 10px",
                            border="1px solid #b8c3bd",
                            border_radius="4px",
                            width="100%",
                        ),
                        display="flex",
                        flex_direction="column",
                        gap="6px",
                        min_width="0",
                    )
                    for name, label in (
                        ("source", "Source community"),
                        ("target", "Destination community"),
                    )
                ],
                rx.button(
                    rx.icon("plus", size=16),
                    "Add connection",
                    type="submit",
                    variant="outline",
                    disabled=Editor.community_rows.length() < 2,
                ),
                on_submit=Editor.add_community_connection,
                aria_label="New community connection",
                display="grid",
                gap="10px",
                align_items="end",
                margin="16px 0",
                grid_template_columns=rx.breakpoints(initial="1fr", md="1fr 1fr auto"),
            ),
            rx.el.ul(
                rx.foreach(Editor.community_connection_rows, community_connection_row),
                padding="0",
                margin="0",
                list_style_type="none",
            ),
            width="100%",
            min_width="0",
        ),
    )


def communities() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.section(
            rx.heading("Communities", size="5"),
            rx.cond(
                Editor.community_rows.length() == 0,
                rx.text("No communities", color="#58615c", margin="12px 0"),
            ),
            ConfigurationGraph.create(
                nodes=Editor.community_nodes,
                edges=Editor.community_edges,
                view_key="communities",
                dirty=Editor.dirty,
                on_move=Editor.move_community,
                on_link=Editor.connect_communities,
                on_pick=Editor.open_community,
            ),
            community_connections(),
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


def configuration_body() -> rx.Component:
    from management_app.pages.agents import agents
    from management_app.pages.relationships import relationships
    from management_app.pages.tools import tools

    return cast(
        rx.Component,
        rx.box(
            rx.cond(
                Editor.selected_community_id != "",
                ConfigurationLeaveGuard.create(dirty=Editor.dirty),
            ),
            rx.box(
                rx.flex(
                    rx.heading("Community and Agent", size="7"),
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
                        rx.cond(
                            Editor.selected_community_id == "",
                            rx.vstack(
                                communities(),
                                rx.el.details(
                                    rx.el.summary(
                                        "Global settings", cursor="pointer", font_weight="600"
                                    ),
                                    agents(),
                                    tools(),
                                    relationships(),
                                    width="100%",
                                    data_testid="global-settings",
                                ),
                                width="100%",
                                align="stretch",
                                spacing="6",
                            ),
                            rx.vstack(
                                rx.button(
                                    rx.icon("arrow-left", size=16),
                                    "Communities",
                                    on_click=Editor.close_community,
                                    variant="ghost",
                                ),
                                rx.cond(
                                    Editor.selection_error != "",
                                    rx.text(Editor.selection_error, role="alert"),
                                    rx.vstack(
                                        rx.heading(
                                            Editor.selected_community_name,
                                            size="6",
                                            overflow_wrap="anywhere",
                                        ),
                                        agents(),
                                        relationships(),
                                        tools(),
                                        width="100%",
                                        align="stretch",
                                        spacing="6",
                                    ),
                                ),
                                width="100%",
                                align="stretch",
                                spacing="6",
                            ),
                        ),
                        width="100%",
                        align="stretch",
                        spacing="8",
                    ),
                    rx.text(Editor.selection_error, role="alert"),
                ),
                max_width="1200px",
                width="100%",
            ),
            width="100%",
        ),
    )
