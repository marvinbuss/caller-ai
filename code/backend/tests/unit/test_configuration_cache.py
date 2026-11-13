"""Only complete validated immutable revisions may become new-call configuration."""

import copy
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import urlunsplit

import httpx
import pytest


@pytest.fixture
def cache():
    from caller_runtime.domain.configuration import RevisionCache

    return RevisionCache()


def test_schema_two_fixture_preserves_legacy_and_directed_boundaries(
    community_configuration_v2, community_configuration
):
    graph = community_configuration_v2
    assert graph["schemaVersion"] == "2.0.0"
    assert community_configuration["schemaVersion"] == "1.0.0"
    assert graph["communityConnections"] == [
        {
            "connectionId": "alpha-to-beta",
            "sourceCommunityId": "alpha",
            "targetCommunityId": "beta",
            "allowedContextFields": ["request_summary", "transfer_reason"],
        }
    ]
    agents = {agent["agentId"]: agent for agent in graph["agents"]}
    assert {agent["role"] for agent in agents.values() if agent["communityId"] is None} == {
        "welcome",
        "routing",
        "goodbye",
    }
    assert all(
        agents[edge["sourceAgentId"]]["role"] == "ordinary"
        and agents[edge["targetAgentId"]]["role"] == "ordinary"
        and agents[edge["sourceAgentId"]]["communityId"]
        == agents[edge["targetAgentId"]]["communityId"]
        for edge in graph["relationships"]
    )
    assert {position["nodeId"] for position in graph["layout"]["communities"]} == {
        "alpha",
        "beta",
    }
    for community, positions in graph["layout"]["agentsByCommunity"].items():
        assert {position["nodeId"] for position in positions} == {
            agent["agentId"] for agent in agents.values() if agent["communityId"] == community
        }
    graph["agents"][0]["prompt"] = "Synthetic changed setup prompt"
    assert community_configuration["agents"][0]["prompt"] != graph["agents"][0]["prompt"]


