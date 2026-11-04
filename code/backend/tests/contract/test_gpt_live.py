"""Foundry Live client-delegation events, not Realtime or Responses envelopes."""

import base64
import json

import pytest


@pytest.fixture
def protocol():
    from caller_runtime.adapters.gpt_live import GptLiveProtocol

    return GptLiveProtocol(instructions="Synthetic approved voice instructions", voice="marin")


def started(protocol):
    event = protocol.start_event()
    return {"type": "session.started", "session": {"id": "sess-fixture", **event["session"]}}


def test_start_is_exact_foundry_client_delegation_without_provider_reasoning(protocol):
    assert protocol.start_event() == {
        "type": "session.start",
        "session": {
            "model": "gpt-live-1",
            "instructions": "Synthetic approved voice instructions",
            "audio": {"output": {"voice": "marin"}},
            "delegation": {"type": "client"},
        },
    }
    protocol.accept(json.dumps(started(protocol)))
    assert protocol.provider_session_id == "sess-fixture"


@pytest.mark.parametrize(
    "corruption", ["model", "delegation", "voice", "instructions", "duplicate-start"]
)
def test_readiness_rejects_changed_provider_configuration(protocol, corruption):
    event = started(protocol)
    if corruption == "model":
        event["session"]["model"] = "legacy-realtime"
    elif corruption == "delegation":
        event["session"]["delegation"] = {"type": "responses"}
    elif corruption == "voice":
        event["session"]["audio"]["output"]["voice"] = "unapproved-voice"
    elif corruption == "instructions":
        event["session"]["instructions"] = "Synthetic changed instructions"
    else:
        protocol.accept(json.dumps(event))
    with pytest.raises(ValueError, match="invalid_live_event"):
        protocol.accept(json.dumps(event))


def test_timed_pcm_and_transcript_fragments_keep_distinct_source_events(protocol):
    protocol.accept(json.dumps(started(protocol)))
    pcm = b"\x01\x00" * 480
    audio = protocol.accept(
        json.dumps(
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(pcm).decode(),
                "start_ms": 0,
                "end_ms": 20,
            }
        )
    )
    assert audio["pcm"] == pcm and audio["start_ms"] == 0 and audio["end_ms"] == 20
    fragment = protocol.accept(
        json.dumps(
            {
                "type": "session.input_transcript.delta",
                "delta": "Synthetic fragment",
                "start_ms": 600,
                "end_ms": 800,
            }
        )
    )
    assert fragment["delta"] == "Synthetic fragment"
    assert fragment["type"] == "session.input_transcript.delta" and "task" not in fragment


def test_client_delegation_is_metadata_only_and_append_keeps_exact_id(protocol):
    protocol.accept(json.dumps(started(protocol)))
    event = protocol.accept(
        json.dumps(
            {
                "type": "session.delegation.created",
                "offset_ms": 1000,
                "delegation": {"id": "item-fixture", "type": "delegation", "target": "client"},
            }
        )
    )
    assert event["delegation"]["id"] == "item-fixture" and "task" not in event
    appended = protocol.context_event("item-fixture", "Synthetic reviewed result")
    assert appended == {
        "type": "session.commentary.append",
        "delegation_id": "item-fixture",
        "content": "Synthetic reviewed result",
    }
    assert (
        protocol.context_event("item-fixture", "Quiet facts", quiet=True)["type"]
        == "session.thinking.append"
    )
    assert protocol.context_event("item-fixture", "\u00e9" * 250)["content"] == "\u00e9" * 250
    with pytest.raises(ValueError):
        protocol.context_event("item-fixture", "\u00e9" * 251)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "response.event", "event": {"type": "response.completed"}},
        {"type": "response.create"},
        {
            "type": "session.delegation.created",
            "offset_ms": 0,
            "delegation": {"id": "item-fixture", "type": "delegation", "target": "responses"},
        },
    ],
)
def test_responses_and_legacy_events_never_become_client_work(protocol, event):
    protocol.accept(json.dumps(started(protocol)))
    with pytest.raises(ValueError, match="invalid_live_event"):
        protocol.accept(json.dumps(event))


