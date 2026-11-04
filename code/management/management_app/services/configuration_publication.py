"""Pure policy that turns a validated graph into a revision, a pointer and the runtime projection.

`publication` runs the validator, numbers the new revision after the previous one and returns the
documents to commit together; any validation issue raises `ConfigurationRejected` with metadata-only
issues. `runtime_projection` drops layout and credential references and adds a policy hash, so the
backend receives only what it needs and can check it. No I/O happens here.
"""

import copy
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from management_app.models.configuration import ConfigurationGraph, ConfigurationRevision
from management_app.models.permissions import AUTHORITY_LIMIT, PermissionAuthority
from management_app.services.configuration_validation import (
    BATCH_LIMIT,
    GRAPH_FIELDS,
    SNAPSHOT_LIMIT,
    ConfigurationValidator,
    encoded,
)
from management_app.services.secret_detection import Issue

Document = dict[str, Any]


class ConfigurationRejected(ValueError):
    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("configuration_rejected")
        self.issues = copy.deepcopy(issues)


def runtime_projection(snapshot: Document) -> Document:
    """Project an integrity-verified revision without claiming to recompute its full hash."""
    policy = {
        key: copy.deepcopy(value)
        for key, value in snapshot.items()
        if key in GRAPH_FIELDS and key != "layout"
    }
    for connection in policy["mcpConnections"]:
        connection.pop("credentialRef")
    provenance = {
        key: snapshot[key]
        for key in ("id", "organizationId", "revisionNumber", "createdAt", "createdBy", "graphHash")
    }
    return provenance | policy | {"policyHash": hashlib.sha256(encoded(policy)).hexdigest()}


def publication(
    validator: ConfigurationValidator,
    payload: Document,
    *,
    actor: str,
    previous: Document | None,
    authority: Document | None,
) -> tuple[Document, Document, Document]:
    errors = validator.validate(
        payload,
        previous_revision=previous,
        authority_bytes=len(encoded(authority)) if authority else 0,
    )
    if errors:
        raise ConfigurationRejected(errors)
    graph = ConfigurationGraph.model_validate(payload).model_dump(mode="json", by_alias=True)
    now = datetime.now(UTC)
    number = previous["revisionNumber"] + 1 if previous else 1
    snapshot = ConfigurationRevision.model_validate(
        {
            **graph,
            "id": "revision-" + uuid4().hex,
            "organizationId": "default",
            "revisionNumber": number,
            "createdAt": now,
            "createdBy": actor,
            "graphHash": hashlib.sha256(encoded(graph)).hexdigest(),
        }
    ).model_dump(mode="json", by_alias=True)
    pointer = {
        "id": "active",
        "organizationId": "default",
        "revisionId": snapshot["id"],
        "revisionNumber": number,
        "publishedAt": now.isoformat(),
    }
    metadata = {"_rid", "_self", "_attachments", "_ts", "_etag"}
    old_authority = {key: value for key, value in (authority or {}).items() if key not in metadata}
    old_tools = {tool["toolId"] for tool in (previous or {}).get("tools", [])}
    old_connections = {item["connectionId"] for item in (previous or {}).get("mcpConnections", [])}
    enabled_tools = {tool["toolId"] for tool in graph["tools"] if tool["enabled"]}
    enabled_connections = {
        item["connectionId"] for item in graph["mcpConnections"] if item["enabled"]
    }
    disabled_tools = set(old_authority.get("disabledToolIds", [])) | (old_tools - enabled_tools)
    disabled_tools.update(tool["toolId"] for tool in graph["tools"] if not tool["enabled"])
    disabled_connections = set(old_authority.get("disabledConnectionIds", [])) | (
        old_connections - enabled_connections
    )
    disabled_connections.update(
        item["connectionId"] for item in graph["mcpConnections"] if not item["enabled"]
    )
    revoked = {
        (item["agentId"], item["toolId"], item["actionName"])
        for item in old_authority.get("revokedAssignments", [])
    }
    current_assignments = {
        encoded(assignment)
        for agent in graph["agents"]
        for assignment in agent["capabilityAssignments"]
    }
    for agent in (previous or {}).get("agents", []):
        for assignment in agent["capabilityAssignments"]:
            if encoded(assignment) not in current_assignments:
                revoked.update(
                    (assignment["agentId"], assignment["toolId"], action)
                    for action in assignment["permittedActionNames"]
                )
    permission = {
        "id": "security-authority",
        "organizationId": "default",
        "policyVersion": old_authority.get("policyVersion", 1),
        "disabledToolIds": sorted(disabled_tools),
        "disabledConnectionIds": sorted(disabled_connections),
        "revokedAssignments": [
            {"agentId": agent, "toolId": tool, "actionName": action}
            for agent, tool, action in sorted(revoked)
        ],
        "admissionSequence": old_authority.get("admissionSequence", 0),
        "updatedAt": now.isoformat(),
        "updatedBy": actor,
    }
    policy_fields = ("disabledToolIds", "disabledConnectionIds", "revokedAssignments")
    if authority and any(permission[field] != old_authority[field] for field in policy_fields):
        permission["policyVersion"] += 1
    permission = PermissionAuthority.model_validate_json(encoded(permission)).model_dump(
        mode="json", by_alias=True
    )
    if len(encoded(snapshot)) > SNAPSHOT_LIMIT or len(encoded(permission)) > AUTHORITY_LIMIT:
        raise ConfigurationRejected(
            [validator.detector.issue("configuration", "configuration_too_large", "snapshot_bytes")]
        )
    if len(encoded([snapshot, pointer, permission])) + 4096 >= BATCH_LIMIT:
        raise ConfigurationRejected(
            [validator.detector.issue("configuration", "publication_too_large", "batch_bytes")]
        )
    return snapshot, pointer, permission
