# Contract: Conversation Endpoints

Supersedes the internal test-session WebSocket from feature 002 (`/v1/internal/test-sessions`, subprotocol `caller.test.v1`). Wire details here are planning decisions; message payloads for turns and audio keep their existing shapes and are only renamed where noted.

## Endpoint inventory

| Purpose | Method and path | Transport |
| --- | --- | --- |
| Text interaction | `POST /v1/conversations/text` | HTTP POST, `text/event-stream` response |
| Voice interaction | `GET /v1/conversations/voice` (upgrade) | WebSocket, subprotocol `caller.voice.v1` |
| Liveness / readiness | `GET /healthz`, `GET /readyz` | HTTP (operational, not conversational) |

Removed and expected to return not-found: `/v1/internal/test-sessions`, `/v1/calls/{id}/media`, `/v1/provider/callbacks`, `/v1/operations/drain/*`, `/v1/dev/transport`.

## Shared connection contract

Applies identically to both conversation endpoints.

- **Authentication**: the signed manager start permit issued by the management console (RS256, fixed verification keys, bounded admission and expiry deadlines). It is sent in the JSON body on text (`permit`, with `requestId`) and in the first `authenticate` message on voice, while the management workload credential travels in `Authorization: Bearer ...` on both; the backend verifies both independently of any caller. A missing, invalid or expired permit fails with `unauthorized` before any model or storage access. The permit carries the manager identity, mode and revision binding, so authority stays server-side.
- **Slots**: each text request and each voice session takes one admission slot, released only after verified cleanup. A manager has at most one in-flight test; further attempts fail with `capacity_unavailable`.
- **Settings**: request field `settings`. Absent or `{}` is accepted. Any other value is rejected with `settings_not_supported` and no session is created.
- **Limits**: one message-size limit, one WebSocket frame limit (256 KiB retained), one turn timeout. Violations fail with `limit_exceeded`.
- **Errors**: one vocabulary, `unauthorized`, `forbidden`, `settings_not_supported`, `limit_exceeded`, `capacity_unavailable`, `invalid_request`, `upstream_unavailable`, `internal_error`. HTTP maps them to 401, 403, 422, 413, 503, 400, 502, 500 respectively. WebSocket maps them to an `error` message followed by a close code in the 4000 range, one code per error. Every error also carries a `reason`: a lowercase fixed value from a documented closed list per code (for example `permit_expired` and `permit_replayed` under `unauthorized`, `work_busy` under `capacity_unavailable`, `conversation_context_limit` under `limit_exceeded`). Authentication failures expose only `permit_invalid`, `permit_expired`, `permit_replayed` and `session_expired` so verification detail is not revealed. The existing internal admission and conversation codes (about sixty) are grouped under the eight public codes by one mapping table in `api/connection.py`; any raised code missing from the table becomes `internal_error` with no `reason`, and a test fails when a live code is unmapped. Details stay in telemetry only. HTTP bodies are `{"error": {"code": "...", "reason": "..."}}`.
- **Observability**: both endpoints emit the same telemetry names with a `kind` attribute (`text` or `voice`); no content or credentials are recorded.

## Text endpoint

Request:

```json
{ "permit": "<signed start permit>", "requestId": "request-1", "conversationId": "optional-existing-id", "text": "user message", "settings": {} }
```

The caller never sends history; a request carrying a `history` field (or any unknown field) fails with `invalid_request`. The backend stores each text conversation per owner (see [data-model.md](../data-model.md), Text Conversation History). Without `conversationId` a new conversation starts; with it the reply continues from the stored history. The request is bounded by the shared message-size limit.

Response `200` with `Content-Type: text/event-stream`:

```text
event: conversation
data: {"conversationId": "c1"}

event: text.delta
data: {"messageId": "m1", "text": "partial reply"}

event: text.completed
data: {"messageId": "m1", "text": ""}
```

`text.delta` carries the reply and `text.completed` is the end marker with an empty `text`. A failure after the stream has started is sent as one final `event: error` with a `code` and `reason` from the shared vocabulary, then the stream ends. One request is one turn and one slot.

History rules: a completed turn appends the user and assistant messages to the stored conversation with an optimistic-concurrency update; a failed turn leaves the stored history unchanged; an agent change resets the stored context to the authorized transfer context; exceeding the context limit fails with `limit_exceeded` reason `conversation_context_limit`; an unknown, expired or another manager's `conversationId` fails with `invalid_request` reason `conversation_not_found` and the three cases are indistinguishable; an update conflict fails with `capacity_unavailable` reason `work_busy`. If the history store cannot be read or written, the request fails closed with `upstream_unavailable` reason `history_unavailable`: no reply is generated without history, nothing is written and the slot is released after verified cleanup. A background check (a sentinel read about every 10 seconds) caches the store's health; `/readyz` reads only the cached value and reports not ready with the fixed reason `history_store_unavailable` when the result is bad or older than 30 seconds. A content-free `history.store` telemetry event records outcome and latency. Each stored conversation expires 30 days after its last turn, and expiry is the only deletion path. Voice sessions store nothing.

## Voice endpoint

Client messages: `authenticate` (first message, carries `permit`, `requestId` and optional `settings`), binary PCM frames, `input.mute`, `output.interrupt`, `audio.played`, `ping`, `pong`, `stop`. Server messages: `ready`, `audio.credit`, `transcript.delta`, `generation.changed`, `agent.changed`, `command.ack`, `ping`, `pong`, `error`, `ended`, plus binary PCM frames. Payloads follow the feature 002 test-session messages (text submission is removed); only the subprotocol, path and error shape change. A rejection before the upgrade (missing workload credential, plain `ws`, wrong subprotocol) is an HTTP denial; every later failure sends one `error` message and closes with the mapped 4xxx code. See [docs/conversation-endpoints.md](../../../docs/conversation-endpoints.md) for the maintained version.

Connection states and close behavior follow [data-model.md](../data-model.md): any failure before `active` closes with the mapped code and creates no session. Closure is confirmed before capacity is released (existing closure-confirmation guarantee retained).

## Provider constraints (unchanged from feature 002)

GPT-Live client delegation only. No Azure Voice Live, no Responses delegation, no public-OpenAI or legacy-realtime fallback.

## Verification

- Contract tests per endpoint for success, each error code, oversized input, missing or expired permit and `settings` rejection.
- Saturation tests: when every slot is in use the next request or session fails with `capacity_unavailable` without disturbing active ones, queues stay bounded, and slots are released only after verified cleanup. The documented targets are the existing bounded values (slots per partition, one in-flight test per manager, message and frame limits).
- Regression test: each removed path returns not-found.
- History tests: a follow-up request continues from stored history; foreign, unknown and expired identifiers are refused identically; a `history` field is rejected; an unavailable store fails closed with `history_unavailable` and writes nothing; item `ttl` and owner key are set; a failed turn writes nothing; conversation text never appears in logs, metrics, traces, error bodies or any other store; voice writes nothing to the history store.
- Reachability check: no production module imports retired call-era code.
