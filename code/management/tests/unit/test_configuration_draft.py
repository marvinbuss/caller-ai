"""Unsaved editor state remains separate from committed revisions and authentication."""

import asyncio
import copy
import json
import time
from uuid import uuid4

import pytest


@pytest.fixture
def draft(configuration_graph):
    from management_app.state.configuration_editor import EditorDraft

    return EditorDraft.from_revision({**configuration_graph, "id": "revision-one"}, '"etag-one"')


def test_draft_is_detached_and_conflict_retains_unsaved_values(draft, configuration_graph) -> None:
    original = copy.deepcopy(configuration_graph)
    draft.edit_agent("agent-alpha", "prompt", "Synthetic unsaved edit")
    before = json.dumps(draft.graph, sort_keys=True)
    draft.rejected(412, {"code": "configuration_conflict"})
    assert json.dumps(draft.graph, sort_keys=True) == before
    assert draft.etag == '"etag-one"' and draft.conflict
    assert configuration_graph == original


def test_editor_components_construct_with_typed_reflex_events() -> None:
    import reflex as rx

    from management_app.pages.communities import configuration_page

    assert isinstance(configuration_page(), rx.Component)


@pytest.mark.parametrize("role", ["welcome", "routing", "goodbye"])
def test_protected_roles_cannot_be_deleted_disabled_or_moved(draft, role) -> None:
    agent_id = "agent-" + role
    before = copy.deepcopy(draft.graph)
    for field, value in (
        ("enabled", False),
        ("communityId", "community-alpha"),
        ("role", "ordinary"),
    ):
        with pytest.raises(ValueError, match="protected_role"):
            draft.edit_agent(agent_id, field, value)
    with pytest.raises(ValueError, match="protected_role"):
        draft.delete_agent(agent_id)
    assert draft.graph == before


def test_field_errors_do_not_replace_other_unsaved_edits(draft) -> None:
    draft.edit_agent("agent-alpha", "name", "")
    draft.edit_agent("agent-alpha", "prompt", "Retained synthetic edit")
    draft.rejected(
        422,
        {
            "errors": [
                {
                    "path": "agents[3].name",
                    "code": "invalid_configuration",
                    "rule_id": "schema",
                    "policy_version": 1,
                }
            ]
        },
    )
    assert draft.errors["agents[3].name"] == "invalid_configuration"
    assert draft.graph["agents"][3]["prompt"] == "Retained synthetic edit"


def test_new_community_and_entry_have_required_lifecycle_paths(draft, validator) -> None:
    community_id = draft.add_community(
        "New community", "Synthetic routing", "New entry", "Synthetic prompt"
    )
    community = next(
        item for item in draft.graph["communities"] if item["communityId"] == community_id
    )
    entry = next(
        item for item in draft.graph["agents"] if item["agentId"] == community["entryAgentId"]
    )
    assert entry["communityId"] == community_id and entry["role"] == "ordinary"
    assert validator.validate(draft.graph) == []


def test_entry_choices_never_include_protected_foreign_or_disabled_agents(draft) -> None:
    choices = draft.entry_choices("community-alpha")
    assert [item["agentId"] for item in choices] == ["agent-alpha"]
    with pytest.raises(ValueError, match="invalid_entry_agent"):
        draft.edit_community("community-alpha", "entryAgentId", "agent-beta")


def test_confirmed_community_deletion_preserves_protected_roles_and_valid_paths(
    draft, validator
) -> None:
    draft.delete_community("community-alpha")
    assert all(item["communityId"] != "community-alpha" for item in draft.graph["communities"])
    assert all(item["agentId"] != "agent-alpha" for item in draft.graph["agents"])
    assert {item["role"] for item in draft.graph["agents"] if item["role"] != "ordinary"} == {
        "welcome",
        "routing",
        "goodbye",
    }
    assert validator.validate(draft.graph) == []


def test_same_community_relationship_and_allowed_context_edits_are_valid(draft, validator) -> None:
    peer = draft.add_agent("community-alpha", "Alpha peer", "Synthetic peer prompt")
    assert [item["agentId"] for item in draft.relationship_targets("agent-alpha")] == [peer]
    identifier = draft.add_relationship("agent-alpha", peer)
    draft.set_context_field(identifier, "transfer_reason", False)
    relationship = next(
        item for item in draft.graph["relationships"] if item["relationshipId"] == identifier
    )
    assert relationship["allowedContextFields"] == ["request_summary"]
    assert validator.validate(draft.graph) == []
    draft.delete_relationship(identifier)
    assert validator.validate(draft.graph) == []