def test_usage_snapshots_are_not_summed_and_close_uses_exact_session(protocol):
    protocol.accept(json.dumps(started(protocol)))
    for seconds in (12, 12, 10, 20):
        protocol.accept(
            json.dumps({"type": "session.usage.updated", "usage": {"seconds": seconds}})
        )
    assert protocol.voice_seconds == 20
    closed = protocol.accept(
        json.dumps(
            {"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 21}}
        )
    )
    assert protocol.voice_seconds == 21 and closed["provider_session_id"] == "sess-fixture"
    assert closed["reason"] == "close_requested" and protocol.closed
    with pytest.raises(ValueError, match="invalid_live_event"):
        protocol.accept(json.dumps({"type": "session.usage.updated", "usage": {"seconds": 22}}))


@pytest.mark.parametrize(
    "raw",
    [
        " " * (256 * 1024 + 1),
        '{"type":"session.closed","type":"session.started"}',
        '{"type":"session.usage.updated","usage":{"seconds":NaN}}',
        json.dumps(
            {"type": "session.output_audio.delta", "delta": "AQ==", "start_ms": 0, "end_ms": 20}
        ),
    ],
    ids=["oversized", "duplicate-key", "nonfinite", "odd-pcm"],
)
def test_invalid_oversized_or_odd_pcm_events_reject_without_raw_details(protocol, raw):
    protocol.accept(json.dumps(started(protocol)))
    with pytest.raises(ValueError, match="^invalid_live_event$"):
        protocol.accept(raw)


def serve_live(handler=None, *, process_request=None):
    import contextlib

    from websockets.asyncio.server import serve

    @contextlib.asynccontextmanager
    async def running():
        async def default(connection):
            await connection.send(json.dumps({"type": "session.started"}))
            async for _message in connection:
                pass

        async with serve(
            handler or default, "127.0.0.1", 0, process_request=process_request
        ) as server:
            yield server.sockets[0].getsockname()[1]

    return running()


def test_sdk_socket_connects_on_live_path_with_bearer_and_enforces_frame_limit():
    import asyncio

    from websockets.exceptions import ConnectionClosedError

    from caller_runtime.adapters.gpt_live import EVENT_LIMIT, sdk_socket

    seen = {}

    def inspect(connection, request):
        seen["path"] = request.path
        seen["authorization"] = request.headers.get("Authorization")

    async def handler(connection):
        await connection.send(json.dumps({"type": "session.started"}))
        await connection.send("x" * (EVENT_LIMIT + 1))
        await connection.wait_closed()

    async def scenario():
        async with serve_live(handler, process_request=inspect) as port:
            socket = await sdk_socket(
                f"http://127.0.0.1:{port}/openai/v1",
                token="synthetic-token",
                max_size=EVENT_LIMIT,
                compression=None,
                proxy=None,
            )
            assert json.loads(await socket.recv()) == {"type": "session.started"}
            with pytest.raises(ConnectionClosedError):
                await socket.recv()
            await socket.close()

    asyncio.run(scenario())
    assert seen == {
        "path": "/openai/v1/live/sessions",
        "authorization": "Bearer synthetic-token",
    }


def test_sdk_socket_refuses_cross_origin_redirects_so_the_token_stays_on_the_origin():
    import asyncio

    from websockets.datastructures import Headers
    from websockets.exceptions import SecurityError
    from websockets.http11 import Response

    from caller_runtime.adapters.gpt_live import sdk_socket

    async def scenario():
        async with serve_live() as target:

            def redirect(connection, request):
                location = f"ws://localhost:{target}/openai/v1/live/sessions"
                return Response(302, "Found", Headers([("Location", location)]), b"")

            async with serve_live(process_request=redirect) as origin:
                with pytest.raises(SecurityError):
                    await sdk_socket(
                        f"http://127.0.0.1:{origin}/openai/v1",
                        token="synthetic-token",
                        proxy=None,
                    )

    asyncio.run(scenario())
