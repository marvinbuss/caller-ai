"""Real SDK sessions over scripted models; no provider, persistence or credentials."""

import asyncio
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from agents.realtime import RealtimeAgent, RealtimeRunner, RealtimeSession
from agents.realtime.model import RealtimeModel
from agents.realtime.model_inputs import RealtimeModelSendUserInput
from pydantic import ValidationError

from caller_runtime.adapters.agents_sdk import realtime_options
from caller_runtime.observability import Telemetry
from caller_runtime.settings import Settings

SENSITIVE = "synthetic-sdk-content-that-must-not-export"
SOURCE_CONTEXT = {
    "session_id": "session-sdk-fixture",
    "revision_id": "revision-sdk-fixture",
    "conversation": [{"role": "user", "content": SENSITIVE}],
    "audio": SENSITIVE,
    "tool_payload": SENSITIVE,
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ScriptedModel(RealtimeModel):
    """Implements the pinned SDK model interface without a network transport."""

    def __init__(self, *, fail_connect: bool = False) -> None:
        self.fail_connect = fail_connect
        self.listeners = []
        self.options = []
        self.sent = []
        self.closed = 0

    async def connect(self, options) -> None:
        self.options.append(options)
        if self.fail_connect:
            raise ConnectionError("synthetic-connect-failure")

    def add_listener(self, listener) -> None:
        self.listeners.append(listener)

    def remove_listener(self, listener) -> None:
        self.listeners.remove(listener)

    async def send_event(self, event) -> None:
        self.sent.append(event)

    async def send_event_if(self, event, send_if) -> bool:
        if not send_if():
            return False
        self.sent.append(event)
        return True

    async def close(self) -> None:
        self.closed += 1


def projection(name: str) -> dict[str, object]:
    return {
        "agentId": f"{name}-fixture",
        "name": name,
        "prompt": "Synthetic approved prompt",
        "enabled": True,
        "role": "ordinary",
        "communityId": "community-sdk-fixture",
        "capabilityAssignments": [],
    }


@pytest.mark.anyio
async def test_scripted_model_exercises_real_locked_sdk_without_credentials() -> None:
    model = ScriptedModel()
    agent = RealtimeAgent(name="sdk-interface-fixture", instructions="Synthetic approved prompt")
    runner = RealtimeRunner(agent, model=model, config=realtime_options(Settings()))
    async with await runner.run(context=SOURCE_CONTEXT) as session:
        assert isinstance(session, RealtimeSession) and session.model is model
        initial = await asyncio.wait_for(anext(aiter(session)), timeout=1)
        assert initial.type == "history_updated" and initial.history == []
        assert initial.info.context.context is SOURCE_CONTEXT
        assert model.options[0]["initial_model_settings"]["tracing"] is None
        await session.send_message("synthetic-caller-input")
        assert isinstance(model.sent[-1], RealtimeModelSendUserInput)
        assert model.sent[-1].user_input == "synthetic-caller-input"
    assert model.closed == 1 and model.listeners == []


@pytest.mark.anyio
async def test_adapter_keeps_source_context_out_of_new_model_session() -> None:
    from caller_runtime.adapters.agents_sdk import AgentsSdkAdapter

    models = []

    def model_factory():
        model = ScriptedModel()
        models.append(model)
        return model

    telemetry = Telemetry("caller-runtime", "local")
    adapter = AgentsSdkAdapter(
        settings=Settings(), telemetry=telemetry, model_factory=model_factory
    )
    source_context = json.loads(json.dumps(SOURCE_CONTEXT))
    try:
        async with adapter.open_session(
            agent_projection=projection("source"), context=source_context
        ) as source:
            assert isinstance(source, RealtimeSession)
            await source.send_message(SENSITIVE)
            async with adapter.open_session(
                agent_projection=projection("destination"),
                context={
                    "session_id": source_context["session_id"],
                    "revision_id": source_context["revision_id"],
                },
                transfer_context={
                    "request_summary": "Synthetic approved summary",
                    "transfer_reason": "Synthetic approved reason",
                },
            ) as destination:
                assert isinstance(destination, RealtimeSession) and destination is not source
                assert source.model is models[0] and destination.model is models[1]
                event = await asyncio.wait_for(anext(aiter(destination)), timeout=1)
                assert event.type == "history_updated" and event.history == []
                assert event.info.context.context is not source_context
                assert SENSITIVE not in json.dumps(event.info.context.context)
                assert SENSITIVE not in json.dumps(models[1].options, default=str)
                assert SENSITIVE not in json.dumps(
                    [asdict(item) for item in models[1].sent], default=str
                )
                assert source_context == SOURCE_CONTEXT
                assert models[0].closed == 0
        assert all(model.closed == 1 and model.listeners == [] for model in models)
        assert SENSITIVE not in json.dumps(
            [dict(span.attributes or {}) for span in telemetry.spans]
        )
    finally:
        telemetry.close()


@pytest.mark.anyio
async def test_failed_destination_preparation_preserves_source_model() -> None:
    from caller_runtime.adapters.agents_sdk import AgentsSdkAdapter

    models = [ScriptedModel(), ScriptedModel(fail_connect=True)]
    pending = iter(models)
    telemetry = Telemetry("caller-runtime", "local")
    adapter = AgentsSdkAdapter(
        settings=Settings(), telemetry=telemetry, model_factory=lambda: next(pending)
    )
    try:
        async with adapter.open_session(
            agent_projection=projection("source"), context=SOURCE_CONTEXT
        ) as source:
            with pytest.raises(ConnectionError):
                async with adapter.open_session(
                    agent_projection=projection("destination"), context={}
                ):
                    pytest.fail("Failed destination model became active")
            assert models[0].closed == 0
            assert models[1].listeners == []
            await source.send_message("source-still-active")
            assert models[0].sent[-1].user_input == "source-still-active"
        assert models[0].closed == 1
    finally:
        telemetry.close()


@pytest.mark.anyio
async def test_session_exception_closes_without_durable_session_or_payload_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from caller_runtime.adapters.agents_sdk import AgentsSdkAdapter

    def forbidden_persistence(*arguments, **keywords):
        raise AssertionError("SDK call context must not open durable storage")

    monkeypatch.setattr("sqlite3.connect", forbidden_persistence)
    monkeypatch.setattr(Path, "write_text", forbidden_persistence)
    monkeypatch.setattr(Path, "write_bytes", forbidden_persistence)
    model = ScriptedModel()
    telemetry = Telemetry("caller-runtime", "local")
    adapter = AgentsSdkAdapter(
        settings=Settings(), telemetry=telemetry, model_factory=lambda: model
    )
    try:
        with pytest.raises(RuntimeError, match="synthetic-owner-error"):
            async with adapter.open_session(
                agent_projection=projection("source"), context=SOURCE_CONTEXT
            ):
                raise RuntimeError("synthetic-owner-error")
        assert model.closed == 1 and model.listeners == []
        assert SENSITIVE not in json.dumps(
            [dict(span.attributes or {}) for span in telemetry.spans]
        )
        assert all("synthetic-owner-error" not in span.to_json() for span in telemetry.spans)
    finally:
        telemetry.close()


async def tracing_probe(enabled: bool) -> None:
    import agents

    from caller_runtime.adapters.agents_sdk import AgentsSdkAdapter, initialize_sdk

    settings = Settings(agents_sdk_tracing_enabled=enabled)
    telemetry = Telemetry("caller-runtime", "local")
    initialize_sdk(settings, telemetry)
    model = ScriptedModel()
    adapter = AgentsSdkAdapter(settings=settings, telemetry=telemetry, model_factory=lambda: model)
    try:
        with agents.trace(SENSITIVE, metadata={"prompt": SENSITIVE}):
            with agents.custom_span(
                SENSITIVE, data={"audio": SENSITIVE, "tool_payload": SENSITIVE}
            ):
                async with adapter.open_session(
                    agent_projection=projection("source"), context=SOURCE_CONTEXT
                ):
                    assert model.options[0]["initial_model_settings"]["tracing"] is None
        sdk_spans = [span for span in telemetry.spans if span.name in {"sdk.trace", "sdk.span"}]
        assert bool(sdk_spans) is enabled
        assert SENSITIVE not in json.dumps(
            [dict(span.attributes or {}) for span in telemetry.spans]
        )
        assert model.closed == 1 and model.listeners == []
    finally:
        telemetry.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_fresh_process_sdk_tracing_defaults_and_safe_memory_opt_in(enabled: bool) -> None:
    source = (
        "import asyncio,runpy; "
        f"module=runpy.run_path({str(Path(__file__).resolve())!r}); "
        f"asyncio.run(module['tracing_probe']({enabled!r})); print('safe-sdk-session-probe')"
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "safe-sdk-session-probe" in result.stdout


@pytest.mark.parametrize(
    "values",
    [
        {"trace_include_content": True},
        {"agents_sdk_tracing_enabled": True, "trace_destination": "https://unapproved.invalid"},
        {"realtime_tracing_enabled": True},
    ],
)
def test_unsafe_or_unqualified_tracing_opt_ins_are_rejected(values) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes,transfer,error",
    [
        ({"tools": []}, None, PermissionError),
        ({"handoffs": []}, None, PermissionError),
        ({"capabilityAssignments": [{"toolId": "unapproved"}]}, None, PermissionError),
        ({"enabled": False}, None, PermissionError),
        ({"role": "unapproved-role"}, None, ValueError),
        ({"communityId": None}, None, ValueError),
        ({}, {"transcript": SENSITIVE}, PermissionError),
        ({}, {"request_summary": "x" * 4097}, ValueError),
    ],
)
async def test_unapproved_projection_or_transfer_never_creates_model(
    changes, transfer, error
) -> None:
    from caller_runtime.adapters.agents_sdk import AgentsSdkAdapter

    calls = []

    def model_factory():
        calls.append(True)
        return ScriptedModel()

    telemetry = Telemetry("caller-runtime", "local")
    adapter = AgentsSdkAdapter(
        settings=Settings(), telemetry=telemetry, model_factory=model_factory
    )
    try:
        with pytest.raises(error):
            async with adapter.open_session(
                agent_projection={**projection("source"), **changes},
                context={},
                transfer_context=transfer,
            ):
                pytest.fail("Unapproved agent/transfer entered a session")
        assert calls == []
    finally:
        telemetry.close()
