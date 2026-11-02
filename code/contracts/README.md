# Foundation Contracts

Current maintained guidance is the root [configuration guide](../../docs/configuration-guide.md),
[architecture](../../docs/architecture.md) and [setup](../../docs/setup.md). Feature contracts
remain under `specs/`; historical results retain their original scope and qualification gaps.

`foundation.openapi.json` is the implementation-owned copy of the accepted health interface
under `specs/001-call-center-agents/contracts/`. Its initial contract version is 1.0.0.

Both applications must satisfy its liveness/readiness schemas. A local ready response does
not certify live call handling or management configuration capability. Neither application
may expose credentials, external endpoint details, or raw dependency errors through probes.

Contract changes require review of the design source, both application consumers, contract
tests and documentation in the same change. Breaking changes require an explicit version
and compatibility decision; do not silently diverge the implementation-owned copy.

## Configuration V2

Feature-002 application alert events use fixed `test.alert` reasons `cleanup_uncertain`,
`dependency_failures` and `latency_high`, with bounded service-owned windows and no content.
They are not production telephone-call counters or proof of approved sink delivery. See
[monitoring and evidence-only recovery](../../docs/runbooks/monitoring.md#feature-002-application-alerts).

[configuration.schema.json](configuration.schema.json) is generated from the management-owned
[revision model](../management/management_app/models/configuration.py). It describes complete
schema `2.0.0` publications: global protected roles, ordinary community membership, directed
ordinary-agent and community connections, reviewed capabilities and presentation-only layout.
The fresh-start contract rejects schema 1; there is no compatibility translation or reset.
The independent health contract above stays at its existing version.

Authenticated managers use `GET/PUT /v2/configuration`. Workloads use the separate bearer-only
`GET /v2/runtime/configuration`; manager cookies do not grant runtime access. Removed v1
configuration paths are not aliases. Existing capability/revocation APIs remain v1. A bootstrap
contains protected roles only, not a fabricated business community or permission to converse.

Manager responses preserve the complete publication and its strong active-pointer ETag.
Mutation requires one exact `If-Match`: missing is 428, malformed/wildcard/weak is 422, stale is
409. A failed write retains the earlier publication; audit pending acknowledges a committed
write and requires reconciliation, not mutation replay. Authentication, validation, publication
and bounded audit/denial work share the original ten-second request deadline.

Management verifies the full canonical `graphHash` before either serialization. Runtime
receives provenance and that verified hash, but no layout or MCP `credentialRef`. Its separate
`policyHash` covers the canonical projected policy only. The runtime verifies `policyHash`;
it cannot reconstruct the full hash from missing fields. Matching authenticated ETag reads
return bodyless 304. New runtime admission requires a validated publication younger than
30 seconds and an enabled eligible business community. A 304 revalidates only the exact current
snapshot from request acceptance time; retained stale snapshots never grant new admission.

Problems use `application/problem+json`, a sanitized bounded correlation ID and fixed codes:
`authentication_required`, `permission_denied`, `configuration_conflict`,
`configuration_too_large`, `invalid_configuration`, `invalid_snapshot_request`,
`precondition_required`, `configuration_unavailable` and `configuration_deadline_exceeded`.
Validation issues may additionally use existing fixed credential/reference/publication codes;
their field paths contain no rejected values. Dependency bodies, endpoints and secrets never
become diagnostics. Source-specific codes remain owned by the two configuration API modules.

## Metadata-Only Activity

Both independently packaged [runtime](../backend/src/caller_runtime/observability.py) and
[management](../management/management_app/observability.py) telemetry retain UTC timestamp,
severity, service/environment, fixed event/outcome, bounded correlation ID and trace/span IDs.
Trace propagation forwards `traceparent` only, not baggage or exception content. Existing
bounded buffers and no-default-network-export behavior remain unchanged.

Configuration activity supports `configuration.refresh`, `configuration.publish` and the
`configuration` event. Shared test spans/events support `test.admission`, `test.session`,
`test.work`, `test.cleanup` and `test.relay`. Test events require `mode` (`text` or `speech`)
and a fixed `reason_code`. Allowed reasons are `ready`, `stopped`, `denied`, `unavailable`,
`deadline`, `idle_expired`, `lifetime_expired`, `auth_expired`, `cleanup_pending`,
`protocol_error`, `revoked`, `stale_owner`, `stale_generation`, `invalid_input` and `quota`.
Span tags use the same closed mode/reason vocabulary and phases `starting`, `ready`, `active`,
`ending`, `terminal` or `cleanup_pending`. Outcomes remain `success` or `failure`.

Runtime also supports fixed `test.delegation`, `test.denial`, `test.usage` and `test.latency`
events. Numeric event fields are optional `measurement` and string-encoded finite `value`,
never labels supplied by models/providers/users. Usage measurements are `input_tokens`,
`output_tokens`, `voice_seconds_snapshot` and `voice_seconds_final`, bounded to nonnegative
values at most 2^31; token counts are integers. Latency measurements are
`backend_first_text_seconds`, `work_seconds`, `cleanup_seconds` and `admission_seconds`,
bounded to 600 seconds. Missing usage remains an unavailable event without a numeric value,
not zero. Voice snapshots retain the cumulative maximum rather than being summed; final usage
requires an authenticated close event. Backend text timing is not audible-latency evidence.
Browser tests do not update production call/admission/handover accounting.

Only fixed route strings are accepted; metrics collapse unknown paths to `unmatched` rather
than recording identifiers. Prompts, audio, transcripts, permits, provider bodies, tool payloads
and actor/session IDs are not accepted trace fields or metric labels. These vocabulary/privacy
contracts prepare later test-session emitters; they do not prove a running conversation,
real provider/device behavior, alert delivery or live acceptance.

## Conversation Capability Attempts

Conversation work uses the authenticated `POST /v1/runtime/capabilities/execute` gateway with
`singleAttempt: true`. This optional strict boolean defaults to the existing bounded behavior
for earlier callers; it is transient request control, not durable admission content. A single
attempt retains the original work `deadlineAt` through current/reviewed/pinned grant checks
and the ordered revocation admission. Retryable dependency errors do not replay conversation
work. The runtime confirms its own owner/generation-bound intent before dispatch and does not
release uncertain capacity after cancellation, transport failure or ambiguous 5xx outcomes.
