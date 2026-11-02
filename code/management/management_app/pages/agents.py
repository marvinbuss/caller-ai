"""Accessible agent controls; protected lifecycle roles expose only safe prompt/name edits."""

from typing import Any, cast

import reflex as rx
from reflex.event import EventHandler
from reflex.vars.object import ObjectVar

from management_app.components.configuration_graph import ConfigurationGraph
from management_app.state.configuration_editor import ConfigurationEditorState as Editor

ERRORS = cast(ObjectVar[dict[str, str]], Editor.errors)
EDIT_AGENT = cast(EventHandler, Editor.edit_agent)
DELETE_AGENT = cast(EventHandler, Editor.delete_agent)
ENABLE_AGENT = cast(EventHandler, Editor.set_agent_enabled)
SET_POSITION = cast(EventHandler, Editor.set_agent_coordinate)


def field_control(label: str, control: rx.Component, identifier: Any, path: Any) -> rx.Component:
    if control.tag in {"input", "textarea"} and "on_change" in control.event_triggers:
        control = rx.debounce_input(control, debounce_timeout=200, force_notify_on_blur=True)
    return cast(
        rx.Component,
        rx.el.div(
            rx.el.label(label, html_for=identifier),
            control,
            rx.cond(
                ERRORS.contains(path),
                rx.el.span(
                    "Invalid value", id=identifier + "-error", color="#a12622", font_size="13px"
                ),
            ),
            display="flex",
            flex_direction="column",
            gap="6px",
            min_width="0",
        ),
    )