@pytest.mark.parametrize(
    "source,target",
    [
        ("agent-alpha", "agent-beta"),
        ("agent-goodbye", "agent-alpha"),
        ("agent-alpha", "agent-welcome"),
        ("agent-routing", "agent-routing"),
        ("agent-welcome", "agent-alpha"),
    ],
)
def test_forbidden_relationships_do_not_mutate_draft(draft, source, target) -> None:
    before = copy.deepcopy(draft.graph)
    with pytest.raises(ValueError, match="relationship_prohibited"):
        draft.add_relationship(source, target)
    assert draft.graph == before


def test_required_lifecycle_paths_cannot_be_deleted(draft) -> None:
    required = draft.required_relationship_ids
    assert len(required) == 8
    before = copy.deepcopy(draft.graph)
    for identifier in required:
        with pytest.raises(ValueError, match="required_lifecycle_path"):
            draft.delete_relationship(identifier)
    assert draft.graph == before


def test_routing_targets_entry_only_and_required_paths_follow_enabled_community(
    draft, validator
) -> None:
    peer = draft.add_agent("community-alpha", "Alpha peer", "Synthetic peer prompt")
    with pytest.raises(ValueError, match="relationship_prohibited"):
        draft.add_relationship("agent-routing", peer)
    entry_path = next(
        item
        for item in draft.graph["relationships"]
        if item["sourceAgentId"] == "agent-routing" and item["targetAgentId"] == "agent-alpha"
    )
    assert entry_path["relationshipId"] in draft.required_relationship_ids
    draft.edit_community("community-alpha", "enabled", False)
    assert entry_path["relationshipId"] not in draft.required_relationship_ids
    draft.delete_relationship(entry_path["relationshipId"])
    assert validator.validate(draft.graph) == []


def test_relationship_duplicates_and_raw_context_are_rejected_without_partial_edits(draft) -> None:
    before = copy.deepcopy(draft.graph)
    with pytest.raises(ValueError, match="duplicate_relationship"):
        draft.add_relationship("agent-alpha", "agent-routing")
    identifier = draft.graph["relationships"][0]["relationshipId"]
    with pytest.raises(ValueError, match="context_field_prohibited"):
        draft.set_context_field(identifier, "conversation", True)
    assert draft.graph == before


def test_relationship_choices_work_during_invalid_unsaved_name_edit(draft) -> None:
    draft.edit_agent("agent-alpha", "name", "")
    assert len(draft.required_relationship_ids) == 8
    assert draft.relationship_targets("agent-alpha") == []


def test_success_advances_etag_and_clears_only_saved_validation_state(draft) -> None:
    draft.edit_agent("agent-alpha", "prompt", "Synthetic saved value")
    draft.rejected(422, {"errors": [{"path": "agents[3].name", "code": "invalid_configuration"}]})
    published = {**copy.deepcopy(draft.graph), "id": "revision-two"}
    draft.committed(published, '"etag-two"')
    assert draft.etag == '"etag-two"' and not draft.errors and not draft.conflict
    assert draft.graph["agents"][3]["prompt"] == "Synthetic saved value"


@pytest.mark.parametrize("previous_status", ["Saved", "Saved; audit pending"])
@pytest.mark.parametrize("outcome", ["saved", "audit_pending", "unavailable"])
def test_save_acknowledgment_belongs_to_the_current_request(
    draft, previous_status, outcome
) -> None:
    from types import SimpleNamespace

    import httpx

    from management_app.state.configuration_editor import ConfigurationEditorState

    requests = []

    async def request(*args, **kwargs):
        requests.append(kwargs)
        if outcome == "unavailable":
            raise RuntimeError("synthetic_transport_unavailable")
        return httpx.Response(
            200,
            json={**copy.deepcopy(kwargs["graph"]), "id": "revision-two"},
            headers={
                "ETag": '"etag-two"',
                "X-Audit-Status": "pending" if outcome == "audit_pending" else "complete",
            },
        )

    state = SimpleNamespace(
        busy=False,
        status=previous_status,
        errors={},
        error="",
        generation=0,
        router=SimpleNamespace(
            session=SimpleNamespace(session_id="fixture-sid", client_token="fixture-token")
        ),
        _editing=lambda: draft,
        _services=lambda: SimpleNamespace(request=request),
    )

    async def scenario():
        submission = ConfigurationEditorState.save.fn(state)
        try:
            await anext(submission)
            assert state.busy and state.status == "Saving"
            assert not requests
            with pytest.raises(StopAsyncIteration):
                await anext(submission)
            assert len(requests) == 1 and not state.busy
            if outcome == "unavailable":
                assert state.status == "Unsaved"
                assert state.error == "Save unavailable. Unsaved edits retained."
                assert draft.etag == '"etag-one"'
            else:
                expected = "Saved; audit pending" if outcome == "audit_pending" else "Saved"
                assert state.status == expected and not state.error
                assert draft.etag == '"etag-two"'
        finally:
            await submission.aclose()

    asyncio.run(scenario())


