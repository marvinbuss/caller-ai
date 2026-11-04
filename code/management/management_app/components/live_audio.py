"""Reflex-owned browser media bridge; raw content stays in bounded browser memory.

`LiveAudio` is a Reflex component without markup of its own: it loads the engine from
`live_audio_js`, binds it to the test console controls once the user is authorized, and sends
only encoded control snapshots and begin events back to Reflex. Audio and transcript content never
leave the browser except over the relay socket.
"""

from typing import Any

import reflex as rx
from reflex_base.utils.imports import ImportDict, ImportVar

from management_app.components.live_audio_js import (
    BUDGET_CODE,
    ENGINE_CODE,
    HOOK_CODE,
    WORKLET_SOURCE_CODE,
)


def control_args(encoded: rx.Var[str]) -> tuple[rx.Var[str]]:
    return (encoded,)


class LiveAudio(rx.Component):
    tag = "CallerLiveAudio"
    authorized: rx.Var[bool]
    on_begin: rx.EventHandler
    on_control: rx.EventHandler

    def add_imports(self) -> ImportDict:
        return {"react": [ImportVar(tag="useEffect"), ImportVar(tag="useRef")]}

    def add_custom_code(self) -> list[str]:
        return [BUDGET_CODE, WORKLET_SOURCE_CODE, ENGINE_CODE, HOOK_CODE]

    @classmethod
    def get_event_triggers(cls) -> dict[str, Any]:
        return super().get_event_triggers() | {"on_begin": control_args, "on_control": control_args}
