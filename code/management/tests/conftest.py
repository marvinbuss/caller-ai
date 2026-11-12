"""Isolated application configuration and owned local browser-server fixtures."""

import copy
import importlib.util
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# CI runners are several times slower than a workstation; the assertions stay the same.
EXPECT_TIMEOUT_MS = 15000 if os.environ.get("CI") else 5000


@pytest.fixture(scope="session", autouse=True)
def browser_expectation_timeout() -> None:
    from playwright.sync_api import expect

    expect.set_options(timeout=EXPECT_TIMEOUT_MS)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep Playwright's session greenlet after ordinary asyncio.run-based checks."""
    items.sort(key=lambda item: item.get_closest_marker("browser") is not None)
    for item in items:
        parts = item.path.parts
        # The quick tier is unit and contract tests that build no frontend and start no host.
        if (
            ("unit" in parts or "contract" in parts)
            and item.get_closest_marker("browser") is None
            and "editor_host_factory" not in item.fixturenames
        ):
            item.add_marker(pytest.mark.quick)


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
    return {
        "schemaVersion": "2.1.0",
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
                "routable": True,
            }
            for name in ("alpha", "beta")
        ],
        "relationships": [],
        "communityConnections": [],
        "layout": {"communities": [], "agentsByCommunity": {}},
        "tools": [],
        "mcpConnections": [],
    }


@pytest.fixture
def configuration_graph() -> dict:
    return configuration_graph_document()


@pytest.fixture
def graph_v2(configuration_graph):
    graph = copy.deepcopy(configuration_graph)
    graph["schemaVersion"] = "2.1.0"
    graph["agents"].append(
        {**graph["agents"][3], "agentId": "agent-alpha-peer", "name": "Alpha peer"}
    )
    graph["relationships"] = [
        {
            "relationshipId": "alpha-handover",
            "sourceAgentId": "agent-alpha",
            "targetAgentId": "agent-alpha-peer",
            "allowedContextFields": ["request_summary", "transfer_reason"],
        }
    ]
    graph["communityConnections"] = [
        {
            "connectionId": "alpha-to-beta",
            "sourceCommunityId": "community-alpha",
            "targetCommunityId": "community-beta",
            "allowedContextFields": ["request_summary", "transfer_reason"],
        }
    ]
    graph["layout"] = {
        "communities": [
            {"nodeId": "community-alpha", "x": 0, "y": 0},
            {"nodeId": "community-beta", "x": 240, "y": 0},
        ],
        "agentsByCommunity": {
            "community-alpha": [
                {"nodeId": "agent-alpha", "x": 0, "y": 0},
                {"nodeId": "agent-alpha-peer", "x": 240, "y": 0},
            ]
        },
    }
    return graph


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
def editor_host_factory():
    path = Path(__file__).resolve().parents[2] / "tools/scripts/smoke.py"
    specification = importlib.util.spec_from_file_location("caller_editor_smoke", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module.running_configuration_editor


@pytest.fixture
def editor_test_app(tmp_path, monkeypatch, configuration_graph):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("__REFLEX_SKIP_COMPILE", "true")
    monkeypatch.setenv("REFLEX_WEB_WORKDIR", str(tmp_path / "web"))

    def reject_frontend_install(*arguments, **keywords):
        pytest.fail("backend_editor_fixture_must_not_prepare_frontend_packages")

    monkeypatch.setattr(
        "reflex.utils.js_runtimes.install_frontend_packages", reject_frontend_install
    )
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "mode": "local",
                "graph": configuration_graph,
                "identities": ["manager-one", "manager-two"],
                "origin": "https://editor.example.invalid",
                "port": 9443,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CALLER_EDITOR_TEST_FIXTURE", str(fixture_path))

    from reflex.config import get_config

    config = get_config()
    original_import = config.app_module_import
    name = "tests.editor_app"
    original_module = sys.modules.get(name)
    specification = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("editor_app.py")
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    try:
        specification.loader.exec_module(module)
        yield module
    finally:
        config.app_module_import = original_import
        if original_module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original_module


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
