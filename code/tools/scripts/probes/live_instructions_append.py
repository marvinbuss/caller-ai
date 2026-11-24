"""Probe P1: does `session.instructions.append` interrupt GPT-Live speech in progress?

Each run starts a `gpt-live-1` session with client delegation and the shipped voice conversation
instructions, feeds silent caller audio like an open microphone, has the model speak a long
synthetic commentary, and sends a synthetic delegation update 1.5 s into the speech. A run counts
as cut when the spoken transcript covers less than 40 % of the commentary; `--control` runs, which
send no update, speak about 60 % because the model paraphrases. The transcript can run ahead of
the audio, so its timing alone does not show a cut. The summary decides the default of
`CALLER_VOICE_DELEGATION_UPDATES` (R5): true only if no run was cut; any failed run leaves it
undecided.
"""

import asyncio
import base64
import json
import sys
import time
from collections.abc import Sequence
from typing import Any

import common

PROBE = "P1"
SPEECH_BEFORE_UPDATE_SECONDS = 1.5
SPOKEN_RATIO_KEPT = 0.4
QUIET_END_SECONDS = 4.0
RUN_LIMIT_SECONDS = 45.0
# 40 ms of silent 24 kHz mono PCM, the runtime's largest audio frame.
SILENCE = base64.b64encode(bytes(1920)).decode("ascii")
CALLS = (
    "session.start",
    "session.commentary.append",
    "session.instructions.append",
    "session.close",
)


def start_event(instructions: str, voice: str) -> dict[str, Any]:
    return {
        "type": "session.start",
        "session": {
            "model": "gpt-live-1",
            "instructions": instructions,
            "audio": {"output": {"voice": voice}},
            "delegation": {"type": "client"},
        },
    }


def commentary_event() -> dict[str, Any]:
    return {
        "type": "session.commentary.append",
        "delegation_id": None,
        "content": common.LONG_COMMENTARY,
    }


def update_event(run: int) -> dict[str, Any]:
    return {
        "type": "session.instructions.append",
        "delegation_id": None,
        "content": common.DELEGATION_UPDATE,
        "event_id": f"probe-update-{run}",
    }


def shipped_instructions() -> str:
    """The instructions the runtime starts GPT-Live with when nothing is published."""
    from caller_runtime.composition import LIVE_DELEGATION_SUFFIX
    from caller_runtime.services.modality_defaults import VOICE_INSTRUCTIONS

    return VOICE_INSTRUCTIONS + LIVE_DELEGATION_SUFFIX


def judge(
    update_at: float,
    output_times: Sequence[float],
    spoken_characters: int,
    audio_seconds: float,
    acknowledged: bool,
) -> dict[str, Any]:
    """Classify one run; only lengths and timings are kept, never the transcript."""
    ratio = spoken_characters / len(common.LONG_COMMENTARY)
    after = [moment - update_at for moment in output_times if moment > update_at]
    return {
        "cut": ratio < SPOKEN_RATIO_KEPT,
        "acknowledged": acknowledged,
        "spoken_ratio": round(ratio, 2),
        "audio_seconds": round(audio_seconds, 1),
        "transcript_after_update_seconds": round(max(after), 2) if after else 0.0,
    }


def provider_error(event: dict[str, Any]) -> RuntimeError:
    """The provider's error code when it is a fixed code; its message is never kept."""
    detail = event.get("error")
    code = detail.get("code") if isinstance(detail, dict) else None
    name = "live_error." + code.lower() if isinstance(code, str) else ""
    return RuntimeError(name if common.CODE.fullmatch(name) else "live_provider_error")


async def _receive(connection: Any, seconds: float) -> dict[str, Any] | None:
    try:
        async with asyncio.timeout(seconds):
            raw = await connection.recv_bytes()
    except TimeoutError:
        return None
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise RuntimeError("live_provider_error")
    if event.get("type") == "error":
        raise provider_error(event)
    return event


