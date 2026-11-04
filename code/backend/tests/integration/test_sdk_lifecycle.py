"""Startup tracing policy of the real locked Agents SDK; no provider, persistence or credentials."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from caller_runtime.observability import Telemetry
from caller_runtime.settings import Settings

SENSITIVE = "synthetic-sdk-content-that-must-not-export"


async def tracing_probe(enabled: bool) -> None:
    import agents

    from caller_runtime.adapters.agents_sdk import initialize_sdk

    settings = Settings(agents_sdk_tracing_enabled=enabled)
    telemetry = Telemetry("caller-runtime", "local")
    initialize_sdk(settings, telemetry)
    assert os.environ["OPENAI_AGENTS_DISABLE_TRACING"] == ("0" if enabled else "1")
    try:
        with agents.trace(SENSITIVE, metadata={"prompt": SENSITIVE}):
            with agents.custom_span(
                SENSITIVE, data={"audio": SENSITIVE, "tool_payload": SENSITIVE}
            ):
                pass
        sdk_spans = [span for span in telemetry.spans if span.name in {"sdk.trace", "sdk.span"}]
        assert bool(sdk_spans) is enabled
        assert SENSITIVE not in json.dumps(
            [dict(span.attributes or {}) for span in telemetry.spans]
        )
        with pytest.raises(RuntimeError, match="^tracing_change_requires_restart$"):
            initialize_sdk(Settings(agents_sdk_tracing_enabled=not enabled), telemetry)
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
