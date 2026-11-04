"""One-time Agents SDK tracing policy; no default exporter and no provider-side tracing.

Input: the startup `Settings` and the service `Telemetry`. Output: process-wide SDK state. Tracing
is off unless explicitly enabled, and then only fixed metadata spans reach `Telemetry`, never
payloads. The SDK's tracing switches are global, so a second call with a different policy raises
`tracing_change_requires_restart` instead of silently changing a running process.
"""

from __future__ import annotations

import os
from threading import Lock
from typing import TYPE_CHECKING, Any

from caller_runtime.settings import Settings

if TYPE_CHECKING:
    from caller_runtime.observability import Telemetry

_policy: tuple[bool, bool] | None = None
_lock = Lock()


def initialize_sdk(settings: Settings, telemetry: Telemetry) -> None:
    global _policy
    policy = (settings.agents_sdk_tracing_enabled, settings.realtime_tracing_enabled)
    with _lock:
        if _policy is not None:
            if _policy != policy:
                raise RuntimeError("tracing_change_requires_restart")
            return

        os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
        import agents
        from agents.tracing import Span, Trace, TracingProcessor

        class MetadataProcessor(TracingProcessor):
            def on_trace_start(self, trace: Trace) -> None:
                return None

            def on_trace_end(self, trace: Trace) -> None:
                with telemetry.span("sdk.trace"):
                    pass

            def on_span_start(self, span: Span[Any]) -> None:
                return None

            def on_span_end(self, span: Span[Any]) -> None:
                with telemetry.span("sdk.span"):
                    pass

            def shutdown(self) -> None:
                return None

            def force_flush(self) -> None:
                return None

        agents.set_tracing_disabled(True)
        # Replacing the processor list also removes the SDK's default exporter to the provider.
        agents.set_trace_processors([MetadataProcessor()] if policy[0] else [])
        if policy[0]:
            os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "0"
            agents.set_tracing_disabled(False)
        _policy = policy
