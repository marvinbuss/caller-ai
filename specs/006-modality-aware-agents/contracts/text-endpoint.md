# Contract: Text conversation endpoint changes

**Endpoint**: `POST /v1/conversations/text` (runtime). The base contract is in `docs/conversation-endpoints.md`, which is updated in the same change.

## Open request (slice 1)

A request **without** `conversationId` and **without** `text` opens a new conversation and returns the greeting (FR-002):

```json
{"permit": "<signed start permit>", "requestId": "request-1", "settings": {}}
```

```text
event: conversation
data: {"conversationId": "c1"}

event: text.delta
data: {"messageId": "m0", "text": "Hello and welcome! …"}

event: text.completed
data: {"messageId": "m0", "text": ""}
```

**Greeting source.** The greeting is written by the Welcome agent (low reasoning effort, no tools, at most 256 output tokens). If no complete greeting is ready within 4.0 s of the request arriving, the fixed fallback greeting is streamed instead (FR-003). In both cases the greeting is stored as the first assistant turn (FR-039).

**Refused combinations.**

| Request | Result |
|---------|--------|
| `conversationId` without `text` | Refused with `invalid_request` |
| `text` without `conversationId` | Starts a conversation **without** a greeting, as today. This is kept only for backward compatibility; clients that want the greeting send the open request first |

## Progress event (slice 2)

During a turn the stream may include:

```text
event: progress
data: {"messageId": "m3", "state": "still_working", "text": "Checking the order status."}
```

- `state` ∈ `still_working`, `waiting_for_input`, `completed`, `failed`, `cancellation_confirmed`.
- `text` is 1–200 characters of plain text.
- `messageId` is the turn's message ID.

Progress events never replace `text.completed`; a turn still ends with `text.completed` or `error`. Clients that do not know `progress` must ignore it. The management relay is updated in the same change.

## Unchanged

- One request is one turn.
- Error codes and size limits stay as they are.
- History stays server-side.
