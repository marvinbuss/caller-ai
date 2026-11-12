"""Community and Agent page at `/communities` (editor role), inside the authenticated frame.

One graph shows the default communities Welcome, Routing and Goodbye with their pre-defined
agents and every custom community with its agents. Selecting a node in the graph or in the
outline list below it opens that node's settings in the panel under the graph; with nothing
selected no panel shows. The Tools section, the publish bar with its confirmation and the
new-revision notice complete the page. Loading slower than a second shows a loading indicator
and a failed load shows an error with Retry.
"""

from typing import Any, cast

import reflex as rx
from reflex.event import EventHandler
from reflex.vars.object import ObjectVar

from management_app.components.configuration_graph import (
    NODE_CSS,
    ConfigurationGraph,
    ConfigurationLeaveGuard,
    Interval,
)
from management_app.components.frame import frame, page_content
from management_app.pages.agents import agent_row
from management_app.pages.communities import community_connection_row, community_row
from management_app.pages.relationships import relationship_row
from management_app.pages.tools import INPUT, assignment_group, tools
from management_app.state.configuration_editor import ConfigurationEditorState as Editor
from management_app.theme import COLORS

NOTICE_SECONDS = 15
PICK = cast(EventHandler, Editor.pick)


def _outline_item(item: ObjectVar[dict[str, Any]]) -> rx.Component:
    return cast(
        rx.Component,
        rx.el.li(
            rx.el.button(
                item["name"].to(str) + " (" + item["labels"].to(str) + ")",
                type="button",
                on_click=PICK(item["id"].to(str)),
                aria_pressed=item["selected"].to(bool),
                class_name="caller-outline-button",
            ),
        ),
    )


def _outline_community(row: ObjectVar[dict[str, Any]]) -> rx.Component:
    return cast(
        rx.Component,
        rx.el.li(
            rx.el.button(
                row["name"].to(str) + " (" + row["labels"].to(str) + ")",
                type="button",
                on_click=PICK(row["id"].to(str)),
                aria_pressed=row["selected"].to(bool),
                class_name="caller-outline-button",
                font_weight="600",
            ),
            rx.el.ul(
                rx.foreach(row["agents"].to(list[dict[str, Any]]), _outline_item),
                padding_left="20px",
            ),
        ),
    )


def outline() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.nav(
            rx.heading("Outline", size="3"),
            rx.el.ul(rx.foreach(Editor.outline, _outline_community), padding_left="0"),
            aria_label="Graph outline",
            data_testid="graph-outline",
            width="100%",
        ),
    )


def _add_agent_form() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.form(
            rx.el.input(
                name="name",
                aria_label="New agent name",
                placeholder="Agent name",
                required=True,
                **INPUT,
            ),
            rx.el.input(
                name="prompt",
                aria_label="New agent prompt",
                placeholder="Prompt",
                required=True,
                **INPUT,
            ),
            rx.button(rx.icon("plus", size=16), "Add agent", type="submit", variant="outline"),
            on_submit=Editor.add_agent,
            reset_on_submit=True,
            aria_label="New agent",
            display="grid",
            grid_template_columns=rx.breakpoints(initial="1fr", md="1fr 1fr auto"),
            gap="10px",
        ),
    )


def _connection_form() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.form(
            rx.el.select(
                rx.el.option("Select destination community", value=""),
                rx.foreach(
                    Editor.connection_targets,
                    lambda item: rx.el.option(item["name"].to(str), value=item["id"].to(str)),
                ),
                name="target",
                required=True,
                aria_label="Destination community",
                **INPUT,
            ),
            rx.button(rx.icon("plus", size=16), "Add connection", type="submit", variant="outline"),
            on_submit=Editor.add_community_connection,
            aria_label="New community connection",
            display="grid",
            grid_template_columns=rx.breakpoints(initial="1fr", md="1fr auto"),
            gap="10px",
        ),
    )


