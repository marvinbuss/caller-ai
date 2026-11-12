"""Reviewed public read-only catalogs and exact ordinary-agent assignment controls.

The page lists tools and MCP connections that were added from the reviewed catalog (a manager
cannot define new ones here) and lets a manager enable them, edit their reviewed metadata, and
assign exact actions and input constraints to ordinary agents. Protected lifecycle roles get no
assignment controls. The page only renders state; the server checks every change against the
reviewed catalog.
"""

from typing import Any, cast

import reflex as rx
from reflex.event import EventHandler
from reflex.vars.object import ObjectVar

from management_app.pages.agents import ERRORS, field_control
from management_app.state.configuration_editor import ConfigurationEditorState as Editor

EDIT_TOOL = cast(EventHandler, Editor.edit_tool)
ENABLE_TOOL = cast(EventHandler, Editor.set_tool_enabled)
DELETE_TOOL = cast(EventHandler, Editor.delete_tool)
EDIT_CONNECTION = cast(EventHandler, Editor.edit_connection)
ENABLE_CONNECTION = cast(EventHandler, Editor.set_connection_enabled)
DELETE_CONNECTION = cast(EventHandler, Editor.delete_connection)
SET_ACTION = cast(EventHandler, Editor.set_action)

INPUT = {
    "width": "100%",
    "padding": "9px 10px",
    "border": "1px solid #b8c3bd",
    "border_radius": "4px",
}


