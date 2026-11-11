"""Published-configuration console; controls are Reflex, content/media are browser-volatile.

The console page renders the start, stop, mute and interrupt controls, the device pickers, the
text form and the status line for one test of the published configuration. Reflex owns the
controls and the permit request; the `LiveAudio` component owns the microphone, speaker and
relay socket in the browser. Transcripts and audio live only in browser memory; they pass through
the relay to the backend but are never kept in Reflex state or stored by the management app.
"""

from typing import cast

import reflex as rx

from management_app.components.live_audio import LiveAudio
from management_app.state.test_console import TestConsoleState as Console

CONTROL_STYLE = {
    "min_height": "40px",
    "padding": "8px 12px",
    "border": "1px solid #b8c3bd",
    "border_radius": "4px",
    "background": "#ffffff",
    "color": "#242925",
    "font_family": "inherit",
    "font_size": "14px",
    "letter_spacing": "0",
}


def icon_control(icon: str, label: str, identifier: str, *, disabled: bool = False) -> rx.Component:
    return cast(
        rx.Component,
        rx.tooltip(
            rx.el.button(
                rx.icon(icon, size=18),
                type="button",
                id=identifier,
                aria_label=label,
                title=label,
                disabled=disabled,
                width="40px",
                height="40px",
                display="inline-flex",
                align_items="center",
                justify_content="center",
                **CONTROL_STYLE,
            ),
            content=label,
        ),
    )


def device_control(label: str, identifier: str) -> rx.Component:
    return cast(
        rx.Component,
        rx.vstack(
            rx.el.label(label, html_for=identifier, font_size="14px", font_weight="600"),
            rx.el.select(
                rx.el.option("System default", value=""),
                id=identifier,
                default_value="",
                width="100%",
                max_width="100%",
                **CONTROL_STYLE,
            ),
            align="start",
            width="100%",
            min_width="0",
            spacing="2",
        ),
    )


# Shown in text mode: the backend keeps text test conversations so a follow-up can continue them.
TEXT_RETENTION_NOTICE = (
    "Text test conversations are stored for 30 days after the last message "
    "and are visible only to their owner."
)


def console_controls() -> rx.Component:
    return cast(
        rx.Component,
        rx.vstack(
            rx.flex(
                rx.vstack(
                    rx.el.label("Mode", html_for="test-mode", font_size="14px", font_weight="600"),
                    rx.el.select(
                        rx.el.option("Text", value="text"),
                        rx.el.option("Speech", value="speech"),
                        id="test-mode",
                        default_value="text",
                        width="150px",
                        **CONTROL_STYLE,
                    ),
                    spacing="2",
                    align="start",
                ),
                rx.spacer(),
                rx.el.button(
                    rx.icon("play", size=16),
                    "Start",
                    id="test-start",
                    type="button",
                    aria_label="Start published test",
                    title="Start published test",
                    display="inline-flex",
                    align_items="center",
                    gap="8px",
                    **CONTROL_STYLE,
                ),
                icon_control("square", "Stop test", "test-stop", disabled=True),
                icon_control("mic-off", "Mute input", "test-mute", disabled=True),
                icon_control("volume-x", "Interrupt output", "test-interrupt", disabled=True),
                align="end",
                wrap="wrap",
                gap="10px",
                width="100%",
            ),
            rx.el.div(
                rx.grid(
                    device_control("Microphone", "test-input-device"),
                    device_control("Output", "test-output-device"),
                    columns=rx.breakpoints(initial="1", sm="2"),
                    spacing="4",
                    width="100%",
                ),
                rx.hstack(
                    icon_control("refresh-cw", "Refresh devices", "test-device-refresh"),
                    icon_control("headphones", "Choose output device", "test-output-pick"),
                    rx.text("System default", id="test-device-status", size="2", role="status"),
                    align="center",
                    margin_top="12px",
                    wrap="wrap",
                ),
                id="test-devices",
                hidden=True,
                width="100%",
                padding_top="12px",
            ),
            rx.grid(
                *[
                    rx.el.div(
                        rx.el.dt(label, color="#58615c", font_size="13px"),
                        rx.el.dd(
                            initial,
                            id="test-" + identifier,
                            margin="4px 0 0",
                            overflow_wrap="anywhere",
                            font_size="14px",
                            font_weight="600",
                        ),
                        min_width="0",
                    )
                    for label, identifier, initial in (
                        ("Published revision", "revision", "-"),
                        ("Agent", "agent", "-"),
                        ("Community", "community", "-"),
                        ("Status", "status", "idle"),
                    )
                ],
                columns=rx.breakpoints(initial="1", sm="2", md="4"),
                spacing="4",
                width="100%",
                padding="20px 0",
                border_top="1px solid #dce1df",
                border_bottom="1px solid #dce1df",
            ),
            rx.hstack(
                rx.text("Input", size="2", color="#58615c"),
                rx.el.output("Off", id="test-capture"),
                rx.spacer(),
                rx.text("Output", size="2", color="#58615c"),
                rx.el.output("Off", id="test-playback"),
                width="100%",
                align="center",
                wrap="wrap",
                font_size="14px",
            ),
            rx.el.div(
                id="test-messages",
                role="log",
                aria_label="Conversation",
                aria_live="polite",
                width="100%",
                min_height="220px",
                max_height="480px",
                overflow_y="auto",
                overflow_wrap="anywhere",
                border_bottom="1px solid #dce1df",
                padding="12px 0",
            ),
            rx.el.div(
                id="test-error",
                role="alert",
                color="#a32831",
                font_size="14px",
                min_height="24px",
                overflow_wrap="anywhere",
            ),
            rx.el.form(
                rx.el.label("Message", html_for="test-text", font_size="14px", font_weight="600"),
                rx.el.textarea(
                    id="test-text",
                    aria_label="Message",
                    rows=3,
                    max_length=4096,
                    width="100%",
                    margin_top="8px",
                    resize="vertical",
                    **CONTROL_STYLE,
                ),
                rx.text(
                    TEXT_RETENTION_NOTICE,
                    id="test-text-notice",
                    size="2",
                    color="#58615c",
                    margin_top="8px",
                ),
                rx.flex(
                    rx.el.button(
                        rx.icon("send", size=16),
                        "Send",
                        id="test-send",
                        type="submit",
                        aria_label="Send text",
                        disabled=True,
                        display="inline-flex",
                        align_items="center",
                        gap="8px",
                        **CONTROL_STYLE,
                    ),
                    gap="12px",
                    align="center",
                    wrap="wrap",
                    margin_top="10px",
                ),
                id="test-form",
                width="100%",
            ),
            LiveAudio.create(
                authorized=Console.authorized,
                on_begin=Console.begin,
                on_control=Console.update_control,
            ),
            spacing="4",
            width="100%",
            align="stretch",
        ),
    )


def test_console_content() -> rx.Component:
    return cast(
        rx.Component,
        rx.box(
            rx.heading("Test", size="7", margin_bottom="24px"),
            rx.cond(Console.authorized, console_controls(), rx.text("Unavailable", role="status")),
            max_width="960px",
            width="100%",
        ),
    )


def test_console_page() -> rx.Component:
    from management_app.components.frame import frame

    return frame("test", test_console_content)