async def silence(connection: Any) -> None:
    """Send silent caller audio, like the open microphone of a real voice test."""
    event = json.dumps({"type": "session.input_audio.append", "audio": SILENCE})
    while True:
        await connection.send_raw(event)
        await asyncio.sleep(0.04)


async def run_once(
    settings: common.ProbeSettings, credential: Any, run: int, control: bool
) -> dict[str, Any]:
    from openai import AsyncOpenAI

    token = await credential.get_token(common.TOKEN_SCOPE)
    client = AsyncOpenAI(
        api_key=token.token, base_url=settings.live_endpoint + "/openai/v1", max_retries=0
    )
    try:
        connection = await client.live.connect().enter()
        try:
            await connection.send_raw(
                json.dumps(start_event(shipped_instructions(), settings.live_voice))
            )
            started = await _receive(connection, 10)
            if started is None or started.get("type") != "session.started":
                raise RuntimeError("live_not_started")
            microphone = asyncio.create_task(silence(connection))
            try:
                return await speak_and_update(connection, run, control)
            finally:
                microphone.cancel()
        finally:
            await connection.close()
    finally:
        await client.close()


async def speak_and_update(connection: Any, run: int, control: bool) -> dict[str, Any]:
    await connection.send_raw(json.dumps(commentary_event()))
    output_times: list[float] = []
    spoken_characters = 0
    audio_bytes = 0
    update_at: float | None = None
    acknowledged = False
    begun = time.monotonic()
    while time.monotonic() - begun < RUN_LIMIT_SECONDS:
        now = time.monotonic()
        if update_at is None and output_times:
            wait = output_times[0] + SPEECH_BEFORE_UPDATE_SECONDS - now
            if wait <= 0:
                if not control:
                    await connection.send_raw(json.dumps(update_event(run)))
                update_at = time.monotonic()
                continue
        elif update_at is None:
            wait = 10.0
        else:
            last = max([update_at, *output_times])
            wait = last + QUIET_END_SECONDS - now
            if wait <= 0:
                break
        event = await _receive(connection, max(0.05, wait))
        if event is None:
            if update_at is None and not output_times:
                raise RuntimeError("live_speech_not_started")
            continue
        if event.get("type") == "session.output_transcript.delta":
            output_times.append(time.monotonic())
            spoken_characters += len(str(event.get("delta", "")))
        elif event.get("type") == "session.output_audio.delta":
            audio_bytes += len(str(event.get("delta", ""))) * 3 // 4
        elif event.get("type") == "session.instructions.appended":
            acknowledged = event.get("client_event_id") == f"probe-update-{run}"
    if update_at is None:
        raise RuntimeError("live_update_not_sent")
    await connection.send_raw(json.dumps({"type": "session.close"}))
    for _ in range(20):
        closed = await _receive(connection, 2)
        if closed is None or closed.get("type") == "session.closed":
            break
    return judge(update_at, output_times, spoken_characters, audio_bytes / 48000, acknowledged)


async def run_all(runs: int, control: bool) -> int:
    settings = common.settings()
    credential = common.credential()
    cut = acknowledged = errors = 0
    try:
        for run in range(runs):
            try:
                value = await run_once(settings, credential, run, control)
            except Exception as error:
                errors += 1
                common.result(PROBE, run, "error", {"code": common.failure_code(error)})
                continue
            cut += value["cut"]
            acknowledged += value["acknowledged"]
            outcome = "failed" if value["cut"] else "passed"
            common.result(PROBE, run, outcome, value)
    finally:
        await credential.close()
    common.result(
        PROBE,
        "total",
        "summary",
        {
            "runs": runs,
            "control": control,
            "cut": cut,
            "acknowledged": acknowledged,
            "errors": errors,
            "voice_delegation_updates_default": (
                "undecided" if errors else ("true" if cut == 0 else "false")
            ),
        },
    )
    return 0 if errors == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    options = common.arguments(__doc__ or PROBE, argv, default_runs=10, control=True)
    if options.preview:
        return common.preview(PROBE, CALLS)
    return asyncio.run(run_all(options.runs, options.control))


if __name__ == "__main__":
    sys.exit(main())