def tool_row(tool: ObjectVar[dict[str, Any]]) -> rx.Component:
    identifier = tool["toolId"].to(str)
    prefix = "tools[" + tool["index"].to(str) + "]."
    return cast(
        rx.Component,
        rx.el.fieldset(
            rx.el.legend(tool["name"].to(str), font_weight="600", font_size="16px"),
            rx.flex(
                rx.badge("public", color_scheme="green"),
                rx.badge("read_only", color_scheme="gray"),
                rx.spacer(),
                rx.alert_dialog.root(
                    rx.alert_dialog.trigger(
                        rx.icon_button(
                            rx.icon("trash-2", size=16),
                            aria_label="Remove tool",
                            title="Remove tool",
                            color_scheme="red",
                            variant="ghost",
                        )
                    ),
                    rx.alert_dialog.content(
                        rx.alert_dialog.title("Remove tool?"),
                        rx.alert_dialog.description(
                            rx.cond(
                                tool["usedBy"].to(str) != "",
                                "These agents lose this tool: " + tool["usedBy"].to(str),
                                "No agent uses this tool.",
                            )
                        ),
                        rx.flex(
                            rx.alert_dialog.cancel(rx.button("Cancel", variant="soft")),
                            rx.alert_dialog.action(
                                rx.button(
                                    "Remove tool",
                                    color_scheme="red",
                                    on_click=DELETE_TOOL(identifier),
                                )
                            ),
                            justify="end",
                            gap="10px",
                            margin_top="20px",
                        ),
                    ),
                ),
                gap="8px",
                align="center",
            ),
            field_control(
                "Name",
                rx.el.input(
                    id=identifier + "-name",
                    value=tool["name"].to(str),
                    on_change=EDIT_TOOL(identifier, "name"),
                    aria_invalid=ERRORS.contains(prefix + "name"),
                    aria_describedby=rx.cond(
                        ERRORS.contains(prefix + "name"), identifier + "-name-error", ""
                    ),
                    **INPUT,
                ),
                identifier + "-name",
                prefix + "name",
            ),
            field_control(
                "Description",
                rx.el.textarea(
                    id=identifier + "-description",
                    value=tool["description"].to(str),
                    on_change=EDIT_TOOL(identifier, "description"),
                    rows=2,
                    aria_invalid=ERRORS.contains(prefix + "description"),
                    aria_describedby=rx.cond(
                        ERRORS.contains(prefix + "description"),
                        identifier + "-description-error",
                        "",
                    ),
                    **INPUT,
                ),
                identifier + "-description",
                prefix + "description",
            ),
            rx.el.label(
                rx.el.input(
                    type="checkbox",
                    checked=tool["enabled"].to(bool),
                    on_change=ENABLE_TOOL(identifier),
                ),
                "Enabled",
                display="flex",
                align_items="center",
                gap="8px",
            ),
            rx.el.dl(
                rx.el.dt("Destination reference"),
                rx.el.dd(tool["approvedDestinationRef"].to(str)),
                rx.el.dt("MCP connection"),
                rx.el.dd(tool["connection"].to(str)),
                rx.el.dt("Output fields"),
                rx.el.dd(tool["outputs"].to(str)),
                display="grid",
                grid_template_columns="minmax(0, 1fr)",
                gap="5px",
                font_size="14px",
                overflow_wrap="anywhere",
            ),
            rx.el.details(
                rx.el.summary("Input schema"),
                rx.el.pre(
                    tool["schemaJson"].to(str),
                    white_space="pre-wrap",
                    overflow_wrap="anywhere",
                    font_size="13px",
                ),
            ),
            aria_label="Tool " + tool["groupLabel"].to(str),
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


def connection_row(connection: ObjectVar[dict[str, Any]]) -> rx.Component:
    identifier = connection["connectionId"].to(str)
    prefix = "mcpConnections[" + connection["index"].to(str) + "]."
    return cast(
        rx.Component,
        rx.el.fieldset(
            rx.el.legend(connection["name"].to(str), font_weight="600", font_size="16px"),
            rx.flex(
                rx.text(
                    connection["approvedEndpointRef"].to(str), size="2", overflow_wrap="anywhere"
                ),
                rx.spacer(),
                rx.alert_dialog.root(
                    rx.alert_dialog.trigger(
                        rx.icon_button(
                            rx.icon("trash-2", size=16),
                            aria_label="Remove MCP connection",
                            title="Remove MCP connection",
                            color_scheme="red",
                            variant="ghost",
                        )
                    ),
                    rx.alert_dialog.content(
                        rx.alert_dialog.title("Remove MCP connection?"),
                        rx.alert_dialog.description("Linked tools must be removed first."),
                        rx.flex(
                            rx.alert_dialog.cancel(rx.button("Cancel", variant="soft")),
                            rx.alert_dialog.action(
                                rx.button(
                                    "Remove connection",
                                    color_scheme="red",
                                    on_click=DELETE_CONNECTION(identifier),
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
            ),
            field_control(
                "Name",
                rx.el.input(
                    id=identifier + "-name",
                    value=connection["name"].to(str),
                    on_change=EDIT_CONNECTION(identifier, "name"),
                    aria_invalid=ERRORS.contains(prefix + "name"),
                    aria_describedby=rx.cond(
                        ERRORS.contains(prefix + "name"), identifier + "-name-error", ""
                    ),
                    **INPUT,
                ),
                identifier + "-name",
                prefix + "name",
            ),
            field_control(
                "Credential reference",
                rx.el.select(
                    rx.foreach(
                        connection["credentialOptions"].to(list[str]),
                        lambda ref: rx.el.option(ref, value=ref),
                    ),
                    id=identifier + "-credential",
                    value=connection["credentialRef"].to(str),
                    on_change=EDIT_CONNECTION(identifier, "credentialRef"),
                    aria_invalid=ERRORS.contains(prefix + "credentialRef"),
                    aria_describedby=rx.cond(
                        ERRORS.contains(prefix + "credentialRef"),
                        identifier + "-credential-error",
                        "",
                    ),
                    **INPUT,
                ),
                identifier + "-credential",
                prefix + "credentialRef",
            ),
            rx.el.label(
                rx.el.input(
                    type="checkbox",
                    checked=connection["enabled"].to(bool),
                    on_change=ENABLE_CONNECTION(identifier),
                ),
                "Enabled",
                display="flex",
                align_items="center",
                gap="8px",
            ),
            rx.el.details(
                rx.el.summary("Reviewed actions"),
                rx.foreach(
                    connection["reviewedCapabilityCatalog"].to(list[dict[str, Any]]),
                    lambda action: rx.el.div(
                        action["actionName"].to(str),
                        " / public / read_only",
                        padding="8px 0",
                        border_top="1px solid #dce1df",
                    ),
                ),
            ),
            aria_label="MCP connection " + connection["groupLabel"].to(str),
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


def assignment_group(agent: ObjectVar[dict[str, Any]]) -> rx.Component:
    agent_id = agent["id"].to(str)

    def assignment(tool: ObjectVar[dict[str, Any]]) -> rx.Component:
        tool_id = tool["id"].to(str)
        return cast(
            rx.Component,
            rx.el.div(
                rx.text(tool["name"].to(str), weight="medium"),
                rx.foreach(
                    tool["actions"].to(list[dict[str, Any]]),
                    lambda action: rx.el.label(
                        rx.el.input(
                            type="checkbox",
                            checked=action["checked"].to(bool),
                            disabled=(~tool["available"].to(bool)) & (~action["checked"].to(bool)),
                            on_change=SET_ACTION(agent_id, tool_id, action["name"].to(str)),
                        ),
                        action["name"].to(str),
                        display="flex",
                        gap="8px",
                        align_items="center",
                    ),
                ),
                rx.cond(
                    tool["assigned"].to(bool),
                    rx.el.form(
                        rx.el.input(type="hidden", name="agent", value=agent_id),
                        rx.el.input(type="hidden", name="tool", value=tool_id),
                        rx.el.label(
                            "Input constraints", html_for=agent_id + "-" + tool_id + "-constraints"
                        ),
                        rx.el.textarea(
                            id=agent_id + "-" + tool_id + "-constraints",
                            name="constraints",
                            default_value=tool["constraints"].to(str),
                            rows=2,
                            max_length=4096,
                            **INPUT,
                        ),
                        rx.button(
                            rx.icon("check", size=16),
                            "Apply constraints",
                            type="submit",
                            variant="outline",
                        ),
                        on_submit=Editor.edit_constraints,
                        display="flex",
                        flex_direction="column",
                        gap="8px",
                        margin_top="8px",
                    ),
                ),
                role="group",
                aria_label="Assignment " + tool["name"].to(str),
                border_top="1px solid #dce1df",
                padding="14px 0",
                display="flex",
                flex_direction="column",
                gap="10px",
                min_width="0",
            ),
        )

    return cast(
        rx.Component,
        rx.el.fieldset(
            rx.el.legend(agent["name"].to(str), font_weight="600", font_size="16px"),
            rx.foreach(agent["tools"].to(list[dict[str, Any]]), assignment),
            aria_label="Assignments for " + agent["name"].to(str),
            border="1px solid #dce1df",
            border_radius="6px",
            padding="18px",
            margin="0",
            min_width="0",
        ),
    )


def tools() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.section(
            rx.heading("Tools", size="5"),
            rx.heading("MCP connections", size="4", margin_top="20px"),
            rx.el.form(
                rx.el.select(
                    rx.el.option("Select reviewed connection", value=""),
                    rx.foreach(
                        Editor.available_connections,
                        lambda item: rx.el.option(item["name"].to(str), value=item["id"].to(str)),
                    ),
                    name="connection",
                    aria_label="Reviewed MCP connection",
                    required=True,
                    **INPUT,
                ),
                rx.button(
                    rx.icon("plus", size=16), "Add MCP connection", type="submit", variant="outline"
                ),
                on_submit=Editor.add_connection,
                aria_label="Add reviewed MCP connection",
                display="flex",
                flex_wrap="wrap",
                gap="10px",
                margin="14px 0",
            ),
            rx.grid(
                rx.foreach(Editor.connection_rows, connection_row),
                columns=rx.breakpoints(initial="1", lg="2"),
                spacing="5",
                width="100%",
            ),
            rx.heading("Tools", size="4", margin_top="24px"),
            rx.el.form(
                rx.el.select(
                    rx.el.option("Select reviewed tool", value=""),
                    rx.foreach(
                        Editor.available_tools,
                        lambda item: rx.el.option(
                            item["name"].to(str),
                            value=item["id"].to(str),
                            disabled=~item["ready"].to(bool),
                        ),
                    ),
                    name="tool",
                    aria_label="Reviewed tool",
                    required=True,
                    **INPUT,
                ),
                rx.button(rx.icon("plus", size=16), "Add tool", type="submit", variant="outline"),
                on_submit=Editor.add_tool,
                aria_label="Add reviewed tool",
                display="flex",
                flex_wrap="wrap",
                gap="10px",
                margin="14px 0",
            ),
            rx.grid(
                rx.foreach(Editor.tool_rows, tool_row),
                columns=rx.breakpoints(initial="1", lg="2"),
                spacing="5",
                width="100%",
            ),
            width="100%",
            min_width="0",
            data_testid="tools-section",
        ),
    )
