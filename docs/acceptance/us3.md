# US3 Local Community Isolation

## Scope

T065-T075 implement and qualify local community visibility, fresh SDK transfers, atomic
session metadata, Routing and recovery. The user authorized independent local work while
telephony remains deferred. Default application startup is unchanged: these services are
explicitly composed in synthetic tests, not mounted as live call routes. There is no cloud
client construction, provider selection, resource provisioning or cross-service storage read.

This is not foundation, release or live-service acceptance. T042-T047, actual Cosmos
durability, live speech latency, native Linux/remote CI evidence and the existing tooling
virtualenv advisory remain open. No dependency ignore or security waiver was added.

## Local Evidence

Qualification uses the locked Python 3.12 backend environment on native Windows, with
outbound sockets blocked. Actual OpenAI Agents SDK sessions use an injected scripted
`RealtimeModel`; the phone is a recording double. Cosmos transactional-batch contracts use
an injected SDK-shaped double. Neither double proves live infrastructure behavior.

| Evidence | Assertions |
|----------|------------|
| Visibility, 15 cases | Complete enabled same-community peer metadata, Routing entry metadata only, protected-role restrictions, disabled destinations, guessed IDs, detached pinned snapshots |
| Isolation and filter, 15 cases | Actual fresh SDK sessions, empty destination history, summary/reason only, credential-pattern rejection, 4096-byte UTF-8 bounds, permitted directories in SDK instructions, retained phone and pinned revision |
| Races and metadata, 21 cases | Competing requests, obsolete/premature speech, caller disconnect before and after commit, failed preparation and cleanup deadlines, unavailable entry, rolling limit, metadata invariants, unavailable/ambiguous store authority |
| Atomic storage, 11 added cases | Same-partition epoch/outcome batch, conditional ETag, mismatch/stale/duplicate rejection and no partial writes in local/Cosmos-double contracts |
| Community journeys, 4 cases | Routing-mediated foreign entry, one clarification then Goodbye, ten committed transfers then controlled closure, scripted decision-to-fenced-speech timing |

The final full backend run recorded **220 passed, 13 errors**, without exclusions or skips.
All 13 errors are the existing deferred `caller_runtime.api.media` contracts. All 66 newly
added US3/atomic-storage cases pass. This mixed result is not a green full-backend gate.
Strict mypy checks all 18 production modules. Documentation validation checks 14 documents
and actually renders 11 Mermaid diagrams. The existing socket-blocking test emits its expected
blocked-egress warning; no network permission was relaxed.

```powershell
uv run --directory code/backend --locked --no-sync pytest tests/unit/test_community_visibility.py tests/integration/test_handover_isolation.py tests/unit/test_handover_races.py tests/unit/test_session_repository.py tests/integration/test_community_journeys.py tests/integration/test_sdk_lifecycle.py -q
uv run --directory code/backend --locked --no-sync pytest -q --tb=line
uv run --directory code/backend --locked --no-sync mypy src/caller_runtime
uv run --directory code/backend --locked --no-sync ruff check src tests
```

## Success Criteria

- SC-005: checked discovery, guessed-ID and direct foreign-transfer attempts disclose no
  foreign ordinary-agent objects, prompts, tools or source-history markers. Protected Routing
  receives only configured community names, descriptions and entry references. Tool execution
  is still unavailable: all SDK agents have empty tool/handoff lists pending US5.
- SC-004: all scripted successful transfers preserve session, physical phone owner, connection
  epoch and pinned revision. Eight immediate scripted transfers measure decision through
  fenced synthetic speech dispatch and assert a synthetic p95 below two seconds. This is not
  measured model speech, network latency, provider behavior or live p95 acceptance.
- Failure evidence: preparation failure/deadline retains the usable source and original epoch.
  Competing requests do not prepare another destination. Caller disconnect cancels preparation
  and ends once. A disconnect after commit retains the committed outcome and ends the new epoch.
  Ambiguous persistence never authorizes speech from either uncertain local session.

## Known Boundaries

The runtime consumes a trusted prevalidated configuration revision. Visibility additionally
checks structural identities, memberships, protected roles and relationship fields; it is not
a replacement for management's full publication validator. Routing descriptions are approved
configuration text, not inferred summaries of foreign prompts.

Transfer selection is field/type/size enforcement and recognized-credential rejection using
the explicitly injected canonical version-1 policy. It is not semantic summarization, a
complete secret detector or proof that arbitrary text labeled a summary contains no private
facts. The trusted producer must supply only the current minimal request and transfer reason.

Output fencing rereads current metadata at the physical media owner. SDK tools are disabled;
future action dispatch must enforce the same current epoch and US5 authorization. There is
no automatic live socket migration or new-owner recovery claim. A nonresponsive store/provider
can prevent durable terminal confirmation or hangup acknowledgment; local output stays fenced,
and those remote outcomes require later reconciliation/qualification, not a fabricated success.

The prepared receiver may generate internal events before commit, but no receiver speech or
action is dispatched to the phone before committed ownership. The local tests own the sole
dispatch path; a live provider composition must preserve that boundary.

## Operational Guidance

See the [community runbook](../runbooks/communities.md) for discovery, transfer fields,
deadlines, exact failure/terminal semantics and recovery diagrams. No caller content belongs
in logs, metrics, durable handover records or incident reports.
