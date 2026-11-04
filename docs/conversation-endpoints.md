# Conversation endpoints

The backend runtime serves two public conversation endpoints and two operational probes. They
share one connection contract, so a caller handles errors, limits and settings the same way for
text and voice. Everything else a caller might try (the retired call, provider-callback, drain and
development routes, and the earlier `/v1/internal/test-sessions` WebSocket) is not found.

| Purpose | Method and path | Transport |
|---------|-----------------|-----------|
| Text turn | `POST /v1/conversations/text` | HTTP request, `text/event-stream` response |
| Voice session | `GET /v1/conversations/voice` (upgrade) | WebSocket, subprotocol `caller.voice.v1` |
| Liveness and readiness | `GET /healthz`, `GET /readyz` | HTTP, operational only |

Both conversation endpoints require TLS, accept no query string and are reached by the
management console, which relays the browser session; browsers never hold the workload credential
or call the backend directly.

```mermaid
sequenceDiagram
    participant B as Browser
    participant M as Management console
    participant R as Backend runtime
    participant H as History store
    participant P as Model provider

    Note over B,P: Text turn (one request is one turn and one slot)
    B->>M: message
    M->>M: sign single-use start permit (text)
    M->>R: POST /v1/conversations/text (Authorization, permit in body)
    R->>R: verify workload and permit, take one slot
    R->>H: load owner's conversation when conversationId is set
    R->>P: stream model turn
    R-->>M: event conversation, text.delta, text.completed
    R->>H: append turn (etag-conditioned, item expiry)
    R->>R: verified cleanup, release slot
    M-->>B: streamed reply

    Note over B,P: Voice session (speech only, nothing stored)
    B->>M: open voice session
    M->>R: upgrade /v1/conversations/voice (Authorization, caller.voice.v1)
    M->>R: authenticate (signed permit, optional empty settings)
    R->>R: verify, take one slot
    R-->>M: ready, audio.credit
    M->>R: audio frames, input.mute, output.interrupt
    R->>P: live audio session
    R-->>M: audio frames, transcript.delta
    M->>R: stop
    R->>R: closure confirmed, release slot
    R-->>M: ended, close 1000
```

## Shared connection contract

- **Workload credential.** The caller sends its Entra workload token in `Authorization: Bearer
  ...`. The backend verifies it before reading the body or accepting a WebSocket.
- **Start permit.** The management console signs a short-lived, single-use RS256 permit per turn
  or per voice session. It carries the manager identity, the mode (`text` or `speech`) and the
  admission deadline, and is valid for at most 30 seconds. A text request carries it in the JSON
  body; a voice session sends it in the first WebSocket message. A permit of the other mode is
  refused with `forbidden` and reason `mode_mismatch`.
- **Slots.** Each text request and each voice session takes one admission slot, and a manager has
  at most one in flight. The slot is released only after cleanup has been verified, so a refused
  or failed request never leaves capacity occupied. When all slots are in use the next request
  fails with `capacity_unavailable` and active sessions are untouched.
- **Settings.** The optional `settings` field must be absent, `null` or `{}`. Any other value is
  refused with `settings_not_supported` before a slot is taken. No setting exists today; the field
  reserves the extension point for both endpoints.
- **Limits.** Control messages and the text request body are bounded (8 KiB control frames, a
  16 KiB text request, 4 KiB of user text), a reply is at most 64 KiB, and a conversation's
  retained context is at most 256 KiB. Voice audio frames are at most 1,920 bytes of PCM and flow
  under credits. Violations fail with `limit_exceeded`.
- **Errors.** One vocabulary of eight public codes, each with a fixed `reason` from a closed list.
  Details (store errors, provider bodies, verification details) are never returned.

| Code | HTTP | WebSocket close | Reasons |
|------|------|-----------------|---------|
| `unauthorized` | 401 | 4401 | `permit_invalid`, `permit_expired`, `permit_replayed`, `session_expired` |
| `forbidden` | 403 | 4403 | `mode_mismatch`, `insecure_transport`, `session_terminal`, `session_stale`, `handover_not_allowed` |
| `settings_not_supported` | 422 | 4422 | none |
| `limit_exceeded` | 413 | 4413 | `control_limit`, `frame_limit`, `text_reply_limit`, `conversation_context_limit`, `transcript_limit`, `audio_credit_exhausted`, `sequence_exhausted`, `work_deadline_exceeded`, `work_limit` |
| `capacity_unavailable` | 503 | 4503 | `capacity_unavailable`, `manager_busy`, `work_busy` |
| `invalid_request` | 400 | 4400 | `invalid_message`, `invalid_audio`, `conversation_not_found`, `not_ready` |
| `upstream_unavailable` | 502 | 4502 | `authority_unavailable`, `configuration_unavailable`, `clock_unavailable`, `model_unavailable`, `capability_unavailable`, `history_unavailable`, `runtime_unavailable` |
| `internal_error` | 500 | 4500 | none |