def test_submitted_catalog_cannot_approve_its_own_action(validator, capability_graph) -> None:
    graph = copy.deepcopy(capability_graph)
    reviewed = copy.deepcopy(graph["mcpConnections"][0]["reviewedCapabilityCatalog"][0])
    reviewed["actionName"] = "unreviewed-action"
    graph["mcpConnections"][0]["reviewedCapabilityCatalog"].append(reviewed)
    graph["agents"][3]["capabilityAssignments"][0]["permittedActionNames"] = ["unreviewed-action"]
    errors = validator.validate(graph)
    assert any(issue["rule_id"] == "capability_review" for issue in errors)


def test_reviewed_catalog_is_detached_and_requires_explicit_action_choices(
    capability_graph,
) -> None:
    from management_app.models.capabilities import ApprovedCapabilities

    approved = ApprovedCapabilities.from_reviewed(capability_graph)
    capability_graph["mcpConnections"][0]["reviewedCapabilityCatalog"].clear()
    assert approved.actions("tool-public") == ["lookup"]
    tool = approved.tool("tool-public")
    tool["classification"] = "private"
    assert approved.tool("tool-public")["classification"] == "public"
    with pytest.raises(ValueError, match="unreviewed_capability"):
        approved.tool("guessed-tool")


def test_missing_server_review_fails_closed(validator, capability_graph) -> None:
    from management_app.services.configuration_validation import ConfigurationValidator

    unapproved = ConfigurationValidator(
        policy=validator.detector.policy.model_dump(by_alias=True),
        approved_secret_aliases=frozenset({"fixture-connection"}),
        approved_destination_refs=frozenset({"fixture-public-endpoint"}),
    )
    assert any(
        issue["rule_id"] == "capability_review" for issue in unapproved.validate(capability_graph)
    )


def test_direct_tool_requires_separately_reviewed_actions(
    configuration_graph, capability_graph, validator
) -> None:
    from management_app.models.capabilities import ApprovedCapabilities
    from management_app.services.configuration_validation import ConfigurationValidator
    from management_app.state.configuration_editor import EditorDraft

    review = copy.deepcopy(capability_graph)
    review["tools"][0]["mcpConnectionId"] = None
    review["mcpConnections"] = []
    approved = ApprovedCapabilities.from_reviewed(
        review, direct_actions={"tool-public": ["lookup"]}
    )
    draft = EditorDraft.from_revision(configuration_graph, '"direct-etag"', reviewed=approved)
    draft.add_tool("tool-public")
    draft.set_action("agent-alpha", "tool-public", "lookup", True)
    checked = ConfigurationValidator(
        policy=validator.detector.policy.model_dump(by_alias=True),
        approved_secret_aliases=frozenset(),
        approved_destination_refs=frozenset({"fixture-public-endpoint"}),
        reviewed=approved,
    )
    assert checked.validate(draft.graph) == []
    assert draft.graph["tools"][0]["mcpConnectionId"] is None
    with pytest.raises(ValueError, match="unreviewed_capability"):
        draft.set_action("agent-alpha", "tool-public", "unreviewed", True)


@pytest.fixture
def capability_draft(configuration_graph, capability_graph):
    from management_app.models.capabilities import ApprovedCapabilities
    from management_app.state.configuration_editor import EditorDraft

    return EditorDraft.from_revision(
        configuration_graph,
        '"capability-etag"',
        reviewed=ApprovedCapabilities.from_reviewed(capability_graph),
    )


def test_reviewed_definitions_and_assignment_form_a_valid_graph(
    capability_draft, validator
) -> None:
    draft = capability_draft
    draft.add_connection("connection-one")
    draft.add_tool("tool-public")
    draft.set_action("agent-alpha", "tool-public", "lookup", True)
    assignment = draft.graph["agents"][3]["capabilityAssignments"][0]
    assert assignment == {
        "agentId": "agent-alpha",
        "toolId": "tool-public",
        "permittedActionNames": ["lookup"],
        "allowedInputConstraints": {},
    }
    assert validator.validate(draft.graph) == []


@pytest.mark.parametrize("role", ["welcome", "routing", "goodbye"])
def test_protected_roles_cannot_receive_tools(capability_draft, role) -> None:
    capability_draft.add_connection("connection-one")
    capability_draft.add_tool("tool-public")
    before = copy.deepcopy(capability_draft.graph)
    with pytest.raises(ValueError, match="protected_role"):
        capability_draft.set_action("agent-" + role, "tool-public", "lookup", True)
    assert capability_draft.graph == before


