"""Startup-only SDK policy with no default exporter or provider tracing."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from threading import Lock
from typing import TYPE_CHECKING, Any

from caller_runtime.settings import Settings

if TYPE_CHECKING:
    from agents import Tool
    from agents.realtime import RealtimeSession
    from agents.realtime.config import RealtimeRunConfig
    from agents.realtime.model import RealtimeModel

    from caller_runtime.adapters.sdk_tools import SdkTools
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
        agents.set_trace_processors([MetadataProcessor()] if policy[0] else [])
        if policy[0]:
            os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "0"
            agents.set_tracing_disabled(False)
        _policy = policy


def realtime_options(settings: Settings) -> RealtimeRunConfig:
    settings.validate_startup()
    return {"tracing_disabled": True, "model_settings": {"tracing": None}}


class AgentsSdkAdapter:
    """Fresh session boundary over an explicit transport; no default live connection."""

    def __init__(
        self,
        *,
        settings: Settings,
        telemetry: Telemetry,
        model_factory: Callable[[], RealtimeModel],
        tool_factory: SdkTools | None = None,
    ) -> None:
        settings.validate_startup()
        initialize_sdk(settings, telemetry)
        self._settings = settings
        self._telemetry = telemetry
        self._model_factory = model_factory
        self._tool_factory = tool_factory

    @asynccontextmanager
    async def open_session(
        self,
        *,
        agent_projection: Mapping[str, Any],
        context: Any,
        transfer_context: Mapping[str, str] | None = None,
        preparation_timeout: float | None = None,
    ) -> AsyncIterator[RealtimeSession]:
        from agents.realtime import RealtimeAgent, RealtimeRunner
        from agents.realtime.model import RealtimeModel

        if preparation_timeout is not None and not 0 < preparation_timeout <= 10:
            raise ValueError("invalid_preparation_timeout")
        deadline = (
            asyncio.get_running_loop().time() + preparation_timeout
            if preparation_timeout is not None
            else None
        )
        allowed = {
            "agentId",
            "name",
            "prompt",
            "enabled",
            "role",
            "communityId",
            "capabilityAssignments",
        }
        if set(agent_projection) - allowed or (
            agent_projection.get("capabilityAssignments") and self._tool_factory is None
        ):
            raise PermissionError("unapproved_agent_projection")
        if agent_projection.get("enabled") is not True:
            raise PermissionError("agent_disabled")
        identifier = agent_projection.get("agentId")
        role = agent_projection.get("role")
        community = agent_projection.get("communityId")
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 128:
            raise ValueError("invalid_agent_projection")
        if role not in {"ordinary", "welcome", "routing", "goodbye"}:
            raise ValueError("invalid_agent_projection")
        if role == "ordinary":
            if not isinstance(community, str) or not community.strip() or len(community) > 128:
                raise ValueError("invalid_agent_projection")
        elif community is not None:
            raise ValueError("invalid_agent_projection")
        name, prompt = agent_projection.get("name"), agent_projection.get("prompt")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(prompt, str)
            or not prompt.strip()
        ):
            raise ValueError("invalid_agent_projection")
        if transfer_context is not None:
            if set(transfer_context) - {"request_summary", "transfer_reason"}:
                raise PermissionError("unapproved_transfer_field")
            if any(
                not isinstance(value, str) or len(value.encode("utf-8")) > 4096
                for value in transfer_context.values()
            ):
                raise ValueError("invalid_transfer_context")
        tools: list[Tool] = []
        if self._tool_factory is not None:
            tools.extend(self._tool_factory.tools_for(agent_projection, context))
        agent = RealtimeAgent(name=name, instructions=prompt, tools=tools, handoffs=[])
        model = self._model_factory()
        if not isinstance(model, RealtimeModel):
            raise TypeError("explicit_realtime_model_required")
        runner = RealtimeRunner(agent, model=model, config=realtime_options(self._settings))
        session = await runner.run(
            context=context,
            model_config={
                "initial_model_settings": {"tracing": None},
            },
        )
        entered = False
        prepared = False
        try:
            with self._telemetry.span("sdk.session"):
                await session.__aenter__()
                entered = True
                if transfer_context:
                    await session.send_message(json.dumps(dict(transfer_context), sort_keys=True))
            prepared = True
            yield session
        finally:
            if prepared:
                await session.__aexit__(None, None, None)
            else:
                async with asyncio.timeout_at(deadline):
                    if entered:
                        await session.__aexit__(None, None, None)
                    else:
                        await model.close()
