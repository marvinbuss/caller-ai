"""SDK policy is tested in fresh processes because tracing configuration is global."""

import subprocess
import sys

import pytest
from pydantic import ValidationError

from caller_runtime.settings import Settings


@pytest.mark.parametrize("enabled", [False, True])
def test_sdk_policy_and_metadata_only_opt_in(enabled: bool) -> None:
    source = f"""
import json
import os
from caller_runtime.settings import Settings
from caller_runtime.observability import Telemetry
from caller_runtime.adapters.agents_sdk import initialize_sdk
telemetry = Telemetry("caller-runtime", "local")
settings = Settings(agents_sdk_tracing_enabled={enabled!r})
initialize_sdk(settings, telemetry)
assert os.environ["OPENAI_AGENTS_DISABLE_TRACING"] == {("0" if enabled else "1")!r}
import agents
needle = "sensitive-content-must-not-export"
with agents.trace(needle, metadata={{"prompt": needle}}):
    with agents.custom_span(needle, data={{"audio": needle, "tool_payload": needle}}):
        pass
spans = telemetry.spans
assert bool(spans) is {enabled!r}
assert needle not in json.dumps([dict(span.attributes or {{}}) for span in spans])
try:
    initialize_sdk(Settings(agents_sdk_tracing_enabled={not enabled!r}), telemetry)
except RuntimeError as error:
    assert str(error) == "tracing_change_requires_restart"
else:
    raise AssertionError("Process-wide tracing changed without restart")
telemetry.close()
print("SDK policy verified")
"""
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SDK policy verified" in result.stdout


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