def test_schema_two_fixture_revisions_are_detached_and_hash_layout(revision_document_v2):
    first = revision_document_v2()
    second = revision_document_v2(2)
    assert first["id"] != second["id"] and second["revisionNumber"] == 2
    assert first["organizationId"] == "default" and first["schemaVersion"] == "2.0.0"
    fields = (
        "schemaVersion",
        "agents",
        "communities",
        "relationships",
        "communityConnections",
        "layout",
        "tools",
        "mcpConnections",
    )
    graph = {field: first[field] for field in fields}
    expected = hashlib.sha256(
        json.dumps(
            graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    assert first["graphHash"] == expected
    first["layout"]["communities"][0]["x"] += 100
    first["agents"][0]["prompt"] = "Synthetic detached setup mutation"
    restored = revision_document_v2()
    assert restored["graphHash"] == expected
    assert restored["layout"] != first["layout"]
    assert restored["agents"][0]["prompt"] != first["agents"][0]["prompt"]


def test_v2_policy_snapshot_keeps_full_hash_without_recomputing_it_from_projection(
    cache, runtime_revision_v2, revision_document_v2
):
    projection = runtime_revision_v2()
    source = revision_document_v2()
    assert projection["policyHash"] != projection["graphHash"]
    pinned = cache.install(projection, etag='"projection-v2"')
    assert pinned.graph_hash == source["graphHash"]
    assert pinned.policy_hash == projection["policyHash"]
    assert pinned.schema_version == "2.0.0"
    assert "layout" not in pinned.for_runtime()
    assert pinned.view_for("a-source")["agent"]["agentId"] == "a-source"
    projection["agents"][0]["prompt"] = "synthetic-private-detached-projection"
    assert "synthetic-private" not in repr(pinned) + repr(pinned.for_runtime())


@pytest.mark.parametrize("business", ["absent", "disabled"])
def test_fresh_valid_publication_without_enabled_business_cannot_admit(
    cache, runtime_revision_v2, business
):
    from caller_runtime.domain.configuration import GRAPH_FIELDS, encoded

    document = runtime_revision_v2()
    if business == "absent":
        document["agents"] = [agent for agent in document["agents"] if agent["role"] != "ordinary"]
        document["communities"] = []
        document["relationships"] = []
        document["communityConnections"] = []
    else:
        for community in document["communities"]:
            community["enabled"] = False
    policy = {key: document[key] for key in GRAPH_FIELDS}
    document["policyHash"] = hashlib.sha256(encoded(policy)).hexdigest()
    document["graphHash"] = hashlib.sha256(
        encoded(policy | {"layout": {"communities": [], "agentsByCommunity": {}}})
    ).hexdigest()
    snapshot = cache.install(document, etag='"protected-only-or-disabled"')
    assert cache.current is snapshot
    assert snapshot.view_for("routing")["communities"] == []
    with pytest.raises(PermissionError, match="configuration_unavailable"):
        cache.pin()


@pytest.mark.parametrize(
    "corruption",
    [
        "policy-hash",
        "full-hash-format",
        "layout",
        "credential-reference",
        "schema-one",
        "extra-metadata",
        "community-reference",
        "community-duplicate",
        "agent-protected",
        "agent-foreign",
    ],
)
def test_v2_policy_snapshot_rejects_strict_and_semantic_corruption(
    cache, runtime_revision_v2, corruption
):
    from caller_runtime.domain.configuration import encoded

    pinned = cache.install(runtime_revision_v2(), etag='"projection-v2"')
    candidate = runtime_revision_v2(2)
    if corruption == "policy-hash":
        candidate["agents"][0]["prompt"] += " synthetic changed policy"
    elif corruption == "full-hash-format":
        candidate["graphHash"] = "not-a-digest"
    elif corruption == "layout":
        candidate["layout"] = {"communities": [], "agentsByCommunity": {}}
    elif corruption == "credential-reference":
        candidate["mcpConnections"] = [
            {
                "connectionId": "private-material",
                "name": "Synthetic connection",
                "approvedEndpointRef": "approved-public-fixture",
                "enabled": True,
                "credentialRef": "secret://fixture-connection",
                "reviewedCapabilityCatalog": [],
            }
        ]
    elif corruption == "schema-one":
        candidate["schemaVersion"] = "1.0.0"
    elif corruption == "extra-metadata":
        candidate["transcript"] = "synthetic-private-forbidden-content"
    elif corruption == "community-reference":
        candidate["communityConnections"][0]["targetCommunityId"] = "missing"
    elif corruption == "community-duplicate":
        candidate["communityConnections"].append(
            {**candidate["communityConnections"][0], "connectionId": "duplicate-direction"}
        )
    elif corruption == "agent-protected":
        candidate["relationships"][0]["sourceAgentId"] = "welcome"
    else:
        candidate["relationships"][0]["targetAgentId"] = "b-entry"
    if corruption != "policy-hash":
        fields = (
            "schemaVersion",
            "agents",
            "communities",
            "relationships",
            "communityConnections",
            "tools",
            "mcpConnections",
        )
        candidate["policyHash"] = hashlib.sha256(
            encoded({key: candidate[key] for key in fields})
        ).hexdigest()
    with pytest.raises(ValueError, match="invalid_configuration_snapshot"):
        cache.install(candidate, etag='"invalid-projection"')
    assert cache.pin() is pinned


def rehashed(document: dict) -> dict:
    from caller_runtime.domain.configuration import GRAPH_FIELDS, OPTIONAL_GRAPH_FIELDS, encoded

    policy = {key: document[key] for key in GRAPH_FIELDS} | {
        key: document[key] for key in OPTIONAL_GRAPH_FIELDS if key in document
    }
    document["policyHash"] = hashlib.sha256(encoded(policy)).hexdigest()
    return document


def schema_two_one(document: dict, **settings) -> dict:
    document["schemaVersion"] = "2.1.0"
    for community in document["communities"]:
        community["routable"] = True
    if settings:
        document["conversationSettings"] = settings
    return rehashed(document)


def test_schema_two_one_is_accepted_with_routable_and_conversation_settings(
    cache, runtime_revision_v2
):
    document = schema_two_one(
        runtime_revision_v2(), voice="marin", maxOutputTokens=800, idleTimeoutSeconds=90
    )
    document["communities"][1]["routable"] = False
    snapshot = cache.install(rehashed(document), etag='"projection-v2-1"')
    assert snapshot.schema_version == "2.1.0"
    runtime = snapshot.for_runtime()
    assert runtime["conversationSettings"] == {
        "voice": "marin",
        "maxOutputTokens": 800,
        "idleTimeoutSeconds": 90,
    }
    assert [community["routable"] for community in runtime["communities"]] == [True, False]
    assert snapshot.conversation_settings() == runtime["conversationSettings"]


def test_absent_routable_and_conversation_settings_mean_defaults(cache, runtime_revision_v2):
    snapshot = cache.install(runtime_revision_v2(), etag='"projection-v2"')
    assert snapshot.schema_version == "2.0.0"
    assert "conversationSettings" not in snapshot.for_runtime()
    assert snapshot.conversation_settings() == {}
    assert all(
        snapshot.routable(row["communityId"]) for row in snapshot.for_runtime()["communities"]
    )
    partial = schema_two_one(runtime_revision_v2(2), voice="quartz")
    assert cache.install(partial, etag='"partial"').conversation_settings() == {"voice": "quartz"}


@pytest.mark.parametrize(
    "settings",
    [
        {"voice": "Not A Voice"},
        {"voice": "cove"},
        {"maxOutputTokens": 0},
        {"maxOutputTokens": "800"},
        {"idleTimeoutSeconds": 29},
        {"idleTimeoutSeconds": 121},
        {"temperature": 1},
    ],
)
def test_conversation_settings_out_of_bounds_are_refused(cache, runtime_revision_v2, settings):
    with pytest.raises(ValueError, match="invalid_configuration_snapshot"):
        cache.install(schema_two_one(runtime_revision_v2(), **settings), etag='"bounds"')


@pytest.mark.parametrize("version", ["1.0.0", "2.3.0", "3.0.0"])
def test_other_schema_versions_are_still_refused(cache, runtime_revision_v2, version):
    document = runtime_revision_v2()
    document["schemaVersion"] = version
    with pytest.raises(ValueError, match="invalid_configuration_snapshot"):
        cache.install(rehashed(document), etag='"other-version"')


RESOLVED_SETTINGS = {
    "voiceInstructions": "Synthetic voice instructions",
    "voicePlatformRules": "Synthetic voice rules",
    "voiceGlobalPrompt": "",
    "textPlatformRules": "Synthetic text rules",
    "textGlobalPrompt": "Synthetic text global",
    "quietHandover": False,
}


@pytest.mark.parametrize("version", ["2.0.0", "2.1.0", "2.2.0"])
def test_schema_2_2_0_and_the_resolved_projection_fields_are_accepted(
    cache, runtime_revision_v2, version
):
    document = schema_two_one(runtime_revision_v2(), **RESOLVED_SETTINGS)
    document["schemaVersion"] = version
    for agent in document["agents"]:
        agent["voiceAddition"], agent["textAddition"] = "", "Synthetic text addition"
    snapshot = cache.install(rehashed(document), etag='"resolved"')
    assert snapshot.schema_version == version
    runtime = snapshot.for_runtime()
    assert runtime["conversationSettings"] == RESOLVED_SETTINGS
    assert all(agent["textAddition"] == "Synthetic text addition" for agent in runtime["agents"])


@pytest.mark.parametrize(
    ("place", "field", "value"),
    [
        ("settings", "voiceInstructions", "x" * (12 * 1024 + 1)),
        ("settings", "voicePlatformRules", "x" * (8 * 1024 + 1)),
        ("settings", "textGlobalPrompt", "x" * (8 * 1024 + 1)),
        ("settings", "unknownLayer", "x"),
        ("agent", "voiceAddition", "x" * (4 * 1024 + 1)),
        ("agent", "unknownAddition", "x"),
    ],
)
def test_oversized_or_unknown_modality_fields_are_refused(
    cache, runtime_revision_v2, place, field, value
):
    document = schema_two_one(runtime_revision_v2(), **RESOLVED_SETTINGS)
    if place == "settings":
        document["conversationSettings"][field] = value
    else:
        document["agents"][0][field] = value
    with pytest.raises(ValueError, match="invalid_configuration_snapshot"):
        cache.install(rehashed(document), etag='"refused"')


def test_composed_instructions_over_36_kib_are_refused_on_load(cache, runtime_revision_v2):
    settings = RESOLVED_SETTINGS | {
        "textPlatformRules": "r" * 8 * 1024,
        "textGlobalPrompt": "g" * 8 * 1024,
    }
    document = schema_two_one(runtime_revision_v2(), **settings)
    document["agents"][0]["prompt"] = "p" * 16 * 1024
    document["agents"][0]["textAddition"] = "a" * 4 * 1024
    with pytest.raises(ValueError, match="invalid_configuration_snapshot"):
        cache.install(rehashed(document), etag='"composed"')
    document["agents"][0]["textAddition"] = "a" * (4 * 1024 - 8)
    assert cache.install(rehashed(document), etag='"composed-fits"')


def test_conversation_setup_clock_advances_utc_and_monotonic_without_sleep(conversation_clock):
    from datetime import UTC, datetime, timedelta

    origin = datetime(2026, 10, 30, tzinfo=UTC)
    assert conversation_clock.monotonic() == 0
    assert conversation_clock.utc_now() == origin
    conversation_clock.advance(600)
    assert conversation_clock.monotonic() == 600
    assert conversation_clock.utc_now() == origin + timedelta(seconds=600)


@pytest.mark.parametrize("invalid", [-1, True, float("inf"), float("nan"), "1"])
def test_conversation_setup_clock_rejects_invalid_advances(conversation_clock, invalid):
    before = conversation_clock.utc_now()
    with pytest.raises(ValueError, match="invalid_fixture_clock_advance"):
        conversation_clock.advance(invalid)
    assert conversation_clock.monotonic() == 0 and conversation_clock.utc_now() == before


def test_conversation_setup_ports_require_explicit_behavior(conversation_ports):
    import asyncio

    async def scenario():
        with pytest.raises(RuntimeError, match="fixture_model_not_configured"):
            await conversation_ports.model()
        with pytest.raises(RuntimeError, match="fixture_admission_not_configured"):
            await conversation_ports.admission()
        conversation_ports.model.side_effect = None
        conversation_ports.model.return_value = "synthetic configured result"
        assert await conversation_ports.model() == "synthetic configured result"
        assert conversation_ports.model.await_count == 2
        assert conversation_ports.admission.await_count == 1

    asyncio.run(scenario())


def test_empty_and_restarted_cache_cannot_admit_from_absent_persistence(cache, revision_document):
    from caller_runtime.domain.configuration import RevisionCache

    assert cache.current is None
    with pytest.raises(PermissionError, match="configuration_unavailable"):
        cache.pin()
    cache.install(revision_document(), etag='"active-one"')
    restarted = RevisionCache()
    assert restarted.current is None
    with pytest.raises(PermissionError):
        restarted.pin()


def test_install_is_detached_and_old_pins_do_not_mutate_when_pointer_changes(
    cache, revision_document
):
    source = revision_document()
    cache.install(source, etag='"active-one"')
    pinned = cache.pin()
    before = pinned.view_for("a-source")
    source["agents"][0]["prompt"] = "synthetic-private-mutation"
    before["agent"]["prompt"] = "synthetic-private-mutation"
    cache.install(revision_document(2), etag='"active-two"')
    assert cache.pin().revision_id == "revision-fixture-2"
    assert pinned.revision_id == "revision-fixture-1"
    assert "synthetic-private" not in repr(pinned)
    assert "synthetic-private" not in pinned.view_for("a-source")["agent"]["prompt"]


def test_validation_and_pin_use_the_same_detached_bytes(cache, revision_document, monkeypatch):
    from caller_runtime.domain import configuration

    source = revision_document()
    original_encoder = configuration.encoded

    def encode_and_detach(value):
        content = original_encoder(value)
        if value is source:
            source.clear()
        return content

    monkeypatch.setattr(configuration, "encoded", encode_and_detach)
    cache.install(source, etag='"active-one"')
    assert cache.pin().revision_id == "revision-fixture-1"
    assert cache.pin().view_for("a-source")["agent"]["prompt"].endswith("revision 1")


@pytest.mark.parametrize(
    "corruption", ["hash", "schema", "organization", "partial", "etag", "version"]
)
def test_rejected_refresh_keeps_the_last_complete_revision(cache, revision_document, corruption):
    cache.install(revision_document(), etag='"active-one"')
    before = cache.pin()
    body = revision_document(2)
    etag = '"active-two"'
    if corruption == "hash":
        body["agents"][0]["prompt"] = "synthetic-private-corruption"
    elif corruption == "schema":
        body["schemaVersion"] = "unsupported"
    elif corruption == "organization":
        body["organizationId"] = "foreign"
    elif corruption == "partial":
        body.pop("communities")
    elif corruption == "etag":
        etag = "not-quoted"
    else:
        body["revisionNumber"] = True
    with pytest.raises(ValueError):
        cache.install(body, etag=etag)
    assert cache.pin() is before


def test_revision_rollback_and_reused_identity_cannot_replace_current(cache, revision_document):
    cache.install(revision_document(2), etag='"active-two"')
    before = cache.pin()
    with pytest.raises(ValueError):
        cache.install(revision_document(1), etag='"older"')
    changed = copy.deepcopy(revision_document(2))
    changed["createdBy"] = "different-manager"
    with pytest.raises(ValueError):
        cache.install(changed, etag='"reused"')
    assert cache.pin() is before


def test_agent_view_never_exposes_foreign_prompt_or_catalog(cache, revision_document):
    cache.install(revision_document(), etag='"active-one"')
    snapshot = cache.pin()
    view = snapshot.view_for("a-source")
    assert {peer["agentId"] for peer in view["peers"]} == {"a-entry", "a-peer"}
    assert view["communities"] == [] and view["agent"]["capabilityAssignments"] == []
    assert "Synthetic prompt for b-entry" not in repr(view)
    routing = snapshot.view_for("routing")
    assert {community["communityId"] for community in routing["communities"]} == {"alpha", "beta"}
    assert all("prompt" not in community for community in routing["communities"])


@pytest.mark.parametrize("corruption", ["tool-effect", "tool-extra", "assignment", "connection"])
def test_even_consistently_hashed_invalid_catalog_graphs_are_rejected(
    cache, revision_document, corruption
):
    document = revision_document()
    if corruption == "tool-effect":
        document["tools"][0]["effect"] = "write"
    elif corruption == "tool-extra":
        document["tools"][0]["credential"] = "synthetic-private-value"
    elif corruption == "assignment":
        document["agents"][-1]["capabilityAssignments"][0]["toolId"] = "unknown-tool"
    else:
        document["tools"][0]["mcpConnectionId"] = "unknown-connection"
    graph = {
        key: document[key]
        for key in (
            "schemaVersion",
            "agents",
            "communities",
            "relationships",
            "communityConnections",
            "tools",
            "mcpConnections",
        )
    }
    document["policyHash"] = hashlib.sha256(
        json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="invalid_configuration_snapshot"):
        cache.install(document, etag='"active-one"')
    assert cache.current is None


@pytest.fixture
def client_case(revision_document):
    from caller_runtime.adapters.configuration_client import ConfigurationClient
    from caller_runtime.domain.configuration import RevisionCache

    class Credential:
        calls = []
        expires_on = time.time() + 3600

        async def get_token(self, scope):
            self.calls.append(scope)
            return SimpleNamespace(token="synthetic-runtime-token", expires_on=self.expires_on)

    class Source:
        status = 200
        body = revision_document()
        etag = '"active-one"'
        raw = None
        calls = []
        elapsed = 0.0

        async def send(self, request):
            self.calls.append(request)
            clock[0] += self.elapsed
            return httpx.Response(
                self.status,
                headers={"ETag": self.etag},
                **(
                    {"content": self.raw}
                    if self.raw is not None
                    else {"json": self.body}
                    if self.status == 200
                    else {}
                ),
            )

    source, credential, clock = Source(), Credential(), [0.0]
    cache = RevisionCache(monotonic=lambda: clock[0])
    client = ConfigurationClient(
        endpoint="https://management.example.invalid",
        audience="00000000-0000-4000-8000-000000000001",
        credential=credential,
        transport=httpx.MockTransport(source.send),
        cache=cache,
        monotonic=lambda: clock[0],
    )
    return client, source, credential, clock, cache


def test_v2_refresh_uses_only_the_authenticated_versioned_runtime_path(client_case):
    import asyncio

    client, source, credential, clock, cache = client_case
    assert asyncio.run(client.refresh())
    assert source.calls[0].url == "https://management.example.invalid/v2/runtime/configuration"
    assert len(credential.calls) == 1 and cache.pin().schema_version == "2.0.0"


@pytest.mark.parametrize(
    "elapsed,eligible", [(29.999, True), (30.0, False), (60.0, False), (-1.0, False)]
)
def test_new_admission_requires_fresh_configuration_without_discarding_old_pin(
    client_case, elapsed, eligible
):
    import asyncio

    client, source, credential, clock, cache = client_case
    assert asyncio.run(client.refresh())
    pinned = cache.pin()
    clock[0] = elapsed
    if eligible:
        assert cache.pin() is pinned
    else:
        with pytest.raises(PermissionError, match="configuration_unavailable"):
            cache.pin()
    assert cache.current is pinned
    assert pinned.view_for("a-source")["agent"]["agentId"] == "a-source"


def test_failed_refresh_keeps_old_content_without_authorizing_new_admission(client_case):
    import asyncio

    client, source, credential, clock, cache = client_case
    assert asyncio.run(client.refresh())
    pinned = cache.pin()
    source.status = 503
    clock[0] = 30
    assert asyncio.run(client.refresh()) is False
    assert cache.current is pinned
    with pytest.raises(PermissionError, match="configuration_unavailable"):
        cache.pin()


def test_matching_304_revalidates_current_snapshot_without_replacing_old_pin(client_case):
    import asyncio

    client, source, credential, clock, cache = client_case
    assert asyncio.run(client.refresh())
    pinned = cache.pin()
    clock[0] = 30
    with pytest.raises(PermissionError):
        cache.pin()
    source.status = 304
    assert asyncio.run(client.refresh())
    assert cache.pin() is pinned
    clock[0] = 59.999
    assert cache.pin() is pinned
    clock[0] = 60
    with pytest.raises(PermissionError):
        cache.pin()


def test_refresh_transport_time_cannot_extend_the_freshness_window(client_case):
    import asyncio

    client, source, credential, clock, cache = client_case
    source.elapsed = 7
    assert asyncio.run(client.refresh())
    assert clock[0] == 7
    clock[0] = 30
    with pytest.raises(PermissionError):
        cache.pin()
    source.status = 304
    assert asyncio.run(client.refresh())
    assert clock[0] == 37
    clock[0] = 60
    with pytest.raises(PermissionError):
        cache.pin()


def test_stale_snapshot_or_future_clock_cannot_revalidate_current_admission(
    runtime_revision_v2, conversation_clock
):
    from caller_runtime.domain.configuration import RevisionCache

    cache = RevisionCache(monotonic=conversation_clock.monotonic)
    older = cache.install(runtime_revision_v2(), etag='"older"')
    conversation_clock.advance(30)
    current = cache.install(runtime_revision_v2(2), etag='"current"')
    with pytest.raises(ValueError, match="configuration_revision_conflict"):
        cache.revalidate(older)
    assert cache.pin() is current
    conversation_clock.advance(30)
    with pytest.raises(ValueError, match="invalid_configuration_clock"):
        cache.revalidate(current, validated_at=61)
    assert cache.current is current
    with pytest.raises(PermissionError):
        cache.pin()


def test_refresh_coalesces_concurrent_ticks_and_authenticates_every_due_attempt(client_case):
    import asyncio

    client, source, credential, clock, cache = client_case

    async def scenario():
        assert all(await asyncio.gather(*(client.refresh() for _index in range(16))))
        assert len(source.calls) == len(credential.calls) == 1
        assert source.calls[0].headers["authorization"] == "Bearer synthetic-runtime-token"
        source.status = 304
        pinned = cache.pin()
        clock[0] += 30
        assert await client.refresh()
        assert cache.pin() is pinned
        assert source.calls[-1].headers["if-none-match"] == pinned.etag
        assert credential.calls == ["api://00000000-0000-4000-8000-000000000001/.default"] * 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["error", "redirect", "bad_json", "duplicate", "oversize", "hash", "etag", "token"]
)
def test_invalid_refresh_retains_last_good_without_permission_or_local_fallback(
    client_case, failure
):
    import asyncio

    client, source, credential, clock, cache = client_case

    async def scenario():
        assert await client.refresh()
        pinned = cache.pin()
        clock[0] += 30
        if failure == "error":
            source.status = 503
        elif failure == "redirect":
            source.status = 307
        elif failure == "bad_json":
            source.raw = b"synthetic-private-malformed-json"
        elif failure == "duplicate":
            source.raw = b'{"id":"one","id":"two"}'
        elif failure == "oversize":
            source.raw = b" " * (512 * 1024 + 1)
        elif failure == "hash":
            source.body["agents"][0]["prompt"] += "synthetic-private-mutation"
        elif failure == "etag":
            source.etag = "unquoted"
        else:
            credential.expires_on = time.time()
        assert await client.refresh() is False
        assert cache.current is pinned
        with pytest.raises(PermissionError, match="configuration_unavailable"):
            cache.pin()
        assert all(request.url.host == "management.example.invalid" for request in source.calls)

    asyncio.run(scenario())


def test_unsolicited_or_mismatched_304_does_not_authorize_a_snapshot(client_case):
    import asyncio

    client, source, credential, clock, cache = client_case

    async def scenario():
        source.status = 304
        assert await client.refresh() is False and cache.current is None
        source.status = 200
        clock[0] += 30
        assert await client.refresh()
        pinned = cache.pin()
        source.status, source.etag = 304, '"different"'
        clock[0] += 30
        assert await client.refresh() is False and cache.current is pinned
        with pytest.raises(PermissionError, match="configuration_unavailable"):
            cache.pin()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://management.example.invalid",
        urlunsplit(("https", "fixture-user:fixture-pass@management.example.invalid", "", "", "")),
        "https://management.example.invalid/override",
        "https://management.example.invalid?token=private",
    ],
)
def test_configuration_origin_is_explicit_fixed_https_before_any_client_effect(cache, endpoint):
    from caller_runtime.adapters.configuration_client import ConfigurationClient

    with pytest.raises(ValueError):
        ConfigurationClient(
            endpoint=endpoint,
            audience="00000000-0000-4000-8000-000000000001",
            credential=object(),
            cache=cache,
        )
