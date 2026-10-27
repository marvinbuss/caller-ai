"""Real SDK wrappers and HTTP contract doubles; never live business capabilities."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from agents import RunContextWrapper
from azure.core.credentials import AccessToken

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def tools_case():
    from caller_runtime.adapters.sdk_tools import SdkTools, ToolBinding

    binding = ToolBinding(
        session_id="session-one", agent_id="agent-one", agent_epoch=3, revision_id="revision-one"
    )
    current = [True]

    class Gateway:
        def __init__(self):
            self.calls = []
            self.obsolete_on_return = False

        async def execute(self, document, *, deadline):
            self.calls.append((document, deadline))
            if self.obsolete_on_return:
                current[0] = False
            return {
                key: document[key] for key in ("requestId", "sessionId", "agentId", "agentEpoch")
            } | {"result": {"name": "Synthetic public result"}}

    async def is_current(candidate):
        return current[0] and candidate == binding

    gateway = Gateway()
    factory = SdkTools(
        client=gateway,
        binding_for=lambda session, agent, revision: binding,
        is_current=is_current,
        catalog=[
            {
                "toolId": "tool-public",
                "actionId": "lookup",
                "description": "Synthetic public lookup",
                "inputSchema": {"type": "object", "additionalProperties": False},
            }
        ],
    )
    projection = {
        "agentId": "agent-one",
        "name": "Synthetic agent",
        "prompt": "Synthetic prompt",
        "enabled": True,
        "role": "ordinary",
        "communityId": "community-one",
        "capabilityAssignments": [
            {
                "agentId": "agent-one",
                "toolId": "tool-public",
                "permittedActionNames": ["lookup"],
                "allowedInputConstraints": {},
            }
        ],
    }
    context = {"session_id": "session-one", "revision_id": "revision-one"}
    return factory, projection, context, gateway, current


async def test_real_sdk_tool_binds_call_references_and_uses_gateway_only(tools_case):
    factory, projection, context, gateway, current = tools_case
    tools = factory.tools_for(projection, context)
    assert len(tools) == 1
    result = await tools[0].on_invoke_tool(RunContextWrapper(context=context), "{}")
    assert json.loads(result) == {"name": "Synthetic public result"}
    document, deadline = gateway.calls[0]
    assert document["sessionId"] == "session-one" and document["agentId"] == "agent-one"
    assert document["agentEpoch"] == 3 and document["pinnedRevisionId"] == "revision-one"
    assert document["toolId"] == "tool-public" and document["actionId"] == "lookup"
    assert deadline > asyncio.get_running_loop().time() and document["requestId"]


async def test_real_realtime_agent_materializes_only_explicit_gateway_tools(
    tools_case, scripted_handover_models
):
    from caller_runtime.adapters.agents_sdk import AgentsSdkAdapter
    from caller_runtime.observability import Telemetry
    from caller_runtime.settings import Settings

    factory, projection, context, gateway, current = tools_case
    telemetry = Telemetry("caller-runtime", "local")
    try:
        adapter = AgentsSdkAdapter(
            settings=Settings(),
            telemetry=telemetry,
            model_factory=scripted_handover_models,
            tool_factory=factory,
        )
        async with adapter.open_session(agent_projection=projection, context=context):
            settings = scripted_handover_models.models[0].options[0]["initial_model_settings"]
            assert len(settings["tools"]) == 1 and settings["handoffs"] == []
            assert settings["tools"][0].name.startswith("public_")
            assert gateway.calls == []
    finally:
        telemetry.close()


async def test_obsolete_tool_epoch_never_dispatches_and_late_result_is_discarded(tools_case):
    factory, projection, context, gateway, current = tools_case
    tool = factory.tools_for(projection, context)[0]
    current[0] = False
    assert "error" in json.loads(
        await tool.on_invoke_tool(RunContextWrapper(context=context), "{}")
    )
    assert gateway.calls == []
    current[0] = True
    gateway.obsolete_on_return = True
    result = await tool.on_invoke_tool(RunContextWrapper(context=context), "{}")
    assert "Synthetic public result" not in result and len(gateway.calls) == 1


@pytest.mark.parametrize(
    "mutation", ["foreign-agent", "foreign-session", "protected", "unassigned-action"]
)
def test_tool_factory_rejects_foreign_or_unassigned_materialization(tools_case, mutation):
    factory, projection, context, gateway, current = tools_case
    if mutation == "foreign-agent":
        projection["agentId"] = "foreign-agent"
    elif mutation == "foreign-session":
        context["session_id"] = "foreign-session"
    elif mutation == "protected":
        projection["role"] = "routing"
    else:
        projection["capabilityAssignments"][0]["permittedActionNames"] = ["unreviewed-action"]
    with pytest.raises(PermissionError):
        factory.tools_for(projection, context)
    assert gateway.calls == []


async def test_managed_identity_client_uses_exact_scope_origin_and_bound_response():
    from caller_runtime.adapters.capabilities import CapabilityClient

    audience = str(uuid4())
    outgoing, scopes = [], []

    class Credential:
        async def get_token(self, *requested, **keywords):
            scopes.append(requested)
            return AccessToken(str(uuid4()), int(time.time()) + 300)

    def handler(request):
        outgoing.append(request)
        assert (
            str(request.url) == "https://management.example.invalid/v1/runtime/capabilities/execute"
        )
        assert request.headers["authorization"].startswith("Bearer ")
        document = json.loads(request.content)
        return httpx.Response(
            200,
            json={key: document[key] for key in ("requestId", "sessionId", "agentId", "agentEpoch")}
            | {"result": {"name": "Synthetic public result"}},
        )

    client = CapabilityClient(
        endpoint="https://management.example.invalid",
        audience=audience,
        credential=Credential(),
        transport=httpx.MockTransport(handler),
    )
    document = {
        "requestId": "request-one",
        "sessionId": "session-one",
        "agentId": "agent-one",
        "agentEpoch": 3,
        "pinnedRevisionId": "revision-one",
        "toolId": "tool-public",
        "actionId": "lookup",
        "inputs": {},
        "deadlineAt": (datetime.now(UTC) + timedelta(seconds=5)).isoformat(),
    }
    result = await client.execute(document, deadline=asyncio.get_running_loop().time() + 5)
    assert result["result"] == {"name": "Synthetic public result"}
    assert scopes == [(f"api://{audience}/.default",)] and len(outgoing) == 1


@pytest.mark.parametrize("status", [302, 401, 403, 503, 504])
async def test_gateway_client_never_follows_redirects_or_reflects_error_bodies(status):
    from caller_runtime.adapters.capabilities import CapabilityClient

    wire = []

    class Credential:
        async def get_token(self, *scopes, **keywords):
            return AccessToken(str(uuid4()), int(time.time()) + 300)

    def handler(request):
        wire.append(request)
        return httpx.Response(
            status,
            text="synthetic-private-error-payload",
            headers={"Location": "https://foreign.example.invalid/"},
        )

    client = CapabilityClient(
        endpoint="https://management.example.invalid",
        audience=str(uuid4()),
        credential=Credential(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError) as failure:
        await client.execute(
            {"requestId": "request-one"}, deadline=asyncio.get_running_loop().time() + 1
        )
    assert "synthetic-private-error-payload" not in str(failure.value) and len(wire) == 1