def _handover_form() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.form(
            rx.el.select(
                rx.el.option("Select handover target", value=""),
                rx.foreach(
                    Editor.relationship_destinations,
                    lambda item: rx.el.option(item["name"], value=item["id"]),
                ),
                name="target",
                required=True,
                aria_label="Handover target",
                **INPUT,
            ),
            rx.button(rx.icon("plus", size=16), "Add handover", type="submit", variant="outline"),
            on_submit=Editor.add_relationship,
            aria_label="New handover",
            display="grid",
            grid_template_columns=rx.breakpoints(initial="1fr", md="1fr auto"),
            gap="10px",
        ),
    )


def settings_panel() -> rx.Component:
    """The selected node's settings, below the graph; nothing without a selection."""
    agent: ObjectVar[dict[str, Any]] = Editor.selected_agent_row
    community: ObjectVar[dict[str, Any]] = Editor.selected_community_row
    default: ObjectVar[dict[str, Any]] = Editor.selected_default
    return cast(
        rx.Component,
        rx.match(
            Editor.selection_kind,
            (
                "agent",
                rx.el.section(
                    rx.heading("Agent settings", size="5"),
                    agent_row(agent),
                    rx.heading("Handovers", size="4"),
                    rx.cond(
                        ~agent["protected"].to(bool),
                        rx.vstack(
                            rx.foreach(Editor.selected_handovers, relationship_row),
                            _handover_form(),
                            align="stretch",
                            width="100%",
                        ),
                        rx.text(
                            "Default communities have no handovers.", color=COLORS["text-muted"]
                        ),
                    ),
                    rx.heading("Tools", size="4"),
                    rx.cond(
                        ~agent["protected"].to(bool),
                        assignment_group(Editor.selected_assignment),
                        rx.text(
                            "Pre-defined agents cannot receive tools.", color=COLORS["text-muted"]
                        ),
                    ),
                    data_testid="agent-settings",
                    aria_label="Agent settings",
                    display="flex",
                    flex_direction="column",
                    gap="14px",
                    width="100%",
                ),
            ),
            (
                "community",
                rx.el.section(
                    rx.heading("Community settings", size="5"),
                    community_row(community),
                    rx.heading("Connections", size="4"),
                    rx.el.ul(
                        rx.foreach(Editor.community_connection_rows, community_connection_row),
                        padding="0",
                        margin="0",
                        list_style_type="none",
                    ),
                    _connection_form(),
                    rx.heading("Add an agent", size="4"),
                    _add_agent_form(),
                    data_testid="community-settings",
                    aria_label="Community settings",
                    display="flex",
                    flex_direction="column",
                    gap="14px",
                    width="100%",
                ),
            ),
            (
                "default",
                rx.el.section(
                    rx.heading("Default community", size="5"),
                    rx.text(default["name"].to(str), weight="bold"),
                    rx.text("Entry agent: " + default["entry"].to(str)),
                    rx.cond(default["note"].to(str) != "", rx.text(default["note"].to(str))),
                    rx.text(
                        "\U0001f512 Default communities are fixed: they cannot be renamed, "
                        "deleted, changed or given agents.",
                        color=COLORS["text-muted"],
                    ),
                    data_testid="default-settings",
                    aria_label="Default community settings",
                    display="flex",
                    flex_direction="column",
                    gap="10px",
                    width="100%",
                ),
            ),
            rx.fragment(),
        ),
    )


def _publish_bar() -> rx.Component:
    return cast(
        rx.Component,
        rx.flex(
            rx.heading("Community and Agent", size="7"),
            rx.spacer(),
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
                    rx.alert_dialog.title("Discard unpublished changes?"),
                    rx.alert_dialog.description("Reload the latest published revision."),
                    rx.flex(
                        rx.alert_dialog.cancel(rx.button("Cancel", variant="soft")),
                        rx.alert_dialog.action(rx.button("Reload", on_click=Editor.load)),
                        justify="end",
                        gap="10px",
                        margin_top="20px",
                    ),
                ),
            ),
            rx.alert_dialog.root(
                rx.alert_dialog.trigger(
                    rx.button(
                        rx.icon("upload", size=16),
                        "Publish",
                        disabled=Editor.busy | ~Editor.dirty,
                        min_height="38px",
                    )
                ),
                rx.alert_dialog.content(
                    rx.alert_dialog.title("Publish changes?"),
                    rx.el.ul(
                        rx.foreach(Editor.publish_summary, lambda line: rx.el.li(line)),
                        data_testid="publish-summary",
                    ),
                    rx.flex(
                        rx.alert_dialog.cancel(rx.button("Cancel", variant="soft")),
                        rx.alert_dialog.action(rx.button("Publish now", on_click=Editor.save)),
                        justify="end",
                        gap="10px",
                        margin_top="20px",
                    ),
                ),
            ),
            align="center",
            wrap="wrap",
            gap="14px",
        ),
    )


