"""One registry of pinned configured SDK agents for text and client-delegated speech."""

from collections.abc import Callable
from typing import Any

from agents import Agent, FunctionTool, ModelSettings
from agents.models.interface import Model
from agents.retry import ModelRetrySettings

from caller_runtime.domain.configuration import RevisionSnapshot

Document = dict[str, Any]


def build_agents(
    snapshot: RevisionSnapshot,
    *,
    model: Model,
    capabilities: Callable[[Document], list[FunctionTool]],
    policy_tools: Callable[[Document], list[FunctionTool]],
) -> dict[str, Agent[Any]]:
    if not isinstance(model, Model) or capabilities is None or policy_tools is None:
        raise ValueError("explicit_configured_agent_ports_required")
    result: dict[str, Agent[Any]] = {}
    for configured in snapshot.for_runtime()["agents"]:
        tools = list(policy_tools(configured))
        if configured["role"] == "ordinary":
            tools.extend(capabilities(configured))
        if (
            len(tools) > 128
            or any(not isinstance(tool, FunctionTool) for tool in tools)
            or len({tool.name for tool in tools}) != len(tools)
        ):
            raise PermissionError("unapproved_agent_tools")
        result[configured["agentId"]] = Agent(
            name=configured["name"],
            instructions=configured["prompt"],
            model=model,
            tools=list(tools),
            handoffs=[],
            mcp_servers=[],
            model_settings=ModelSettings(
                store=False, parallel_tool_calls=False, retry=ModelRetrySettings(max_retries=0)
            ),
        )
    return result
