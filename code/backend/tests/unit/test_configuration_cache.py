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
            "tools",
            "mcpConnections",
        )
    }
    document["graphHash"] = hashlib.sha256(
        json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="invalid_configuration_snapshot"):
        cache.install(document, etag='"active-one"')
    assert cache.current is None


@pytest.fixture
def client_case(cache, revision_document):
    from caller_runtime.adapters.configuration_client import ConfigurationClient

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

        async def send(self, request):
            self.calls.append(request)
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
    client = ConfigurationClient(
        endpoint="https://management.example.invalid",
        audience="00000000-0000-4000-8000-000000000001",
        credential=credential,
        transport=httpx.MockTransport(source.send),
        cache=cache,
        monotonic=lambda: clock[0],
    )
    return client, source, credential, clock, cache


def test_refresh_coalesces_concurrent_ticks_and_authenticates_every_due_attempt(client_case):
    import asyncio

    client, source, credential, clock, cache = client_case

    async def scenario():
        assert all(await asyncio.gather(*(client.refresh() for _index in range(16))))
        assert len(source.calls) == len(credential.calls) == 1
        assert source.calls[0].headers["authorization"] == "Bearer synthetic-runtime-token"
        source.status = 304
        clock[0] += 30
        pinned = cache.pin()
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
        assert cache.pin() is pinned
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
        assert await client.refresh() is False and cache.pin() is pinned

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