def _messages() -> rx.Component:
    return cast(
        rx.Component,
        rx.box(
            rx.cond(
                Editor.notice_revision > 0,
                rx.hstack(
                    rx.text(
                        "Another manager published revision "
                        + cast(rx.Var[int], Editor.notice_revision).to(str)
                        + ". Your draft is unchanged."
                    ),
                    rx.button("Reload", on_click=Editor.load, variant="outline"),
                    role="status",
                    data_testid="revision-notice",
                    padding="10px 12px",
                    background=COLORS["warning-fill"],
                    border_radius="6px",
                    align="center",
                ),
            ),
            rx.cond(
                (Editor.error != "") & Editor.authorized,
                rx.el.div(
                    Editor.error,
                    role="alert",
                    color=COLORS["danger"],
                    data_testid="editor-error",
                ),
            ),
            rx.cond(
                Editor.selection_error != "",
                rx.el.div(Editor.selection_error, role="alert", color=COLORS["danger"]),
            ),
            rx.foreach(
                Editor.graph_errors,
                lambda line: rx.el.div(line, role="alert", color=COLORS["danger"]),
            ),
            rx.el.span(
                Editor.status, role="status", color=COLORS["brand"], data_testid="editor-status"
            ),
            min_height="28px",
            padding="8px 0",
            display="flex",
            flex_direction="column",
            gap="8px",
        ),
    )


def _add_community_form() -> rx.Component:
    return cast(
        rx.Component,
        rx.el.details(
            rx.el.summary("Add a community", cursor="pointer", font_weight="600"),
            rx.el.form(
                *[
                    rx.el.input(
                        name=name, aria_label=label, placeholder=label, required=True, **INPUT
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
                reset_on_submit=True,
                aria_label="New community",
                display="grid",
                grid_template_columns=rx.breakpoints(initial="1fr", md="repeat(2, minmax(0, 1fr))"),
                gap="10px",
                margin="12px 0",
            ),
            width="100%",
        ),
    )


def community_agent_content() -> rx.Component:
    return cast(
        rx.Component,
        rx.box(
            rx.el.style(
                NODE_CSS
                + ".caller-outline-button { background: none; border: none; padding: 6px 4px;"
                " min-height: 32px; text-align: left; cursor: pointer; color: inherit;"
                " font: inherit; }"
                ".caller-outline-button[aria-pressed='true'] { color: #125a4e;"
                " text-decoration: underline; font-weight: 700; }"
            ),
            ConfigurationLeaveGuard.create(dirty=Editor.dirty),
            Interval.create(seconds=NOTICE_SECONDS, on_tick=Editor.check_revision),
            _publish_bar(),
            page_content(
                loading=cast(rx.Var[bool], Editor.loading),
                error=rx.cond(Editor.authorized, "", Editor.error),
                on_retry=Editor.load,
                content=rx.cond(
                    Editor.authorized,
                    rx.vstack(
                        _messages(),
                        ConfigurationGraph.create(
                            nodes=Editor.graph_nodes,
                            edges=Editor.graph_edges,
                            actor=Editor.actor_ref,
                            dirty=Editor.dirty,
                            guard_leave=False,
                            on_link=Editor.link,
                            on_pick=Editor.pick,
                        ),
                        outline(),
                        settings_panel(),
                        _add_community_form(),
                        tools(),
                        width="100%",
                        align="stretch",
                        spacing="6",
                    ),
                ),
            ),
            max_width="1200px",
            width="100%",
        ),
    )


def community_agent_page() -> rx.Component:
    return frame("communities", community_agent_content)