@pytest.mark.parametrize(
    "operation", ["unknown-tool", "unknown-action", "classification", "destination", "credential"]
)
def test_catalog_edits_cannot_expand_server_review(capability_draft, operation) -> None:
    draft = capability_draft
    draft.add_connection("connection-one")
    draft.add_tool("tool-public")
    before = copy.deepcopy(draft.graph)
    with pytest.raises(ValueError):
        if operation == "unknown-tool":
            draft.add_tool("guessed-tool")
        elif operation == "unknown-action":
            draft.set_action("agent-alpha", "tool-public", "unreviewed", True)
        elif operation == "classification":
            draft.edit_tool("tool-public", "classification", "private")
        elif operation == "destination":
            draft.edit_connection("connection-one", "approvedEndpointRef", "unreviewed")
        else:
            draft.edit_connection("connection-one", "credentialRef", "secret://unreviewed")
    assert draft.graph == before


def test_capability_removal_cleans_assignments_and_preserves_unsaved_constraints(
    capability_draft, validator
) -> None:
    draft = capability_draft
    draft.add_connection("connection-one")
    draft.add_tool("tool-public")
    draft.set_action("agent-alpha", "tool-public", "lookup", True)
    draft.edit_constraints("agent-alpha", "tool-public", '{"limit":5}')
    draft.rejected(412, {"code": "configuration_conflict"})
    assert draft.graph["agents"][3]["capabilityAssignments"][0]["allowedInputConstraints"] == {
        "limit": 5
    }
    with pytest.raises(ValueError, match="connection_in_use"):
        draft.delete_connection("connection-one")
    draft.delete_tool("tool-public")
    assert draft.graph["agents"][3]["capabilityAssignments"] == []
    draft.delete_connection("connection-one")
    assert validator.validate(draft.graph) == []


def test_editor_api_client_uses_real_session_and_rechecks_revocation(
    configuration_graph, validator
) -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI, HTTPException

    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.adapters.configuration_repository import LocalConfigurationRepository
    from management_app.api.configuration import create_router
    from management_app.security import (
        EntraPolicy,
        EventAuthorizationMiddleware,
        IdentityService,
        Principal,
        ReflexEventGuard,
        SessionStore,
        TokenVerifier,
    )
    from management_app.services.audit import AuditService
    from management_app.state.configuration_editor import EditorServices

    class NoKeys:
        async def signing_keys(self):
            pytest.fail("A cookie session must not discover signing keys")

    policy = EntraPolicy(
        tenant_id=str(uuid4()),
        client_id=str(uuid4()),
        api_audience=str(uuid4()),
        origin="https://localhost:8443",
        manager_role="Configuration.Manager",
        manager_scope="Configuration.ReadWrite",
    )
    sessions = SessionStore(policy=policy)
    identity = IdentityService(
        policy=policy, verifier=TokenVerifier(policy=policy, keys=NoKeys()), sessions=sessions
    )
    ticket = sessions.issue(
        Principal(str(uuid4()), policy.tenant_id, int(time.time()) + 300, (policy.manager_role,))
    )
    guard = ReflexEventGuard(identity=identity)
    token = uuid4().hex
    guard.connect(
        "fixture-sid",
        {
            "QUERY_STRING": "token=" + token,
            "asgi.scope": {
                "type": "websocket",
                "headers": [
                    (b"origin", policy.origin.encode()),
                    (b"cookie", (identity.cookie_name + "=" + ticket.cookie).encode()),
                ],
            },
        },
    )
    store = LocalConfigurationRepository(validator=validator)
    asyncio.run(store.initialize(configuration_graph, actor_id="bootstrap"))
    api = FastAPI()
    api.include_router(
        create_router(
            repository=store,
            identity=identity,
            validator=validator,
            audit=AuditService(repository=LocalAuditRepository()),
        )
    )
    services = EditorServices(api=api, guard=guard)

    async def scenario():
        middleware = EventAuthorizationMiddleware(guard=guard)
        state = SimpleNamespace(
            router=SimpleNamespace(
                session=SimpleNamespace(session_id="fixture-sid", client_token=token)
            )
        )
        internal = SimpleNamespace(router_data={})
        assert await middleware.preprocess(None, state, internal) is None
        foreign = SimpleNamespace(router_data={"sid": "foreign-sid", "token": "guessed-token"})
        assert await middleware.preprocess(None, state, foreign) is not None
        response = await services.request("fixture-sid", token, "GET")
        assert response.status_code == 200
        updated = copy.deepcopy(configuration_graph)
        updated["agents"][3]["prompt"] = "Synthetic authenticated editor save"
        saved = await services.request(
            "fixture-sid", token, "PUT", graph=updated, etag=response.headers["ETag"]
        )
        assert saved.status_code == 200
        sessions.revoke(ticket.cookie)
        assert await middleware.preprocess(None, state, internal) is not None
        with pytest.raises(HTTPException):
            await services.request("fixture-sid", token, "GET")

    asyncio.run(scenario())
