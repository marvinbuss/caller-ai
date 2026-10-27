"""Real SDK function tools with trusted immutable bindings and gateway-only effects."""

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from agents import FunctionTool, RunContextWrapper


class GatewayPort(Protocol):
    async def execute(self, document: dict[str, Any], *, deadline: float) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ToolBinding:
    session_id: str
    agent_id: str
    agent_epoch: int
    revision_id: str

    def __post_init__(self) -> None:
        if (
            type(self.agent_epoch) is not int
            or self.agent_epoch < 1
            or any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value)
                for value in (self.session_id, self.agent_id, self.revision_id)
            )
        ):
            raise ValueError("invalid_tool_binding")


class SdkTools:
    def __init__(
        self,
        *,
        client: GatewayPort,
        binding_for: Callable[[str, str, str], ToolBinding],
        is_current: Callable[[ToolBinding], Awaitable[bool]],
        catalog: list[dict[str, Any]],
    ) -> None:
        self._client, self._binding_for, self._is_current = client, binding_for, is_current
        self._catalog = json.dumps(catalog, allow_nan=False).encode("utf-8")
        if len(self._catalog) > 512 * 1024:
            raise ValueError("oversized_tool_catalog")

    def tools_for(self, projection: Mapping[str, Any], context: Any) -> list[FunctionTool]:
        if not projection.get("capabilityAssignments"):
            return []
        try:
            if (
                projection["role"] != "ordinary"
                or projection["enabled"] is not True
                or not isinstance(context, Mapping)
            ):
                raise ValueError("invalid_projection")
            binding = self._binding_for(
                context["session_id"], projection["agentId"], context["revision_id"]
            )
            if (
                binding.session_id != context["session_id"]
                or binding.agent_id != projection["agentId"]
                or binding.revision_id != context["revision_id"]
            ):
                raise ValueError("foreign_binding")
            catalog = json.loads(self._catalog)
            tools = []
            seen = set()
            for grant in projection["capabilityAssignments"]:
                if grant["agentId"] != binding.agent_id:
                    raise ValueError("foreign_assignment")
                for action in grant["permittedActionNames"]:
                    key = grant["toolId"], action
                    matching = [
                        item for item in catalog if (item["toolId"], item["actionId"]) == key
                    ]
                    if key in seen or len(matching) != 1:
                        raise ValueError("unreviewed_tool")
                    seen.add(key)
                    tools.append(self._tool(matching[0], binding))
            if len(tools) > 128:
                raise ValueError("oversized_tools")
            return tools
        except (KeyError, TypeError, ValueError):
            raise PermissionError("unapproved_agent_tools") from None

    def _tool(self, descriptor: dict[str, Any], binding: ToolBinding) -> FunctionTool:
        async def invoke(context: RunContextWrapper[Any], arguments: str) -> str:
            try:
                deadline = asyncio.get_running_loop().time() + 10
                async with asyncio.timeout_at(deadline):
                    if len(arguments.encode("utf-8")) > 64 * 1024 or not await self._is_current(
                        binding
                    ):
                        raise ValueError("obsolete_tool_epoch")
                    inputs = json.loads(arguments)
                    if not isinstance(inputs, dict):
                        raise ValueError("invalid_tool_input")
                    document = {
                        "requestId": uuid4().hex,
                        "sessionId": binding.session_id,
                        "agentId": binding.agent_id,
                        "agentEpoch": binding.agent_epoch,
                        "pinnedRevisionId": binding.revision_id,
                        "toolId": descriptor["toolId"],
                        "actionId": descriptor["actionId"],
                        "inputs": inputs,
                        "deadlineAt": (
                            datetime.now(UTC)
                            + timedelta(
                                seconds=max(0.0, deadline - asyncio.get_running_loop().time())
                            )
                        ).isoformat(),
                    }
                    result = await self._client.execute(document, deadline=deadline)
                    if not await self._is_current(binding) or any(
                        result[key] != document[key]
                        for key in ("requestId", "sessionId", "agentId", "agentEpoch")
                    ):
                        raise ValueError("obsolete_tool_result")
                    return json.dumps(result["result"], allow_nan=False)
            except Exception:
                return '{"error":"capability_unavailable"}'

        name = (
            "public_"
            + hashlib.sha256(
                (descriptor["toolId"] + ":" + descriptor["actionId"]).encode()
            ).hexdigest()[:32]
        )
        return FunctionTool(
            name=name,
            description=descriptor["description"],
            params_json_schema=descriptor["inputSchema"],
            on_invoke_tool=invoke,
            strict_json_schema=False,
        )
