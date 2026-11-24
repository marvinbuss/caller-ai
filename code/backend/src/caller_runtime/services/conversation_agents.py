"""Builds the SDK agents of one pinned revision, shared by text and client-delegated speech.

Input: a revision snapshot, the model, two tool factories and an optional handoff factory. Output:
agents keyed by id. Agents get only the SDK handoffs the factory builds from `handover_rules` (one
per permitted destination, feature 007), no MCP servers and no stored model state, and retries are
off, so every step the model takes is an explicit tool call or handoff that application code
authorizes. More than 128 tools and handoffs, duplicate names or non-function tools raise
`unapproved_agent_tools`.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from agents import Agent, FunctionTool, Handoff, ModelSettings, RunContextWrapper
from agents.models.interface import Model
from agents.retry import ModelRetrySettings
from openai.types.shared import Reasoning

from caller_runtime.domain.configuration import RevisionSnapshot, compose_instructions
from caller_runtime.services.handover_rules import Destination
from caller_runtime.settings import ReasoningEffort

Document = dict[str, Any]
Mode = Literal["text", "speech"]
HandoffFactory = Callable[[Document, dict[str, Agent[Any]]], list[Handoff[Any, Any]]]


def agent_instructions(configured: Document, mode: Mode, graph: Document | None = None) -> str:
    """Compose a working agent's instructions for one modality from the pinned projection:
    platform rules, global prompt, prompt, addition. Missing layers fall back to the shipped
    platform rules in `modality_defaults.py`."""
    return compose_instructions(graph or {}, configured, mode)


def handoff_schema(destination: Destination) -> Document:
    """The handoff's arguments: none, or the Routing target named from a closed list."""
    properties: Document = {}
    if destination.community_choices:
        properties["community_id"] = {
            "type": "string",
            "enum": list(destination.community_choices),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def destination_handoff(
    destination: Destination,
    target: Agent[Any],
    *,
    enabled: bool,
    invoke: Callable[[str | None], Awaitable[None]],
) -> Handoff[Any, Any]:
    """One SDK handoff named `handover_<n>`; `invoke` applies it or raises the refusal."""

    async def on_invoke_handoff(context: RunContextWrapper[Any], arguments: str | None) -> Any:
        await invoke(arguments)
        return target

    return Handoff(
        tool_name=destination.name,
        tool_description=destination.description,
        input_json_schema=handoff_schema(destination),
        on_invoke_handoff=on_invoke_handoff,
        agent_name=target.name,
        strict_json_schema=True,
        is_enabled=enabled,
    )


def build_agents(
    snapshot: RevisionSnapshot,
    *,
    model: Model,
    mode: Mode,
    capabilities: Callable[[Document], list[FunctionTool]],
    policy_tools: Callable[[Document], list[FunctionTool]],
    reasoning_effort: ReasoningEffort | None = None,
    handoffs: HandoffFactory | None = None,
) -> dict[str, Agent[Any]]:
    if not isinstance(model, Model) or capabilities is None or policy_tools is None:
        raise ValueError("explicit_configured_agent_ports_required")
    result: dict[str, Agent[Any]] = {}
    graph = snapshot.for_runtime()
    for configured in graph["agents"]:
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
            instructions=agent_instructions(configured, mode, graph),
            model=model,
            tools=list(tools),
            handoffs=[],
            mcp_servers=[],
            model_settings=ModelSettings(
                store=False,
                parallel_tool_calls=False,
                retry=ModelRetrySettings(max_retries=0),
                # None keeps the deployment default (CALLER_REASONING_EFFORT sets it, R12).
                reasoning=None if reasoning_effort is None else Reasoning(effort=reasoning_effort),
            ),
        )
    if handoffs is not None:
        for configured in graph["agents"]:
            agent = result[configured["agentId"]]
            offered = handoffs(configured, result)
            names = [tool.name for tool in agent.tools if isinstance(tool, FunctionTool)]
            names += [item.tool_name for item in offered]
            if (
                any(not isinstance(item, Handoff) for item in offered)
                or len(names) > 128
                or len(set(names)) != len(names)
            ):
                raise PermissionError("unapproved_agent_tools")
            agent.handoffs = list(offered)
    return result
