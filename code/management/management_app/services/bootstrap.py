"""Explicit idempotent protected-role initialization, never automatic foundation I/O."""

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from management_app.adapters.configuration_repository import Document, Publication, graph_document
from management_app.models.configuration import ConfigurationGraph


class BootstrapRepository(Protocol):
    async def initialize(self, payload: Document, *, actor_id: str) -> Publication: ...


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    revision_id: str
    etag: str
    configuration_ready: bool


def protected_graph() -> Document:
    roles = ("welcome", "routing", "goodbye")
    identifiers = {
        role: "agent-" + uuid5(NAMESPACE_URL, "caller-ai/default/protected/" + role).hex
        for role in roles
    }
    prompts = {
        "welcome": "Introduce the service and continue to Routing.",
        "routing": "Select an enabled business community entry from approved routing metadata.",
        "goodbye": "Give a concise farewell and end the call.",
    }
    return {
        "schemaVersion": "2.0.0",
        "agents": [
            {
                "agentId": identifiers[role],
                "name": role.title(),
                "prompt": prompts[role],
                "enabled": True,
                "role": role,
                "communityId": None,
                "capabilityAssignments": [],
            }
            for role in roles
        ],
        "communities": [],
        "tools": [],
        "mcpConnections": [],
        "relationships": [],
        "communityConnections": [],
        "layout": {"communities": [], "agentsByCommunity": {}},
    }


async def bootstrap(repository: BootstrapRepository) -> BootstrapResult:
    snapshot, etag = await repository.initialize(protected_graph(), actor_id="system-bootstrap")
    graph = ConfigurationGraph.model_validate(graph_document(snapshot))
    return BootstrapResult(
        revision_id=snapshot["id"],
        etag=etag,
        configuration_ready=any(community.enabled for community in graph.communities),
    )
