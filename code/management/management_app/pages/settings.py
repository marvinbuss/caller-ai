"""Settings page at `/settings` (editor role), inside the authenticated frame.

Voice, text and service settings grouped by topic. Only the voice, the reply length limit and the
idle timeout are editable; they have their own draft and publish on top of the active revision.
"""

from typing import Any, cast

import reflex as rx

from management_app.components.frame import frame, page_content
from management_app.state.settings import IDLE_RANGE, SettingsState
from management_app.theme import COLORS


def _field_error(field: str) -> rx.Component:
    return cast(
        rx.Component,
        rx.cond(
            cast(Any, SettingsState.field_errors).contains(field),
            rx.text(
                SettingsState.field_errors[field],
                color=COLORS["danger"],
                size="2",
                role="alert",
                data_testid=f"settings-error-{field}",
            ),
        ),
    )


def _group(group: Any) -> rx.Component:
    return cast(
        rx.Component,
        rx.el.optgroup(
            rx.foreach(
                group["voices"].to(list[dict[str, str]]),
                lambda voice: rx.el.option(voice["label"], value=voice["id"]),
            ),
            label=group["language"],
        ),
    )


def _section(title: str, *children: rx.Component) -> rx.Component:
    return cast(
        rx.Component,
        rx.card(
            rx.vstack(rx.heading(title, size="4", as_="h2"), *children, spacing="3"),
            width="100%",
        ),
    )


def _number(label: str, field: str, value: Any, on_change: Any, note: Any) -> rx.Component:
    return cast(
        rx.Component,
        rx.vstack(
            rx.el.label(label, html_for=f"settings-{field}", font_weight="500"),
            rx.el.input(
                id=f"settings-{field}",
                type="text",
                input_mode="numeric",
                value=value,
                on_change=on_change,
                read_only=SettingsState.read_only,
                aria_describedby=f"settings-{field}-help",
                class_name="caller-input",
            ),
            rx.text(note, id=f"settings-{field}-help", size="2", color=COLORS["text-muted"]),
            _field_error(field),
            spacing="1",
            width="100%",
        ),
    )


def settings_content() -> rx.Component:
    return cast(
        rx.Component,
        rx.vstack(
            rx.heading("Settings", size="7"),
            rx.text(
                "Changes are published as a new revision and apply to conversations started "
                "after publication; running conversations keep their values.",
                color=COLORS["text-muted"],
            ),
            page_content(
                loading=cast(rx.Var[bool], SettingsState.loading),
                error=cast(rx.Var[str], SettingsState.error),
                on_retry=SettingsState.load,
                content=rx.cond(
                    SettingsState.error == "",
                    rx.vstack(
                        rx.cond(
                            SettingsState.read_only,
                            rx.callout(
                                "The conversation service cannot be reached, so the voices "
                                "and limits it supports are unknown. Settings are read-only "
                                "until it is reachable again.",
                                color_scheme="orange",
                                role="status",
                                data_testid="settings-read-only",
                            ),
                        ),
                        rx.cond(
                            SettingsState.conflict != "",
                            rx.hstack(
                                rx.text(SettingsState.conflict),
                                rx.button(
                                    "Reload", on_click=SettingsState.rebase, variant="outline"
                                ),
                                role="alert",
                                data_testid="settings-conflict",
                                align="center",
                            ),
                        ),
                        _section(
                            "Voice",
                            rx.el.label("Voice", html_for="settings-voice", font_weight="500"),
                            rx.el.select(
                                rx.foreach(SettingsState.groups, _group),
                                id="settings-voice",
                                value=SettingsState.voice,
                                on_change=SettingsState.set_voice,
                                disabled=SettingsState.read_only,
                                class_name="caller-input",
                            ),
                            rx.cond(
                                SettingsState.read_only,
                                rx.text("Current voice: ", SettingsState.voice),
                            ),
                            _field_error("voice"),
                            _number(
                                "Idle timeout (seconds)",
                                "idleTimeoutSeconds",
                                SettingsState.idle,
                                SettingsState.set_idle,
                                f"Integer, {IDLE_RANGE[0]} to {IDLE_RANGE[1]}. A conversation "
                                "ends after this long without the caller speaking or writing.",
                            ),
                        ),
                        _section(
                            "Text",
                            _number(
                                "Reply length limit (tokens)",
                                "maxOutputTokens",
                                SettingsState.max_tokens,
                                SettingsState.set_max_tokens,
                                SettingsState.tokens_note,
                            ),
                        ),
                        _section(
                            "Service (read-only)",
                            rx.el.dl(
                                rx.foreach(
                                    SettingsState.environment,
                                    lambda row: rx.fragment(
                                        rx.el.dt(row["label"], color=COLORS["text-muted"]),
                                        rx.el.dd(row["value"], margin="0 0 8px 0"),
                                    ),
                                ),
                                margin="0",
                                data_testid="settings-environment",
                            ),
                        ),
                        rx.hstack(
                            rx.button(
                                "Publish settings",
                                on_click=SettingsState.publish,
                                disabled=SettingsState.read_only,
                                loading=SettingsState.busy,
                            ),
                            rx.text(
                                SettingsState.message, role="status", data_testid="settings-status"
                            ),
                            align="center",
                            spacing="3",
                        ),
                        spacing="4",
                        width="100%",
                    ),
                ),
            ),
            spacing="4",
            width="100%",
            max_width="800px",
        ),
    )


def settings_page() -> rx.Component:
    return frame("settings", settings_content)
