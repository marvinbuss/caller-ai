"""Pure pinned/current/reviewed intersection; no network or cached permission authority."""

import ipaddress
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from management_app.models.capabilities import ApprovedCapabilities
from management_app.models.configuration import ConfigurationGraph
from management_app.models.permissions import CapabilityAttempt, PermissionAuthority
from management_app.services.configuration_validation import GRAPH_FIELDS, encoded, invalid_schema
from management_app.services.secret_detection import SecretDetector


class CapabilityInputRejected(PermissionError):
    """Input violates an approved schema; values must not be reflected in errors."""


def public_destination(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or parts.query
            or parts.port not in {None, 443}
            or "\\" in value
            or any(ord(character) < 33 for character in value)
        ):
            raise ValueError("unsafe_destination")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            normalized = host.lower().rstrip(".")
            if "." not in normalized or normalized.endswith((".localhost", ".local", ".internal")):
                raise ValueError("unsafe_destination") from None
            host.encode("idna")
        else:
            if not address.is_global:
                raise ValueError("unsafe_destination")
        return value
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("invalid_capability_destination") from None


@dataclass(frozen=True, slots=True)
class AuthorizedCapability:
    tool_id: str
    action_id: str
    destination: str
    connection_id: str | None
    credential_ref: str | None = field(repr=False)
    output_allowlist: tuple[str, ...]
    _inputs: bytes = field(repr=False)

    @property
    def inputs(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self._inputs)
        return result


class CapabilityPolicy:
    def __init__(
        self,
        *,
        reviewed: ApprovedCapabilities,
        destinations: Mapping[str, str],
        detector: SecretDetector | None = None,
    ) -> None:
        self._reviewed = reviewed
        self._destinations = {key: public_destination(value) for key, value in destinations.items()}
        self._detector = detector

    def authorize(
        self,
        request: CapabilityAttempt,
        *,
        pinned: dict[str, Any],
        current: dict[str, Any],
        authority: PermissionAuthority,
    ) -> AuthorizedCapability:
        try:
            if (
                pinned["id"] != request.pinned_revision_id
                or request.tool_id in authority.disabled_tool_ids
            ):
                raise ValueError("denied")
            if any(
                item.agent_id == request.agent_id
                and item.tool_id == request.tool_id
                and item.action_name == request.action_id
                for item in authority.revoked_assignments
            ):
                raise ValueError("denied")
            schemas: list[dict[str, Any]] = []
            selected = None
            for snapshot in (pinned, current):
                graph = ConfigurationGraph.model_validate(
                    {key: value for key, value in snapshot.items() if key in GRAPH_FIELDS}
                )
                agent = next(agent for agent in graph.agents if agent.agent_id == request.agent_id)
                if (
                    agent.role != "ordinary"
                    or not agent.enabled
                    or not any(
                        community.community_id == agent.community_id and community.enabled
                        for community in graph.communities
                    )
                ):
                    raise ValueError("denied")
                grant = next(
                    item
                    for item in agent.capability_assignments
                    if item.tool_id == request.tool_id
                    and item.agent_id == request.agent_id
                    and request.action_id in item.permitted_action_names
                )
                tool = next(tool for tool in graph.tools if tool.tool_id == request.tool_id)
                if (
                    not tool.enabled
                    or not self._reviewed.permits_tool(tool)
                    or request.action_id not in self._reviewed.actions(tool.tool_id)
                ):
                    raise ValueError("denied")
                schemas.extend((tool.input_schema, grant.allowed_input_constraints))
                connection = None
                output = set(tool.output_allowlist)
                destination_ref = tool.approved_destination_ref
                if tool.mcp_connection_id is not None:
                    connection = next(
                        item
                        for item in graph.mcp_connections
                        if item.connection_id == tool.mcp_connection_id
                    )
                    if (
                        not connection.enabled
                        or connection.connection_id in authority.disabled_connection_ids
                        or not self._reviewed.permits_connection(connection)
                    ):
                        raise ValueError("denied")
                    action = next(
                        item
                        for item in connection.reviewed_capability_catalog
                        if item.action_name == request.action_id
                    )
                    schemas.append(action.input_schema)
                    output &= set(action.output_allowlist)
                    destination_ref = connection.approved_endpoint_ref
                candidate = AuthorizedCapability(
                    tool.tool_id,
                    request.action_id,
                    self._destinations[destination_ref],
                    connection.connection_id if connection else None,
                    connection.credential_ref if connection else None,
                    tuple(sorted(output)),
                    encoded(request.inputs),
                )
                if selected is not None and (
                    selected.destination != candidate.destination
                    or selected.connection_id != candidate.connection_id
                ):
                    raise ValueError("denied")
                selected = (
                    candidate
                    if selected is None
                    else AuthorizedCapability(
                        candidate.tool_id,
                        candidate.action_id,
                        candidate.destination,
                        candidate.connection_id,
                        candidate.credential_ref,
                        tuple(sorted(set(selected.output_allowlist) & output)),
                        candidate._inputs,
                    )
                )
            if (
                selected is None
                or not selected.output_allowlist
                or len(selected._inputs) > 64 * 1024
            ):
                raise ValueError("denied")
            for schema in schemas:
                if invalid_schema(schema):
                    raise ValueError("denied")
                if not Draft202012Validator(schema).is_valid(request.inputs):
                    raise CapabilityInputRejected("invalid_capability_input")
            return selected
        except CapabilityInputRejected:
            raise
        except (ValueError, KeyError, TypeError, StopIteration, RecursionError):
            raise PermissionError("capability_not_allowed") from None

    def filter_result(
        self, selected: AuthorizedCapability, result: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self._detector is None or not isinstance(result, Mapping):
            raise PermissionError("unsafe_capability_result")
        filtered = {key: result[key] for key in selected.output_allowlist if key in result}
        if any(
            not (
                value is None
                or isinstance(value, (str, bool, int))
                or isinstance(value, float)
                and math.isfinite(value)
            )
            for value in filtered.values()
        ):
            raise PermissionError("unsafe_capability_result")
        try:
            if len(encoded(filtered)) > 64 * 1024 or self._detector.scan(filtered):
                raise ValueError("unsafe_result")
        except (ValueError, TypeError, RecursionError):
            raise PermissionError("unsafe_capability_result") from None
        return filtered
