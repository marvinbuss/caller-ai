"""Remove inherited live configuration from deterministic runtime tests."""

import copy
import hashlib
import json
import math
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def community_configuration() -> dict:
    ordinary = [
        ("a-source", "alpha", True),
        ("a-entry", "alpha", True),
        ("a-peer", "alpha", True),
        ("a-disabled", "alpha", False),
        ("b-entry", "beta", True),
        ("b-peer", "beta", True),
    ]
    agents = [
        {
            "agentId": role,
            "name": role.title(),
            "prompt": "Synthetic " + role + " prompt",
            "enabled": True,
            "role": role,
            "communityId": None,
            "capabilityAssignments": [],
        }
        for role in ("welcome", "routing", "goodbye")
    ]
    agents += [
        {
            "agentId": identifier,
            "name": identifier,
            "prompt": "Synthetic prompt for " + identifier,
            "enabled": enabled,
            "role": "ordinary",
            "communityId": community,
            "capabilityAssignments": []
            if community == "alpha"
            else [
                {
                    "agentId": identifier,
                    "toolId": "beta-tool",
                    "permittedActionNames": ["public-lookup"],
                    "allowedInputConstraints": {},
                }
            ],
        }
        for identifier, community, enabled in ordinary
    ]
    pairs = [
        ("welcome", "routing"),
        ("routing", "goodbye"),
        ("routing", "a-entry"),
        ("routing", "b-entry"),
        ("a-source", "a-entry"),
        ("a-source", "a-peer"),
        ("a-peer", "a-source"),
        ("b-entry", "b-peer"),
    ]
    pairs += [
        (identifier, target)
        for identifier, _, enabled in ordinary
        if enabled
        for target in ("routing", "goodbye")
    ]
    return {
        "revisionId": "revision-us3-fixture",
        "schemaVersion": "1.0.0",
        "agents": agents,
        "communities": [
            {
                "communityId": community,
                "name": community.title(),
                "routingDescription": "Synthetic " + community + " routing",
                "enabled": True,
                "entryAgentId": entry,
            }
            for community, entry in (("alpha", "a-entry"), ("beta", "b-entry"))
        ],
        "relationships": [
            {
                "relationshipId": f"path-{index}",
                "sourceAgentId": source,
                "targetAgentId": target,
                "allowedContextFields": ["request_summary", "transfer_reason"],
            }
            for index, (source, target) in enumerate(pairs)
        ],
        "tools": [
            {
                "toolId": "beta-tool",
                "name": "Synthetic beta tool",
                "description": "Foreign tool marker",
                "schemaVersion": "1.0.0",
                "inputSchema": {"type": "object"},
                "outputAllowlist": ["name"],
                "enabled": True,
                "approvedDestinationRef": "approved-public-fixture",
                "mcpConnectionId": None,
                "effect": "read_only",
                "classification": "public",
            }
        ],
        "mcpConnections": [],
    }


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith(("CALLER_", "AZURE_", "OPENAI_")):
            monkeypatch.delenv(name)


@pytest.fixture
def community_configuration_v2(community_configuration) -> dict:
    graph = copy.deepcopy(community_configuration)
    graph.pop("revisionId")
    graph["schemaVersion"] = "2.0.0"
    agents = {agent["agentId"]: agent for agent in graph["agents"]}
    graph["relationships"] = [
        edge
        for edge in graph["relationships"]
        if agents[edge["sourceAgentId"]]["role"] == "ordinary"
        and agents[edge["targetAgentId"]]["role"] == "ordinary"
        and agents[edge["sourceAgentId"]]["communityId"]
        == agents[edge["targetAgentId"]]["communityId"]
    ]
    graph["communityConnections"] = [
        {
            "connectionId": "alpha-to-beta",
            "sourceCommunityId": "alpha",
            "targetCommunityId": "beta",
            "allowedContextFields": ["request_summary", "transfer_reason"],
        }
    ]
    graph["layout"] = {
        "communities": [
            {"nodeId": community["communityId"], "x": index * 240, "y": 0}
            for index, community in enumerate(graph["communities"])
        ],
        "agentsByCommunity": {
            community["communityId"]: [
                {"nodeId": agent["agentId"], "x": index * 240, "y": 0}
                for index, agent in enumerate(
                    member
                    for member in graph["agents"]
                    if member["communityId"] == community["communityId"]
                )
            ]
            for community in graph["communities"]
        },
    }
    return graph


@pytest.fixture
def revision_document_v2(community_configuration_v2):
    def create(number=1):
        graph = copy.deepcopy(community_configuration_v2)
        for agent in graph["agents"]:
            if agent["agentId"] == "a-source":
                agent["prompt"] += f" revision {number}"
        body = json.dumps(
            graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        return graph | {
            "id": f"revision-browser-fixture-{number}",
            "organizationId": "default",
            "revisionNumber": number,
            "createdAt": "2026-10-30T00:00:00Z",
            "createdBy": "manager-fixture",
            "graphHash": hashlib.sha256(body).hexdigest(),
        }

    return create


@pytest.fixture
def runtime_revision_v2(revision_document_v2):
    def create(number=1):
        full = revision_document_v2(number)
        fields = {
            "schemaVersion",
            "agents",
            "communities",
            "relationships",
            "communityConnections",
            "tools",
            "mcpConnections",
        }
        policy = {key: copy.deepcopy(full[key]) for key in fields}
        for connection in policy["mcpConnections"]:
            connection.pop("credentialRef")
        body = json.dumps(
            policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        provenance = {
            key: full[key]
            for key in (
                "id",
                "organizationId",
                "revisionNumber",
                "createdAt",
                "createdBy",
                "graphHash",
            )
        }
        return provenance | policy | {"policyHash": hashlib.sha256(body).hexdigest()}

    return create


@pytest.fixture
def conversation_clock():
    class Clock:
        seconds = 0.0

        def monotonic(self):
            return self.seconds

        def utc_now(self):
            return datetime(2026, 10, 30, tzinfo=UTC) + timedelta(seconds=self.seconds)

        def advance(self, seconds):
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                raise ValueError("invalid_fixture_clock_advance")
            self.seconds += seconds

    return Clock()


@pytest.fixture
def conversation_ports():
    return SimpleNamespace(
        model=AsyncMock(side_effect=RuntimeError("fixture_model_not_configured")),
        admission=AsyncMock(side_effect=RuntimeError("fixture_admission_not_configured")),
    )


@pytest.fixture
def revision_document(runtime_revision_v2):
    def create(number=1):
        document = runtime_revision_v2(number)
        document["id"] = f"revision-fixture-{number}"
        return document

    return create


def pytest_collection_modifyitems(items):
    """Mark the fast tiers: everything under unit/ and contract/ that needs no browser."""
    for item in items:
        parts = item.path.parts
        if ("unit" in parts or "contract" in parts) and item.get_closest_marker("browser") is None:
            item.add_marker(pytest.mark.quick)
