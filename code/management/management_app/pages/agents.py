"""Accessible agent controls; protected lifecycle roles expose only safe prompt/name edits.

`agent_row` renders one agent as a labelled fieldset with its editable fields, enable switch and
position inputs; `field_control` is the shared labelled-input helper that the other editor pages
reuse. Rows read the editor state only: every edit is a Reflex event that the server validates,
so the page decides nothing about what is allowed. Protected lifecycle roles cannot be deleted
and show only the fields that are safe to change.
"""

from typing import Any, cast

import reflex as rx
from reflex.event import EventHandler
from reflex.vars.object import ObjectVar

from management_app.state.configuration_editor import ConfigurationEditorState as Editor

ERRORS = cast(ObjectVar[dict[str, str]], Editor.errors)
EDIT_AGENT = cast(EventHandler, Editor.edit_agent)
DELETE_AGENT = cast(EventHandler, Editor.delete_agent)
ENABLE_AGENT = cast(EventHandler, Editor.set_agent_enabled)


def field_control(label: str, control: rx.Component, identifier: Any, path: Any) -> rx.Component:
    if control.tag in {"input", "textarea"} and "on_change" in control.event_triggers:
        # Debounce typing so each keystroke is not a server round trip; blur flushes the last value.
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
                            rx.alert_dialog.description(
                                rx.cond(
                                    Editor.selected_handover_names != "",
                                    agent["name"].to(str)
                                    + " and its handovers will be removed: "
                                    + Editor.selected_handover_names,
                                    agent["name"].to(str) + " will be removed.",
                                )
                            ),
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
                        disabled=agent["protected"].to(bool),
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
                agent["protected"].to(bool),
                rx.text(
                    "Pre-defined agent: it cannot be deleted, moved, disabled or given tools.",
                    size="2",
                    color="#4f5852",
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
            rx.text(
                "Last changed: " + agent["lastChanged"].to(str),
                size="2",
                color="#4f5852",
                data_testid="agent-last-changed",
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