HTTP errors are `{"error": {"code": "...", "reason": "..."}}` (`reason` is omitted when the code
has none). Authentication failures expose only the four `unauthorized` reasons, so a caller
cannot learn why a credential or permit was rejected. The mapping from the backend's internal
codes to these public values is one table in `code/backend/src/caller_runtime/api/connection.py`;
an internal code missing from the table becomes `internal_error` with no reason, and a test fails
when a live code is unmapped.

## Text endpoint

```json
{
  "permit": "<signed start permit>",
  "requestId": "request-1",
  "conversationId": "optional id returned by an earlier turn",
  "text": "user message",
  "settings": {}
}
```

The caller never sends history. A request with a `history` field or any other unknown field fails
with `invalid_request`. `text` must be non-blank and at most 4,096 bytes.

A failure before the first event is a plain JSON error with the HTTP status above. Otherwise the
response is `200` with `Content-Type: text/event-stream` and `Cache-Control: no-store`:

```text
event: conversation
data: {"conversationId": "c1"}

event: text.delta
data: {"messageId": "m1", "text": "partial reply"}

event: text.completed
data: {"messageId": "m1", "text": ""}
```

`text.delta` events carry the reply in order; `text.completed` ends the turn and carries an empty
`text`. A failure after the stream has started is one final `event: error` with
`data: {"code": "...", "reason": "..."}`, then the stream ends. One request is one turn.

### Stored history

The backend stores each text conversation under the manager's owner key (see
[text history](runbooks/text-history.md)). Without `conversationId` a new conversation starts and
its id is the first event; with it the turn continues from the stored conversation.

- An unknown, expired or another manager's `conversationId` fails identically with
  `invalid_request` and reason `conversation_not_found`.
- Only user and assistant text is stored. A completed turn appends both messages with an
  etag-conditioned update; a failed turn leaves the stored conversation unchanged. A concurrent
  update fails with `capacity_unavailable` and reason `work_busy`.
- If the stored conversation or the new text would exceed the 256 KiB context limit, the request
  fails with `limit_exceeded` and reason `conversation_context_limit`.
- If the history store cannot be read, the request fails closed with `upstream_unavailable` and
  reason `history_unavailable` and no reply is generated. If it cannot be written after the reply
  streamed, nothing is stored and the stream ends with the same error as its final event.
- Each stored conversation expires 30 days after its last turn; expiry is the only deletion path.
- `/readyz` reports `not_ready` with the reason `history_store_unavailable` when the background
  check of the store (about every 10 seconds) is failing or older than 30 seconds.

## Voice endpoint

The client connects with the subprotocol `caller.voice.v1` (exactly once) and the workload
credential. A refusal before the upgrade is an HTTP denial. After the upgrade, the first message
must arrive within 3 seconds:

```json
{ "type": "authenticate", "permit": "<signed start permit>", "requestId": "request-1", "settings": {} }
```

Admission then has at most 10 seconds. The server answers `ready` (revision, agent, expiry,
bounds and the audio format `pcm16le`, 24 kHz, mono) and starts sending `audio.credit` messages.

| Direction | Messages |
|-----------|----------|
| Client to server (text frames, each with `sequence` and `sessionId`) | `input.mute`, `output.interrupt`, `audio.played`, `ping`, `pong`, `stop` |
| Client to server (binary frames) | 9-byte header (version, generation, sequence) plus up to 1,920 bytes of PCM |
| Server to client (text frames) | `ready`, `audio.credit`, `transcript.delta`, `generation.changed`, `agent.changed`, `command.ack`, `ping`, `pong`, `error`, `ended` |
| Server to client (binary frames) | the same 9-byte header plus up to 960 bytes of PCM |

Text submission is not part of the voice protocol. Any failure sends one `error` message
(`{"type": "error", "code": "...", "reason": "..."}`) and closes with the matching 4xxx code. A
normal `stop` ends with `ended` (`cleanupStatus` is `complete` once the slot is released) and
close code 1000. Voice sessions store nothing; audio, transcripts and provider bodies stay
volatile and are disposed when the session closes.

## Observability

Both endpoints emit the same event names with a `kind` attribute (`text` or `voice`). Events carry
outcome, fixed reason and bounded measurements only: never content, credentials or provider
bodies. The history store emits `history.store` with outcome and `store_seconds`.

## Provider constraints

Voice uses the GPT-Live client delegation only: no Azure Voice Live, no Responses delegation and
no public-OpenAI or legacy-realtime fallback. Text uses the approved Foundry deployment.
