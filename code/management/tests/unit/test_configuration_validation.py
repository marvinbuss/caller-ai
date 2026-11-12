"""Revision invariants and versioned, non-disclosing credential detection."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.parametrize(
    "target", ["agent-welcome", "agent-routing", "agent-goodbye", "agent-beta"]
)
def test_us2_ordinary_agent_layout_cannot_include_protected_or_foreign_nodes(
    validator, graph_v2, target
):
    assert validator.validate(graph_v2) == []
    graph_v2["layout"]["agentsByCommunity"]["community-alpha"][0]["nodeId"] = target
    errors = validator.validate(graph_v2)
    assert_safe(errors)
    assert any(issue["rule_id"] == "layout_reference" for issue in errors)


def test_schema_two_complete_graph_roundtrip_without_mutation(validator, graph_v2):
    from management_app.models.configuration import ConfigurationGraph

    before = copy.deepcopy(graph_v2)
    model = ConfigurationGraph.model_validate(graph_v2)
    assert model.model_dump(mode="json", by_alias=True) == graph_v2
    assert validator.validate(graph_v2) == []
    assert graph_v2 == before


def test_schema_two_rejects_schema_one_instead_of_migrating(validator, configuration_graph):
    from pydantic import ValidationError

    from management_app.models.configuration import ConfigurationGraph

    configuration_graph["schemaVersion"] = "1.0.0"
    configuration_graph.pop("communityConnections")
    configuration_graph.pop("layout")
    with pytest.raises(ValidationError):
        ConfigurationGraph.model_validate(configuration_graph)
    assert_safe(validator.validate(configuration_graph))


@pytest.mark.parametrize(
    "collection,field,characters",
    [("agents", "prompt", 8192), ("communities", "routingDescription", 2048)],
)
def test_schema_two_utf8_field_boundaries(validator, graph_v2, collection, field, characters):
    assert validator.validate(graph_v2) == []
    row = graph_v2[collection][3 if collection == "agents" else 0]
    row[field] = "\u00e9" * characters
    assert validator.validate(graph_v2) == []
    row[field] += "x"
    assert_safe(validator.validate(graph_v2))


@pytest.mark.parametrize("count,valid", [(50, True), (51, False)])
def test_schema_two_community_count_boundary(validator, graph_v2, count, valid):
    assert validator.validate(graph_v2) == []
    for index in range(len(graph_v2["communities"]), count):
        identifier = f"extra-{index}"
        graph_v2["communities"].append(
            {
                **graph_v2["communities"][0],
                "communityId": identifier,
                "name": identifier,
                "entryAgentId": identifier,
            }
        )
        graph_v2["agents"].append(
            {
                **graph_v2["agents"][3],
                "agentId": identifier,
                "name": identifier,
                "communityId": identifier,
            }
        )
    errors = validator.validate(graph_v2)
    assert (errors == []) is valid


@pytest.mark.parametrize("count,valid", [(200, True), (201, False)])
def test_schema_two_agent_count_boundary(validator, graph_v2, count, valid):
    assert validator.validate(graph_v2) == []
    for index in range(len(graph_v2["agents"]), count):
        identifier = f"extra-{index}"
        graph_v2["agents"].append(
            {**graph_v2["agents"][3], "agentId": identifier, "name": identifier}
        )
    assert (validator.validate(graph_v2) == []) is valid


@pytest.mark.parametrize(
    "identifier,valid",
    [
        ("a" * 128, True),
        ("a" * 129, False),
        ("-bad", False),
        ("bad space", False),
        ("\u00e9", False),
    ],
)
def test_schema_two_identifier_bounds(graph_v2, identifier, valid):
    from pydantic import ValidationError

    from management_app.models.configuration import ConfigurationGraph

    ConfigurationGraph.model_validate(graph_v2)
    graph_v2["agents"][5]["agentId"] = identifier
    if valid:
        assert ConfigurationGraph.model_validate(graph_v2).agents[5].agent_id == identifier
    else:
        with pytest.raises(ValidationError):
            ConfigurationGraph.model_validate(graph_v2)


@pytest.mark.parametrize("name,valid", [("n" * 128, True), ("n" * 129, False), (" ", False)])
def test_schema_two_name_bounds(validator, graph_v2, name, valid):
    assert validator.validate(graph_v2) == []
    graph_v2["communities"][0]["name"] = name
    assert (validator.validate(graph_v2) == []) is valid


@pytest.mark.parametrize("count,valid", [(128, True), (129, False)])
def test_schema_two_effective_tool_budget(validator, graph_v2, capability_graph, count, valid):
    from management_app.models.capabilities import ApprovedCapabilities

    assert validator.validate(graph_v2) == []
    graph_v2["tools"] = capability_graph["tools"]
    graph_v2["mcpConnections"] = capability_graph["mcpConnections"]
    prototype = graph_v2["mcpConnections"][0]["reviewedCapabilityCatalog"][0]
    actions = [f"lookup-{index}" for index in range(count)]
    graph_v2["mcpConnections"][0]["reviewedCapabilityCatalog"] = [
        {**prototype, "actionName": action} for action in actions
    ]
    assignment = copy.deepcopy(capability_graph["agents"][3]["capabilityAssignments"][0])
    assignment["permittedActionNames"] = actions
    graph_v2["agents"][3]["capabilityAssignments"] = [assignment]
    validator.reviewed = ApprovedCapabilities.from_reviewed(graph_v2)
    assert (validator.validate(graph_v2) == []) is valid


@pytest.mark.parametrize(
    "coordinate,valid",
    [
        (-100000, True),
        (100000, True),
        (-100001, False),
        (100001, False),
        (float("inf"), False),
        (float("nan"), False),
        (True, False),
    ],
)
def test_schema_two_layout_coordinate_boundaries(validator, graph_v2, coordinate, valid):
    assert validator.validate(graph_v2) == []
    graph_v2["layout"]["communities"][0]["x"] = coordinate
    assert (validator.validate(graph_v2) == []) is valid


@pytest.mark.parametrize(
    "mutation",
    [
        "agent-self",
        "agent-duplicate",
        "agent-foreign",
        "agent-protected",
        "community-self",
        "community-duplicate",
        "community-dangling",
        "context-unknown",
        "context-duplicate",
        "layout-duplicate",
        "layout-foreign",
        "layout-membership",
        "layout-semantic",
        "ordinary-membership",
        "protected-membership",
        "disabled-entry",
    ],
)
def test_schema_two_graph_and_layout_denials(validator, graph_v2, mutation):
    assert validator.validate(graph_v2) == []
    agent_edge = graph_v2["relationships"][0]
    community_edge = graph_v2["communityConnections"][0]
    if mutation == "agent-self":
        agent_edge["targetAgentId"] = agent_edge["sourceAgentId"]
    elif mutation == "agent-duplicate":
        graph_v2["relationships"].append({**agent_edge, "relationshipId": "duplicate-agent"})
    elif mutation == "agent-foreign":
        agent_edge["targetAgentId"] = "agent-beta"
    elif mutation == "agent-protected":
        agent_edge["sourceAgentId"] = "agent-routing"
    elif mutation == "community-self":
        community_edge["targetCommunityId"] = community_edge["sourceCommunityId"]
    elif mutation == "community-duplicate":
        graph_v2["communityConnections"].append(
            {**community_edge, "connectionId": "duplicate-community"}
        )
    elif mutation == "community-dangling":
        community_edge["targetCommunityId"] = "missing"
    elif mutation == "context-unknown":
        community_edge["allowedContextFields"] = ["conversation"]
    elif mutation == "context-duplicate":
        community_edge["allowedContextFields"] = ["request_summary", "request_summary"]
    elif mutation == "layout-duplicate":
        graph_v2["layout"]["communities"].append(graph_v2["layout"]["communities"][0].copy())
    elif mutation == "layout-foreign":
        graph_v2["layout"]["communities"][0]["nodeId"] = "missing"
    elif mutation == "layout-membership":
        graph_v2["layout"]["agentsByCommunity"]["community-alpha"][0]["nodeId"] = "agent-beta"
    elif mutation == "layout-semantic":
        graph_v2["layout"]["communities"][0]["permissions"] = ["all"]
    elif mutation == "ordinary-membership":
        graph_v2["agents"][3]["communityId"] = None
    elif mutation == "protected-membership":
        graph_v2["agents"][0]["communityId"] = "community-alpha"
    else:
        graph_v2["agents"][3]["enabled"] = False
    assert_safe(validator.validate(graph_v2))


def test_schema_two_directed_connections_and_layout_are_independent(validator, graph_v2):
    assert validator.validate(graph_v2) == []
    forward = graph_v2["communityConnections"][0]
    graph_v2["communityConnections"].append(
        {
            **forward,
            "connectionId": "beta-to-alpha",
            "sourceCommunityId": "community-beta",
            "targetCommunityId": "community-alpha",
        }
    )
    assert validator.validate(graph_v2) == []
    before = copy.deepcopy(graph_v2)
    graph_v2["layout"] = {"communities": [], "agentsByCommunity": {}}
    assert validator.validate(graph_v2) == []
    assert {key: value for key, value in graph_v2.items() if key != "layout"} == {
        key: value for key, value in before.items() if key != "layout"
    }


@pytest.mark.parametrize("total,valid", [(1000, True), (1001, False)])
def test_schema_two_combined_connection_budget(validator, graph_v2, total, valid):
    assert validator.validate(graph_v2) == []
    members = ["agent-alpha", "agent-alpha-peer"]
    for index in range(38):
        identifier = f"extra-{index}"
        members.append(identifier)
        graph_v2["agents"].append(
            {**graph_v2["agents"][3], "agentId": identifier, "name": identifier}
        )
    pairs = [(source, target) for source in members for target in members if source != target]
    graph_v2["relationships"] = [
        {
            "relationshipId": f"path-{index}",
            "sourceAgentId": source,
            "targetAgentId": target,
            "allowedContextFields": [],
        }
        for index, (source, target) in enumerate(pairs[: total - 1])
    ]
    assert (validator.validate(graph_v2) == []) is valid


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
    "mutation", ["unknown-target", "foreign-direct", "raw-context", "protected-path"]
)
def test_relationship_references_context_and_lifecycle(
    validator, configuration_graph, mutation
) -> None:
    configuration_graph["agents"].append(
        {**configuration_graph["agents"][3], "agentId": "alpha-peer", "name": "Alpha peer"}
    )
    relationships = configuration_graph["relationships"]
    relationships.append(
        {
            "relationshipId": "ordinary-path",
            "sourceAgentId": "agent-alpha",
            "targetAgentId": "alpha-peer",
            "allowedContextFields": [],
        }
    )
    assert validator.validate(configuration_graph) == []
    if mutation == "protected-path":
        relationships[0]["sourceAgentId"] = "agent-routing"
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


def rules(errors: list[dict]) -> set[str]:
    return {error["rule_id"] for error in errors}


def with_limits(validator, voices=("marin", "quartz"), max_output_tokens=1024):
    from management_app.services.configuration_validation import ConversationLimits

    limits = ConversationLimits(frozenset(voices), max_output_tokens)
    validator.conversation_limits = lambda: limits
    return validator


def test_schema_two_one_accepts_routable_and_conversation_settings(validator, configuration_graph):
    from management_app.models.configuration import ConfigurationGraph

    with_limits(validator)
    assert configuration_graph["schemaVersion"] == "2.1.0"
    configuration_graph["communities"][1]["routable"] = False
    configuration_graph["conversationSettings"] = {
        "voice": "marin",
        "maxOutputTokens": 800,
        "idleTimeoutSeconds": 90,
    }
    assert validator.validate(configuration_graph) == []
    model = ConfigurationGraph.model_validate(configuration_graph)
    assert model.model_dump(mode="json", by_alias=True) == configuration_graph
    assert model.communities[1].routable is False


@pytest.mark.parametrize(
    "settings",
    [{}, {"voice": "quartz"}, {"maxOutputTokens": 1}, {"idleTimeoutSeconds": 120}],
)
def test_conversation_settings_fields_are_optional(validator, configuration_graph, settings):
    from management_app.models.configuration import ConfigurationGraph

    with_limits(validator)
    configuration_graph["conversationSettings"] = settings
    assert validator.validate(configuration_graph) == []
    dumped = ConfigurationGraph.model_validate(configuration_graph).model_dump(
        mode="json", by_alias=True
    )
    assert dumped["conversationSettings"] == settings


def test_conversation_settings_unknown_fields_are_schema_errors(validator, configuration_graph):
    configuration_graph["conversationSettings"] = {"voice": "marin", "temperature": 1}
    errors = validator.validate(configuration_graph)
    assert_safe(errors)
    assert "schema" in rules(errors)


@pytest.mark.parametrize(
    "settings,rule",
    [
        ({"voice": "cove"}, "conversation_voice"),
        ({"voice": "Not A Voice"}, "conversation_voice"),
        ({"maxOutputTokens": 0}, "conversation_limit"),
        ({"maxOutputTokens": 1025}, "conversation_limit"),
        ({"maxOutputTokens": 4097}, "conversation_limit"),
        ({"maxOutputTokens": "800"}, "conversation_limit"),
        ({"idleTimeoutSeconds": 29}, "conversation_limit"),
        ({"idleTimeoutSeconds": 121}, "conversation_limit"),
        ({"idleTimeoutSeconds": 60.5}, "conversation_limit"),
    ],
)
def test_conversation_settings_outside_the_runtime_catalog_and_limits_are_refused(
    validator, configuration_graph, settings, rule
):
    with_limits(validator)
    configuration_graph["conversationSettings"] = settings
    errors = validator.validate(configuration_graph)
    assert_safe(errors)
    assert rules(errors) == {rule}


def test_without_the_runtime_catalog_only_an_unchanged_voice_is_accepted(
    validator, configuration_graph
):
    previous = {**configuration_graph, "conversationSettings": {"voice": "quartz"}}
    configuration_graph["conversationSettings"] = {"voice": "quartz", "idleTimeoutSeconds": 60}
    assert validator.validate(configuration_graph, previous_revision=previous) == []
    configuration_graph["conversationSettings"] = {"voice": "marin"}
    errors = validator.validate(configuration_graph, previous_revision=previous)
    assert rules(errors) == {"conversation_voice"}
    assert rules(validator.validate(configuration_graph)) == {"conversation_voice"}


def test_conversation_limits_come_from_the_status_body():
    from management_app.services.configuration_validation import ConversationLimits

    limits = ConversationLimits.from_status(
        {
            "supportedVoices": [{"id": "marin"}, {"id": "quartz"}],
            "limits": {"maxOutputTokens": 900},
        }
    )
    assert limits == ConversationLimits(frozenset({"marin", "quartz"}), 900)


def test_schema_two_zero_document_reads_with_routable_true(validator, configuration_graph):
    from management_app.models.configuration import ConfigurationGraph

    configuration_graph["schemaVersion"] = "2.0.0"
    for community in configuration_graph["communities"]:
        community.pop("routable")
    assert validator.validate(configuration_graph) == []
    model = ConfigurationGraph.model_validate(configuration_graph)
    assert all(community.routable for community in model.communities)
    assert model.conversation_settings is None
    assert "conversationSettings" not in model.model_dump(mode="json", by_alias=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("communityId", "default-billing"),
        ("communityId", "default-welcome"),
        ("name", "Welcome"),
        ("name", "routing"),
        ("name", "GOODBYE"),
        ("name", " Goodbye "),
    ],
)
def test_reserved_community_ids_and_names_are_refused(validator, configuration_graph, field, value):
    community = configuration_graph["communities"][0]
    if field == "communityId":
        for agent in configuration_graph["agents"]:
            if agent["communityId"] == community["communityId"]:
                agent["communityId"] = value
    community[field] = value
    errors = validator.validate(configuration_graph)
    assert_safe(errors)
    assert "reserved_name" in rules(errors)
    assert value.strip() not in json.dumps(errors)


@pytest.mark.parametrize("name", ["Welcome desk", "Routings", "Goodbyes", "Default"])
def test_names_near_reserved_ones_are_allowed(validator, configuration_graph, name):
    configuration_graph["communities"][0]["name"] = name
    assert validator.validate(configuration_graph) == []