def agent_row(agent: ObjectVar[dict[str, Any]]) -> rx.Component:
    identifier = agent["agentId"].to(str)
    prefix = "agents[" + agent["index"].to(str) + "]."
    name_id, prompt_id = identifier + "-name", identifier + "-prompt"
    return cast(
        rx.Component,
        rx.el.fieldset(
            rx.el.legend(
                agent["name"].to(str), font_weight="600", font_size="16px", overflow_wrap="anywhere"
            ),
            rx.flex(
                rx.badge(agent["role"].to(str), color_scheme="gray"),
                rx.spacer(),
                rx.cond(
                    ~agent["protected"].to(bool),
                    rx.alert_dialog.root(
                        rx.alert_dialog.trigger(
                            rx.icon_button(
                                rx.icon("trash-2", size=16),
                                aria_label="Delete agent",
                                title="Delete agent",
                                variant="ghost",
                                color_scheme="red",
                            )
                        ),
                        rx.alert_dialog.content(
                            rx.alert_dialog.title("Delete agent?"),
                            rx.alert_dialog.description(agent["name"].to(str)),
                            rx.flex(
                                rx.alert_dialog.cancel(rx.button("Cancel", variant="soft")),
                                rx.alert_dialog.action(
                                    rx.button(
                                        "Delete agent",
                                        color_scheme="red",
                                        on_click=DELETE_AGENT(identifier),
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
                width="100%",
            ),
            rx.grid(
                field_control(
                    "Name",
                    rx.el.input(
                        id=name_id,
                        value=agent["name"].to(str),
                        on_change=EDIT_AGENT(identifier, "name"),
                        aria_invalid=ERRORS.contains(prefix + "name"),
                        aria_describedby=rx.cond(
                            ERRORS.contains(prefix + "name"), name_id + "-error", ""
                        ),
                        width="100%",
                        padding="9px 10px",
                        border="1px solid #b8c3bd",
                        border_radius="4px",
                    ),
                    name_id,
                    prefix + "name",
                ),
                rx.el.div(
                    rx.el.label("Community", html_for=identifier + "-community"),
                    rx.el.select(
                        rx.el.option("Lifecycle", value=""),
                        rx.foreach(
                            agent["communityOptions"].to(list[dict[str, str]]),
                            lambda community: rx.el.option(
                                community["name"], value=community["id"]
                            ),
                        ),
                        id=identifier + "-community",
                        value=rx.cond(
                            agent["protected"].to(bool), "", agent["communityId"].to(str)
                        ),
                        disabled=agent["protected"].to(bool) | (Editor.selected_community_id != ""),
                        on_change=EDIT_AGENT(identifier, "communityId"),
                        padding="9px 10px",
                        border="1px solid #b8c3bd",
                        border_radius="4px",
                        width="100%",
                    ),
                    display="flex",
                    flex_direction="column",
                    gap="6px",
                    min_width="0",
                ),
                columns=rx.breakpoints(initial="1", md="2"),
                spacing="4",
                width="100%",
            ),
            field_control(
                "Prompt",
                rx.el.textarea(
                    id=prompt_id,
                    value=agent["prompt"].to(str),
                    on_change=EDIT_AGENT(identifier, "prompt"),
                    rows=4,
                    aria_invalid=ERRORS.contains(prefix + "prompt"),
                    aria_describedby=rx.cond(
                        ERRORS.contains(prefix + "prompt"), prompt_id + "-error", ""
                    ),
                    width="100%",
                    padding="10px",
                    border="1px solid #b8c3bd",
                    border_radius="4px",
                    resize="vertical",
                ),
                prompt_id,
                prefix + "prompt",
            ),
            rx.cond(
                ~agent["protected"].to(bool),
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
                                value=agent["positionXText" if axis == "x" else "positionYText"].to(
                                    str
                                ),
                                on_change=SET_POSITION(identifier, axis),
                                width="100%",
                                padding="9px 10px",
                                border="1px solid #b8c3bd",
                                border_radius="4px",
                            ),
                            identifier + "-position-" + axis,
                            "layout.agentsByCommunity." + identifier + "." + axis,
                        )
                        for axis in ("x", "y")
                    ],
                    columns="2",
                    spacing="4",
                    width="100%",
                ),
            ),
            rx.el.label(
                rx.el.input(
                    type="checkbox",
                    checked=agent["enabled"].to(bool),
                    disabled=agent["protected"].to(bool),
                    on_change=ENABLE_AGENT(identifier),
                ),
                "Enabled",
                display="flex",
                align_items="center",
                gap="8px",
            ),
            id=identifier,
            aria_label=agent["groupLabel"].to(str),
            border="1px solid #dce1df",
            border_radius="6px",
            padding="18px",
            display="flex",
            flex_direction="column",
            gap="16px",
            min_width="0",
            margin="0",
        ),
    )


def agents() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.section(
            rx.heading("Agents", size="5"),
            rx.cond(
                Editor.selected_community_id != "",
                rx.vstack(
                    rx.el.label(
                        "Community entry agent",
                        rx.el.select(
                            rx.foreach(
                                Editor.selected_entry_choices,
                                lambda agent: rx.el.option(
                                    agent["name"].to(str), value=agent["agentId"].to(str)
                                ),
                            ),
                            value=Editor.selected_community_entry,
                            on_change=Editor.select_community_entry,
                            aria_label="Community entry agent",
                            padding="9px 10px",
                            border="1px solid #b8c3bd",
                            border_radius="4px",
                            width="100%",
                        ),
                        display="flex",
                        flex_direction="column",
                        gap="6px",
                        width="100%",
                    ),
                    ConfigurationGraph.create(
                        nodes=Editor.agent_nodes,
                        edges=Editor.agent_edges,
                        view_key=Editor.selected_community_id,
                        dirty=Editor.dirty,
                        guard_leave=False,
                        on_move=Editor.move_agent,
                        on_link=Editor.connect_agents,
                        on_pick=Editor.focus_agent,
                    ),
                    width="100%",
                    align="stretch",
                    spacing="4",
                ),
            ),
            rx.el.form(
                rx.el.label(
                    "Name",
                    rx.el.input(
                        name="name",
                        aria_label="New agent name",
                        placeholder="Agent name",
                        required=True,
                        padding="9px 10px",
                        border="1px solid #b8c3bd",
                        border_radius="4px",
                        width="100%",
                    ),
                    display="flex",
                    flex_direction="column",
                    gap="6px",
                    min_width="0",
                ),
                rx.el.label(
                    "Community",
                    rx.el.select(
                        rx.foreach(
                            Editor.agent_community_options,
                            lambda item: rx.el.option(
                                item["name"].to(str), value=item["communityId"].to(str)
                            ),
                        ),
                        name="community",
                        aria_label="New agent community",
                        required=True,
                        padding="9px 10px",
                        border="1px solid #b8c3bd",
                        border_radius="4px",
                        width="100%",
                    ),
                    display="flex",
                    flex_direction="column",
                    gap="6px",
                    min_width="0",
                ),
                rx.el.label(
                    "Prompt",
                    rx.el.input(
                        name="prompt",
                        aria_label="New agent prompt",
                        placeholder="Prompt",
                        required=True,
                        padding="9px 10px",
                        border="1px solid #b8c3bd",
                        border_radius="4px",
                        width="100%",
                    ),
                    display="flex",
                    flex_direction="column",
                    gap="6px",
                    min_width="0",
                ),
                rx.button(rx.icon("plus", size=16), "Add agent", type="submit", variant="outline"),
                on_submit=Editor.add_agent,
                aria_label="New agent",
                display="grid",
                grid_template_columns=rx.breakpoints(initial="1fr", md="repeat(2, minmax(0, 1fr))"),
                align_items="end",
                gap="10px",
                margin="16px 0",
            ),
            rx.grid(
                rx.foreach(Editor.agent_rows, agent_row),
                columns=rx.breakpoints(initial="1", lg="2"),
                spacing="5",
                width="100%",
            ),
            width="100%",
            min_width="0",
        ),
    )
