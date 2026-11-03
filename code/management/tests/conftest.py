"""Isolated application configuration and owned local browser-server fixtures."""

import importlib.util
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep Playwright's session greenlet after ordinary asyncio.run-based checks."""
    items.sort(key=lambda item: item.get_closest_marker("browser") is not None)


@pytest.fixture(name="validator")
def configuration_validator():
    from management_app.models.capabilities import ApprovedCapabilities
    from management_app.services.configuration_validation import ConfigurationValidator

    return ConfigurationValidator(
        policy=json.loads(
            (
                Path(__file__).resolve().parents[2] / "contracts/secret-detection-policy.json"
            ).read_text()
        ),
        approved_secret_aliases=frozenset({"fixture-connection"}),
        approved_destination_refs=frozenset({"fixture-public-endpoint"}),
        reviewed=ApprovedCapabilities.from_reviewed(capability_graph_document()),
    )


def configuration_graph_document() -> dict:
    members = [
        ("agent-welcome", "welcome", None),
        ("agent-routing", "routing", None),
        ("agent-goodbye", "goodbye", None),
        ("agent-alpha", "ordinary", "community-alpha"),
        ("agent-beta", "ordinary", "community-beta"),
    ]
    routes = [
        ("agent-welcome", "agent-routing"),
        ("agent-routing", "agent-alpha"),
        ("agent-routing", "agent-beta"),
        ("agent-alpha", "agent-routing"),
        ("agent-beta", "agent-routing"),
    ]
    routes += [
        (identifier, "agent-goodbye")
        for identifier, _, _ in members
        if identifier != "agent-goodbye"
    ]
    return {
        "schemaVersion": "1.0.0",
        "agents": [
            {
                "agentId": identifier,
                "name": identifier,
                "prompt": "Synthetic approved prompt",
                "enabled": True,
                "role": role,
                "communityId": community,
                "capabilityAssignments": [],
            }
            for identifier, role, community in members
        ],
        "communities": [
            {
                "communityId": f"community-{name}",
                "name": name,
                "routingDescription": f"Synthetic {name} routing",
                "enabled": True,
                "entryAgentId": f"agent-{name}",
            }
            for name in ("alpha", "beta")
        ],
        "relationships": [
            {
                "relationshipId": f"path-{index}",
                "sourceAgentId": source,
                "targetAgentId": target,
                "allowedContextFields": ["request_summary", "transfer_reason"],
            }
            for index, (source, target) in enumerate(routes)
        ],
        "tools": [],
        "mcpConnections": [],
    }


@pytest.fixture
def configuration_graph() -> dict:
    return configuration_graph_document()


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith(("CALLER_", "AZURE_", "OPENAI_")) and name != "CALLER_UI_URL":
            monkeypatch.delenv(name)


def capability_graph_document() -> dict:
    configuration_graph = configuration_graph_document()
    configuration_graph["tools"] = [
        {
            "toolId": "tool-public",
            "name": "Public lookup",
            "description": "Synthetic public lookup",
            "schemaVersion": "1.0.0",
            "inputSchema": {"type": "object", "additionalProperties": False},
            "outputAllowlist": ["name"],
            "enabled": True,
            "approvedDestinationRef": "fixture-public-endpoint",
            "mcpConnectionId": "connection-one",
            "effect": "read_only",
            "classification": "public",
        }
    ]
    configuration_graph["mcpConnections"] = [
        {
            "connectionId": "connection-one",
            "name": "Reviewed connection",
            "approvedEndpointRef": "fixture-public-endpoint",
            "enabled": True,
            "credentialRef": "secret://fixture-connection",
            "reviewedCapabilityCatalog": [
                {
                    "actionName": "lookup",
                    "inputSchema": {"type": "object"},
                    "outputAllowlist": ["name"],
                    "effect": "read_only",
                    "classification": "public",
                }
            ],
        }
    ]
    configuration_graph["agents"][3]["capabilityAssignments"] = [
        {
            "agentId": "agent-alpha",
            "toolId": "tool-public",
            "permittedActionNames": ["lookup"],
            "allowedInputConstraints": {},
        }
    ]
    return configuration_graph


@pytest.fixture
def capability_graph() -> dict:
    return capability_graph_document()


@pytest.fixture(scope="session")
def shell_url() -> Iterator[str]:
    existing = os.environ.get("CALLER_UI_URL")
    if existing:
        yield existing
        return
    path = Path(__file__).resolve().parents[2] / "tools/scripts/smoke.py"
    specification = importlib.util.spec_from_file_location("caller_smoke", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    with module.running_apps() as urls:
        yield urls["ui"]
