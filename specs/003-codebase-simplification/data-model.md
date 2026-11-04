# Data Model: Codebase Simplification

This feature adds one persistent entity, Text Conversation History. Everything else describes the contracts and artifacts the cleanup produces or constrains. Field names are design-level; wire details are in [conversation-endpoints](contracts/conversation-endpoints.md).

## Conversation Endpoint

| Field | Rules |
| --- | --- |
| `kind` | `text` or `voice`; exactly one endpoint per kind (FR-001, FR-004) |
| `path` | `/v1/conversations/text` and `/v1/conversations/voice` |
| `transport` | text: HTTP POST with a server-sent-event response; voice: WebSocket with subprotocol `caller.voice.v1` |
| `authentication` | signed manager start permit validated independently by the backend |
| `connection_contract` | shared with the other kind; see Connection Request |

Invariant: no second endpoint exists for the same purpose. Removed paths must return not-found (verified by regression tests).

## Connection Request

Shared by both endpoints and by every caller (management gateway, future client app).

| Field | Rules |
| --- | --- |
| `permit` | signed manager start permit, required; carries manager identity, mode, revision binding and the admission and expiry deadlines; single-use and valid for at most 30 seconds, so every text request needs its own, minted server-side by the console and never shown to the browser; sent as the bearer credential on text and in the first `start` message on voice |
| `settings` | optional object; MUST be absent or empty; non-empty is rejected with `settings_not_supported` (clarification Q4) |
| `conversationId` | text only: optional; absent starts a new conversation and the first stream event returns the new identifier; present continues the stored conversation owned by the permit's manager |
| `slot` | each text request or voice session takes one admission slot, released only after verified cleanup; one in-flight test per manager |
| `limits` | bounded message size, frame size and turn time, identical across endpoints |

State transitions (voice): `connecting` -> `authenticated` -> `admitted` -> `active` -> `closing` -> `closed`. Any failure before `active` closes with a stable code and no partial session.

## Text Conversation History

Stored in the backend-owned Cosmos container `text-conversations`; the only conversation content the system persists (spec FR-028 to FR-031).

| Field | Rules |
| --- | --- |
| `id` | server-assigned opaque conversation identifier |
| `ownerKey` | partition key; hash of tenant and manager from the verified permit; only that owner can read or write the item |
| `turns` | ordered user and assistant text turns; total size at most 256 KiB (existing context limit); appended only after a completed turn |
| `createdAt`, `updatedAt` | UTC timestamps |
| `ttl` | 2,592,000 seconds (30 days) written on every update, so expiry is 30 days after the last turn |
| `_etag` | optimistic concurrency token used for every update |

Invariants: text only (never voice transcripts or audio); never appears in logs, metrics, traces, error bodies, alerts, admission or authority records or any other store; unknown, expired and foreign identifiers are indistinguishable to the caller; an agent change resets the stored context to the authorized transfer context; expiry is the only deletion path in this feature; an unavailable store fails the request closed with reason `history_unavailable`.

## Error Response

| Field | Rules |
| --- | --- |
| `code` | one of eight public codes: `unauthorized`, `forbidden`, `settings_not_supported`, `limit_exceeded`, `capacity_unavailable`, `invalid_request`, `upstream_unavailable`, `internal_error` |
| `reason` | lowercase value from a fixed list per code (for example `conversation_not_found` under `invalid_request`, `conversation_context_limit` under `limit_exceeded`, `history_unavailable` under `upstream_unavailable`); omitted for `internal_error`; authentication failures use only `permit_invalid`, `permit_expired`, `permit_replayed`, `session_expired` |

Invariant: every internal code raised by live backend code maps to exactly one public code and `reason` through one table; an unmapped code fails a test.

## Connection Settings (extension point)

An empty, reserved object on the Connection Request. No keys are defined in this feature. Future per-connection settings add keys here and in the contract without a new endpoint or caller-specific path.

## Test Console Connection

The management console's session to the backend.

| Field | Rules |
| --- | --- |
| `browser_session` | browser to management WebSocket, authenticated by the console |
| `backend_connection` | created through the single connection-request builder against the public endpoints |
| `ticket` | short-lived console-issued ticket binding browser to relay (existing mechanism, renamed) |

Invariant: the console holds no backend-only code path; its relay and any other client are indistinguishable to the backend.

## Environment Configuration

| Field | Rules |
| --- | --- |
| `backend_file` | backend-config file (HCL or JSON), one per stack (`bootstrap`, `platform`), holding state location, that stack's own state `key` and identity; the only source of backend and state-key data |
| `variable_file` | variable file (HCL or JSON) holding every environment-specific value |
| `label` | optional free-form text used for naming and tags only; never branched on |

Invariant: the Terraform configuration contains no condition on an environment name. Adding an environment means adding a backend file and a variable file and nothing else.

## Workflow Hook

| Field | Rules |
| --- | --- |
| `event` | Spec Kit lifecycle event (for example before specify, after tasks) |
| `extension` / `command` | bundled `git`, bundled `github`, or the repository-local push/pull-request command |
| `optional` | always true; each hook prompts before acting |
| `skip_reason` | recorded when `git`, `gh` or authentication is missing |

Invariant: no hook runs without user confirmation and none is a GitHub Actions trigger.

## Baseline Measurement

| Field | Rules |
| --- | --- |
| `metric` | lines, module count, endpoint count, test count, step duration |
| `before` | value captured in [research.md](research.md) |
| `after` | value recorded at delivery |
| `target` | advisory (25 percent, 600 lines, 50 percent, 3 minutes); a miss needs a recorded reason |

## Library Decision Record

| Field | Rules |
| --- | --- |
| `area` | the capability affected |
| `decision` | official library adopted, or custom code retained |
| `reason` | required whenever custom code is retained (FR-012) |
