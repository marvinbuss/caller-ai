# Contract: Configuration Graphs and Management Views

**Version**: configuration `2.0.0`, application HTTP `/v2`.
**Status**: Implemented locally behind explicit reviewed composition; see
[source-specific evidence](../../../docs/acceptance/multimodal-agent-management.md).
Local checks do not authorize actual identity/store/model/device use or establish live acceptance.
See [data model](../data-model.md) for field rules and [test protocol](conversation.md) for
published-only execution. Existing v1 capability and security-revocation APIs remain separate.

## Management HTTP and Publication

| Endpoint | Authentication and Request | Result |
|---|---|---|
| `GET /v2/configuration` | Existing verified manager session/role | Latest complete publication and strong pointer ETag, or explicit unpublished state. Never fabricate a business community. |
| `PUT /v2/configuration` | Same manager, exact allowed Origin, existing CSRF mechanism; one strong `If-Match`; complete schema-2 graph/draft body | Validate then atomically create revision/update pointer/security authority. Return complete server revision, new ETag and recorded/pending audit status. |
| `GET /v2/runtime/configuration` | Existing bearer-only workload identity with distinct audience/role/client allowlist; optional one `If-None-Match`; no body/query | Complete validated runtime projection, 200 with ETag or 304. No manager cookies or private-container credentials accepted. |

Keep the current editor request envelope and explicit first-publication conditional semantics;
do not implement first-writer wins via wildcard/absent condition. Contract tests must lock the
existing bootstrap ETag representation before replacing endpoint versions. Request body at
most 512 KiB, existing stricter limits retained; JSON rejects duplicate keys, unknown fields,
nonfinite numbers and invalid references. No auto migration or schema-1 compatibility route.

Problems use `application/problem+json`, bounded fixed `code`, HTTP status and sanitized
correlation ID, not rejected content. Outcomes include 401 identity, 403 permission/CSRF/origin,
409 stale ETag, 413 too large, 422 invalid graph/schema/condition, 503 unavailable and 504
deadline. Missing `If-Match` is 428. Return actionable field paths without repeating prompts,
tool payloads, credentials or arbitrary attacker text. Mutation/auth/publication work shares
the existing ten-second deadline; no independent retry budget multiplies it.

Management owns all revision provenance and permission versions. Save acknowledgement applies
only to its initiating request/draft generation. Audit pending is not permission to replay
publication. On conflict, network ambiguity or validation failure, retain the draft and prior
complete publication; reconciliation reads server state rather than retrying a mutation blindly.

## Schema and Routing Semantics

The v2 complete document retains agents, communities, `relationships`, tools, MCP catalogs and
existing revision provenance. Add `communityConnections` and `layout`; use the
[data-model validation rules](../data-model.md#configuration-entities). Update management and
runtime DTOs, generated JSON Schema, graph hashes and schema-parity tests together. Keep
credential references opaque and omit credentials from every response.

Runtime projection excludes layout, draft-only state and secret resolution material while
retaining all effective routing/agent/capability rules and immutable revision identity. ETag
still tracks the complete publication, even if a layout-only revision has identical policy.
Validate the new projection as its own strict versioned DTO with `policyHash`, SHA-256 over
canonical effective policy fields, plus the management-verified full revision `graphHash`.
Do not recompute the full hash from a projection without layout. Pin revision identity and
both hashes; test source-document verification and projection-hash parity independently.

Routing initial selection sees eligible enabled communities and their entries. A later
community transfer carries its source from trusted runtime state, traverses an explicit
directed community edge, enters Routing and then the destination entry. A model/browser cannot
erase the source to turn a transfer into initial routing. Ordinary agents discover eligible
same-community peers without receiving prompts or capability grants; only their explicit
directed agent edges permit handover. Protected roles have separate lifecycle transition
policy and cannot become ordinary graph members or bypass community edges.

Publication verifies exactly one enabled global Welcome/Routing/Goodbye; membership, entry,
connection endpoint/direction, capability review and destination restrictions; and all layout
references. Unused disabled nodes remain editable but are never eligible execution targets.
Current permission/review remains intersected with pinned grants before every capability
attempt through management's existing gateway. New connection UI never grants a capability.

## UI Routes and Behavior

| Route | Contents and Controls | State Rules |
|---|---|---|
| `/configuration` | Initial communities and directed connections; create/edit/remove; graph plus keyboard/forms; publication/conflict status; protected-role and reviewed-capability editors reachable separately | One organization-wide draft; no startup model connection. |
| `/configuration/communities/[community_id]` | Selected identity, back navigation, ordinary agents and directed handovers; entry/capability controls; graph plus keyboard/forms | Stable ID lookup; unavailable/access-denied on missing or unauthorized ID; never substitute another community. |
| `/configuration/test` | Text/speech mode, start/stop/mute, devices, revision, agent/connection state, volatile messages | Published-only normal Welcome/Routing flow; page selection never overrides entry; no model allocation or microphone capture on page load. |

The graph uses a minimal Python Reflex wrapper over maintained xyflow, with drag feedback
local and one bounded final-position command. Connection gestures are validated commands,
not wholesale trusted graph replacement. Import graph CSS consistently with Tailwind and
verify client-only hooks do not access browser APIs during SSR. Lock actual compatible package
resolution through the existing Reflex-managed lock. The current implementation uses maintained
xyflow 12.12.0; the separate frontend advisory gate remains subject to current security evidence.

Give nodes, edge editors, entry selectors, tool controls and icon buttons accessible names;
retain keyboard focus after edits/conflicts. Graph operations all have equivalent forms,
including edge deletion/direction and position changes. Confirm destructive removals; never
silently cascade semantic changes from dragging. Use navigation guards for dirty reload/leave,
but preserve the existing draft for ordinary in-app community navigation. No nested cards or
new independent frontend design system. Verify 360/768/1440 layouts without page-level overflow.

## Required Contract Tests

- Schema generation/runtime projection parity, fresh schema-1 rejection and exact bootstrap
  first-publication condition; atomic pointer/revision/current-grant behavior and audit pending.
- Directed graph duplicates/self/dangling/cross-community attempts, protected-role cardinality,
  disabled entries and foreign layout IDs; dragging alone never changes permissions.
- Same-community discovery versus handover, source-preserving Routing transfers and guessed
  foreign IDs; current revocation blocks the next pinned-session capability attempt.
- Browser direct child links, stale/missing community, back/forward/reload, dirty navigation,
  conflicting managers and save acknowledgement for the right draft generation.
- Auth/Origin/CSRF, oversized/duplicate-key input, runtime audience separation, snapshot 304,
  bounded cache refresh and preservation of old publication on any rejected write.
