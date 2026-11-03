"""Revision invariants and versioned, non-disclosing credential detection."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest


def test_configuration_models_roundtrip_only_declarative_graph(configuration_graph) -> None:
    from management_app.models.configuration import ConfigurationGraph

    model = ConfigurationGraph.model_validate(configuration_graph)
    assert model.model_dump(mode="json", by_alias=True) == configuration_graph
    assert model.agents[0].role == "welcome"
    assert model.tools == [] and model.mcp_connections == []


def test_configuration_models_hide_rejected_values(configuration_graph) -> None:
    from pydantic import ValidationError

    from management_app.models.configuration import ConfigurationGraph

    candidate = str(uuid4())
    configuration_graph["agents"][0]["enabled"] = candidate
    with pytest.raises(ValidationError) as failure:
        ConfigurationGraph.model_validate(configuration_graph)
    assert candidate not in str(failure.value)


def test_configuration_models_match_published_json_schema() -> None:
    from jsonschema import Draft202012Validator

    from management_app.models.configuration import ConfigurationRevision

    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "contracts/configuration.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    assert schema == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **ConfigurationRevision.model_json_schema(by_alias=True),
    }


def assert_safe(errors: list[dict], candidate: str | None = None) -> None:
    assert errors
    for error in errors:
        assert {"path", "code", "rule_id", "policy_version"} == error.keys()
        assert isinstance(error["path"], str) and error["path"]
        assert error["policy_version"] == 1
    if candidate is not None:
        assert candidate not in json.dumps(errors)


def test_complete_two_community_graph_is_valid_without_mutation(
    validator, configuration_graph
) -> None:
    before = copy.deepcopy(configuration_graph)
    assert validator.validate(configuration_graph) == []
    assert configuration_graph == before


@pytest.mark.parametrize("role", ["welcome", "routing", "goodbye"])
@pytest.mark.parametrize("mutation", ["remove", "disable", "community", "duplicate"])
def test_protected_role_cardinality_and_membership(
    validator, configuration_graph, role, mutation
) -> None:
    agents = configuration_graph["agents"]
    agent = next(item for item in agents if item["role"] == role)
    if mutation == "remove":
        agents.remove(agent)
    elif mutation == "disable":
        agent["enabled"] = False
    elif mutation == "community":
        agent["communityId"] = "community-alpha"
    else:
        agents.append({**agent, "agentId": "duplicate-protected", "name": "Duplicate"})
    assert_safe(validator.validate(configuration_graph))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-community",
        "unknown-community",
        "blank-name",
        "duplicate-name",
        "blank-prompt",
        "duplicate-id",
        "unknown-role",
    ],
)
def test_agent_invariants(validator, configuration_graph, mutation) -> None:
    agent = configuration_graph["agents"][3]
    field, value = {
        "missing-community": ("communityId", None),
        "unknown-community": ("communityId", "unknown"),
        "blank-name": ("name", "  "),
        "duplicate-name": ("name", "agent-welcome"),
        "blank-prompt": ("prompt", "\t"),
        "duplicate-id": ("agentId", "agent-welcome"),
        "unknown-role": ("role", "unrestricted"),
    }[mutation]
    agent[field] = value
    assert_safe(validator.validate(configuration_graph))


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-entry",
        "foreign-entry",
        "protected-entry",
        "disabled-entry",
        "duplicate-id",
        "blank-name",
    ],
)
def test_community_entry_and_identity_rules(validator, configuration_graph, mutation) -> None:
    community = configuration_graph["communities"][0]
    if mutation == "disabled-entry":
        configuration_graph["agents"][3]["enabled"] = False
    else:
        field, value = {
            "unknown-entry": ("entryAgentId", "missing"),
            "foreign-entry": ("entryAgentId", "agent-beta"),
            "protected-entry": ("entryAgentId", "agent-routing"),
            "duplicate-id": ("communityId", "community-beta"),
            "blank-name": ("name", " "),
        }[mutation]
        community[field] = value
    assert_safe(validator.validate(configuration_graph))


@pytest.mark.parametrize(
    "mutation", ["unknown-target", "foreign-direct", "raw-context", "missing-lifecycle"]
)
def test_relationship_references_context_and_lifecycle(
    validator, configuration_graph, mutation
) -> None:
    relationships = configuration_graph["relationships"]
    if mutation == "missing-lifecycle":
        relationships.pop(0)
    elif mutation == "foreign-direct":
        relationships.append(
            {
                "relationshipId": "forbidden-direct",
                "sourceAgentId": "agent-alpha",
                "targetAgentId": "agent-beta",
                "allowedContextFields": [],
            }
        )
    elif mutation == "unknown-target":
        relationships[0]["targetAgentId"] = "missing"
    else:
        relationships[0]["allowedContextFields"] = ["conversation"]
    assert_safe(validator.validate(configuration_graph))


@pytest.mark.parametrize(
    "collection", ["agents", "communities", "relationships", "tools", "mcpConnections"]
)
def test_unknown_nested_properties_are_not_persistable(
    validator, configuration_graph, collection
) -> None:
    if configuration_graph[collection]:
        configuration_graph[collection][0]["unreviewedNestedText"] = "Synthetic unexpected text"
    else:
        configuration_graph[collection].append(
            {"unreviewedNestedText": "Synthetic unexpected text"}
        )
    assert_safe(validator.validate(configuration_graph))


def test_snapshot_byte_limit_counts_encoded_utf8(validator, configuration_graph) -> None:
    configuration_graph["agents"][3]["prompt"] = "\u00e9" * (256 * 1024 + 1)
    errors = validator.validate(configuration_graph)
    assert_safe(errors)
    assert any(error["code"] == "configuration_too_large" for error in errors)


def test_publication_batch_budget_includes_authority_and_metadata(
    validator, configuration_graph
) -> None:
    errors = validator.validate(configuration_graph, authority_bytes=768 * 1024)
    assert_safe(errors)
    assert any(error["code"] == "publication_too_large" for error in errors)


@pytest.mark.parametrize(
    "rule",
    ["private_key_pem", "uri_userinfo", "connection_string", "sas_signature", "bearer_token"],
)
@pytest.mark.parametrize("location", ["prompt", "name", "routingDescription", "nested"])
def test_recognized_literals_are_rejected_everywhere_without_reflection(
    validator, configuration_graph, rule, location
) -> None:
    candidate = str(uuid4())
    value = {
        "private_key_pem": "-----BEGIN "
        + "PRIVATE KEY-----\n"
        + candidate
        + "\n-----END PRIVATE KEY-----",
        "uri_userinfo": "https://fixture-user:" + candidate + "@example.invalid/resource",
        "connection_string": "=".join(("Password", candidate)),
        "sas_signature": "https://example.invalid/resource?sv=fixture&sig=" + candidate,
        "bearer_token": " ".join(("Bearer", candidate)),
    }[rule]
    if location == "routingDescription":
        configuration_graph["communities"][0][location] = value
    elif location == "nested":
        configuration_graph["agents"][3]["capabilityAssignments"] = [{"unknownNestedField": value}]
    else:
        configuration_graph["agents"][3][location] = value
    errors = validator.validate(configuration_graph)
    assert_safe(errors, candidate)
    assert any(
        error["code"] == "credential_literal_prohibited" and error["rule_id"] == rule
        for error in errors
    )


@pytest.mark.parametrize(
    "text",
    [
        "Discuss password rotation without credentials.",
        "Explain a bearer instrument.",
        "See https://example.invalid/public",
        "Bearer",
        "A fictional account key is not an assignment.",
    ],
)
def test_security_words_without_recognized_credentials_are_not_false_positives(
    validator, configuration_graph, text
) -> None:
    configuration_graph["agents"][3]["prompt"] = text
    assert validator.validate(configuration_graph) == []


@pytest.mark.parametrize(
    "alias,valid",
    [
        ("secret://fixture-connection", True),
        ("secret://unknown-alias", False),
        ("secret://x", False),
        ("secret://Uppercase-alias", False),
        ("secret://fixture-connection?value=fixture", False),
        ("https://example.vault.azure.net/secrets/fixture", False),
    ],
)
def test_secret_reference_requires_exact_approved_alias(
    validator, configuration_graph, alias, valid
) -> None:
    configuration_graph["mcpConnections"] = [
        {
            "connectionId": "connection-one",
            "name": "Synthetic connection",
            "approvedEndpointRef": "fixture-public-endpoint",
            "enabled": True,
            "credentialRef": alias,
            "reviewedCapabilityCatalog": [],
        }
    ]
    errors = validator.validate(configuration_graph)
    if valid:
        assert errors == []
    else:
        assert_safe(errors)
        assert any(error["path"] == "mcpConnections[0].credentialRef" for error in errors)


def test_validation_failures_leave_input_graph_unchanged(validator, configuration_graph) -> None:
    configuration_graph["agents"][3]["communityId"] = "unknown"
    before = copy.deepcopy(configuration_graph)
    assert_safe(validator.validate(configuration_graph))
    assert configuration_graph == before


def test_valid_reviewed_public_read_only_capability_graph(validator, capability_graph) -> None:
    assert validator.validate(capability_graph) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "private",
        "mutating",
        "unknown-destination",
        "missing-connection",
        "unreviewed-action",
        "wrong-owner",
        "missing-tool",
        "assignment-override",
        "protected-assignment",
        "invalid-schema",
        "external-schema-reference",
    ],
)
def test_capabilities_cannot_expand_authority(validator, capability_graph, mutation) -> None:
    tool = capability_graph["tools"][0]
    assignment = capability_graph["agents"][3]["capabilityAssignments"][0]
    if mutation == "private":
        tool["classification"] = "private"
    elif mutation == "mutating":
        tool["effect"] = "write"
    elif mutation == "unknown-destination":
        tool["approvedDestinationRef"] = "unapproved-destination"
    elif mutation == "missing-connection":
        tool["mcpConnectionId"] = "missing"
    elif mutation == "unreviewed-action":
        assignment["permittedActionNames"] = ["unreviewed"]
    elif mutation == "wrong-owner":
        assignment["agentId"] = "agent-beta"
    elif mutation == "missing-tool":
        assignment["toolId"] = "missing"
    elif mutation == "assignment-override":
        assignment["classification"] = "private"
    elif mutation == "protected-assignment":
        capability_graph["agents"][0]["capabilityAssignments"] = [
            {**assignment, "agentId": "agent-welcome"}
        ]
    elif mutation == "invalid-schema":
        tool["inputSchema"] = {"type": "unknown-schema-type"}
    else:
        tool["inputSchema"] = {"$ref": "https://example.invalid/unreviewed-schema.json"}
    assert_safe(validator.validate(capability_graph))


def test_protected_ids_cannot_be_replaced_on_a_later_revision(
    validator, configuration_graph
) -> None:
    previous = copy.deepcopy(configuration_graph)
    configuration_graph["agents"][0]["agentId"] = "replacement-welcome"
    for relationship in configuration_graph["relationships"]:
        if relationship["sourceAgentId"] == "agent-welcome":
            relationship["sourceAgentId"] = "replacement-welcome"
    errors = validator.validate(configuration_graph, previous_revision=previous)
    assert_safe(errors)
    assert any(error["rule_id"] == "protected_identity" for error in errors)


@pytest.mark.parametrize("name", ["Password", "Pwd", "AccountKey", "SharedAccessKey"])
def test_each_connection_string_assignment_is_recognized(
    validator, configuration_graph, name
) -> None:
    candidate = str(uuid4())
    configuration_graph["agents"][3]["prompt"] = "=".join((name, candidate))
    errors = validator.validate(configuration_graph)
    assert_safe(errors, candidate)
    assert any(error["rule_id"] == "connection_string" for error in errors)


def test_unknown_input_field_name_cannot_reflect_a_credential_in_problem_paths(
    validator, configuration_graph
) -> None:
    candidate = str(uuid4())
    configuration_graph["Password=" + candidate] = "Synthetic unused value"
    errors = validator.validate(configuration_graph)
    assert_safe(errors, candidate)


def test_recursive_python_input_fails_with_safe_metadata(validator, configuration_graph) -> None:
    configuration_graph["unexpectedCycle"] = configuration_graph
    assert_safe(validator.validate(configuration_graph))


def test_deep_untrusted_json_is_bounded_before_schema_processing(
    validator, configuration_graph
) -> None:
    nested = {}
    cursor = nested
    for _ in range(80):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    configuration_graph["unexpectedNestedValue"] = nested
    errors = validator.validate(configuration_graph)
    assert_safe(errors)
    assert any(error["rule_id"] == "document_depth" for error in errors)


def test_opaque_unknown_property_names_are_not_echoed_as_field_paths(
    validator, configuration_graph
) -> None:
    candidate = "candidate" + uuid4().hex
    configuration_graph[candidate] = "Synthetic unused value"
    errors = validator.validate(configuration_graph)
    assert_safe(errors, candidate)
