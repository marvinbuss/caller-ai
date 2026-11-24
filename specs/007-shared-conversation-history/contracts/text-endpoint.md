# Contract: Text endpoint and text history

**Owner**: runtime `api/text.py`, `services/text_conversation.py`, `adapters/text_history_repository.py`. Documented in `docs/conversation-endpoints.md` and `docs/runbooks/text-history.md`.

## Request

Unchanged: the open request (feature 006) and later requests carry the permit, the text and `conversationId`.

## Resume

| Stored document | Behavior |
|-----------------|----------|
| None for this owner and id | New conversation: open request → greeting → Welcome moves to Routing (R7) |
| No `historyVersion` (stored before this release) | Treated as closed: a new conversation with a new id starts as above (FR-025), and the request is answered after the greeting. The SSE stream's first event, `conversation`, carries the new `conversationId`. |
| `historyVersion: 2` | Resumed: position revalidated as today, `pendingDestination` restored, all entries loaded into the SharedHistory |

## Stream events

Existing events are unchanged (`text.delta`, `text.completed`, `progress`, `agent.changed`, `error`). New event:

| Event | Fields | Meaning |
|-------|--------|---------|
| `history.condensed` | none | Older parts were condensed before this answer (FR-016). The Test page shows a status line. |

The limit failure reuses `error` with code `conversation_context_limit` and the message "This conversation became too long. Please start a new one."

## Save

After each completed request the document is written with `historyVersion: 2` and all entries (etag-guarded replace, as today). If the document would exceed 512 KiB, condensing runs first. If it still exceeds the limit, the conversation ends with `conversation_context_limit`.
