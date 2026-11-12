"""Builds the SDK agents of one pinned revision, shared by text and client-delegated speech.

Input: a revision snapshot, the model and two tool factories. Output: agents keyed by id. Agents
get no handoffs, no MCP servers and no stored model state, and retries are off, so every step the
model takes is an explicit tool call that application code authorizes. More than 128 tools,
duplicate names or non-function tools raise `unapproved_agent_tools`.
"""

from collections.abc import Callable
from typing import Any, Literal

from agents import Agent, FunctionTool, ModelSettings
from agents.models.interface import Model
from agents.retry import ModelRetrySettings
from openai.types.shared import Reasoning

from caller_runtime.domain.configuration import RevisionSnapshot

Document = dict[str, Any]


def build_agents(
    snapshot: RevisionSnapshot,
    *,
    model: Model,
    capabilities: Callable[[Document], list[FunctionTool]],
    policy_tools: Callable[[Document], list[FunctionTool]],
    reasoning_effort: Literal["low"] | None = None,
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
                store=False,
                parallel_tool_calls=False,
                retry=ModelRetrySettings(max_retries=0),
                # None keeps the deployment default; speech and clarity checks ask for low.
                reasoning=None if reasoning_effort is None else Reasoning(effort=reasoning_effort),
            ),
        )
    return result
