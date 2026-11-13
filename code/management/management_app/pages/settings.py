"""Settings page at `/settings` (editor role), inside the authenticated frame.

Voice, text and service settings grouped by topic. The voice, the reply length limit, the idle
timeout and the voice and text instructions, platform rules and global prompts are editable; they
have their own draft and publish on top of the active revision. Changed platform rules or voice
instructions publish only after the confirmation dialog.
"""

from typing import Any, cast

import reflex as rx
from reflex.event import EventHandler

from management_app.components.configuration_graph import Interval
from management_app.components.frame import frame, page_content
from management_app.pages.community_agent import NOTICE_SECONDS
from management_app.state.settings import IDLE_RANGE, SettingsState
from management_app.theme import COLORS

SET_TEXT = cast(EventHandler, SettingsState.set_text)
RESTORE_TEXT = cast(EventHandler, SettingsState.restore_text)


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


def _text(field: str, label: str, help_text: str) -> rx.Component:
    """One editable modality text: byte counter, default marker and Restore default."""
    texts = cast(Any, SettingsState.texts)
    return cast(
        rx.Component,
        rx.vstack(
            rx.hstack(
                rx.el.label(label, html_for=f"settings-{field}", font_weight="500"),
                rx.cond(
                    cast(Any, SettingsState.defaulted)[field],
                    rx.badge("Default", color_scheme="gray"),
                ),
                align="center",
                spacing="2",
            ),
            rx.debounce_input(
                rx.el.textarea(
                    id=f"settings-{field}",
                    value=texts[field].to(str),
                    on_change=SET_TEXT(field),
                    read_only=SettingsState.read_only,
                    rows=6,
                    aria_describedby=f"settings-{field}-help settings-{field}-bytes",
                    class_name="caller-input",
                    width="100%",
                    resize="vertical",
                ),
                debounce_timeout=200,
                force_notify_on_blur=True,
            ),
            rx.hstack(
                rx.text(
                    help_text, id=f"settings-{field}-help", size="2", color=COLORS["text-muted"]
                ),
                rx.spacer(),
                rx.text(
                    cast(Any, SettingsState.text_bytes)[field].to(str),
                    id=f"settings-{field}-bytes",
                    size="2",
                    color=COLORS["text-muted"],
                    data_testid=f"settings-bytes-{field}",
                ),
                rx.button(
                    "Restore default",
                    type="button",
                    variant="soft",
                    size="1",
                    aria_label=f"Restore default {label.lower()}",
                    on_click=RESTORE_TEXT(field),
                    disabled=SettingsState.read_only,
                ),
                align="center",
                width="100%",
                wrap="wrap",
            ),
            _field_error(field),
            spacing="1",
            width="100%",
        ),
    )


def _confirmation() -> rx.Component:
    return cast(
        rx.Component,
        rx.alert_dialog.root(
            rx.alert_dialog.content(
                rx.alert_dialog.title("Publish changed platform rules?"),
                rx.alert_dialog.description(
                    "This can break voice or text behavior. Publish anyway?"
                ),
                rx.flex(
                    rx.alert_dialog.cancel(
                        rx.button(
                            "Cancel", variant="soft", on_click=SettingsState.cancel_confirmation
                        )
                    ),
                    rx.alert_dialog.action(
                        rx.button(
                            "Publish anyway",
                            color_scheme="red",
                            on_click=SettingsState.publish_confirmed,
                        )
                    ),
                    justify="end",
                    gap="10px",
                    margin_top="20px",
                ),
            ),
            open=SettingsState.confirm_open,
        ),
    )


def settings_content() -> rx.Component:
    return cast(
        rx.Component,
        rx.vstack(
            rx.heading("Settings", size="7"),
            Interval.create(seconds=NOTICE_SECONDS, on_tick=SettingsState.check_revision),
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
                            SettingsState.notice_revision > 0,
                            rx.hstack(
                                rx.text(
                                    "Another manager published revision "
                                    + cast(rx.Var[int], SettingsState.notice_revision).to(str)
                                    + ". Your settings are unchanged."
                                ),
                                rx.button(
                                    "Reload", on_click=SettingsState.reload, variant="outline"
                                ),
                                role="status",
                                data_testid="settings-revision-notice",
                                padding="10px 12px",
                                background=COLORS["warning-fill"],
                                border_radius="6px",
                                align="center",
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
                                aria_describedby="settings-voice-help",
                                class_name="caller-input",
                            ),
                            rx.text(
                                "The voice spoken replies use in voice conversations.",
                                id="settings-voice-help",
                                size="2",
                                color=COLORS["text-muted"],
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
                            _text(
                                "voiceInstructions",
                                "Voice conversation instructions",
                                "Guide the model that speaks with the caller. It never sees the "
                                "agents' instructions.",
                            ),
                            _text(
                                "voicePlatformRules",
                                "Voice platform rules",
                                "Added first to every agent's instructions in voice conversations.",
                            ),
                            _text(
                                "voiceGlobalPrompt",
                                "Voice global prompt",
                                "Added after the platform rules for every agent in voice "
                                "conversations.",
                            ),
                            rx.hstack(
                                rx.el.label(
                                    rx.el.input(
                                        type="checkbox",
                                        role="switch",
                                        id="settings-quietHandover",
                                        checked=SettingsState.quiet_handover,
                                        on_change=SettingsState.set_quiet_handover,
                                        disabled=SettingsState.read_only,
                                        aria_describedby="settings-quietHandover-help",
                                    ),
                                    "Quiet update on handover",
                                    display="flex",
                                    align_items="center",
                                    gap="8px",
                                    font_weight="500",
                                ),
                                rx.cond(
                                    SettingsState.quiet_default,
                                    rx.badge("Default", color_scheme="gray"),
                                ),
                                rx.spacer(),
                                rx.button(
                                    "Restore default",
                                    type="button",
                                    variant="soft",
                                    size="1",
                                    aria_label="Restore default quiet update on handover",
                                    on_click=SettingsState.restore_quiet_handover,
                                    disabled=SettingsState.read_only,
                                ),
                                align="center",
                                width="100%",
                            ),
                            rx.text(
                                "When on, the speaking model gets a short silent note that "
                                "the work started each time it hands a request to the agents.",
                                id="settings-quietHandover-help",
                                size="2",
                                color=COLORS["text-muted"],
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
                            _text(
                                "textPlatformRules",
                                "Text platform rules",
                                "Added first to every agent's instructions in text conversations.",
                            ),
                            _text(
                                "textGlobalPrompt",
                                "Text global prompt",
                                "Added after the platform rules for every agent in text "
                                "conversations.",
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
                        _confirmation(),
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
