# Data Model: Modality-Aware Agent Interaction

**Feature**: [spec.md](spec.md) | **Decisions**: [research.md](research.md)

Slice 1 adds no stored fields. Slice 2 adds optional fields to the configuration document (schema `2.2.0`, [contracts/configuration-v2.2.md](contracts/configuration-v2.2.md)).

## Stored configuration (management, Cosmos revisions)

### ConversationSettings (extended in slice 2)

Every new field is optional. **Absent means "use the current shipped default"**, and absent fields are never written. Existing fields (`voice`, `maxOutputTokens`, `idleTimeoutSeconds`) are unchanged.

| Field | Type | Limit (UTF-8) | Default when absent | Edited on |
|-------|------|---------------|---------------------|-----------|
| `voiceInstructions` | string | 1 byte to 12 KiB | Shipped voice conversation instructions | Settings › Voice |
| `voicePlatformRules` | string | 0–8 KiB (empty allowed, with a warning) | Shipped voice platform rules | Settings › Voice |
| `voiceGlobalPrompt` | string | 0–8 KiB | Shipped voice global prompt | Settings › Voice |
| `textPlatformRules` | string | 0–8 KiB (empty allowed, with a warning) | Shipped text platform rules | Settings › Text |
| `textGlobalPrompt` | string | 0–8 KiB | Shipped text global prompt | Settings › Text |
| `quietHandover` | boolean | – | `true` | Settings › Voice |

Validation:

- Strings must not contain NUL characters.
- An empty string is a deliberate edit and is stored as `""`. It is not the same as absent.
- When a platform-rules field is empty, the Test page shows a notice ("Platform rules for <modality> are empty").

### Agent (extended in slice 2)

| Field | Type | Limit | Default when absent |
|-------|------|-------|---------------------|
| `voiceAddition` | string | 0–4 KiB | no addition |
| `textAddition` | string | 0–4 KiB | no addition |

`prompt` (existing, at most 16 KiB) is unchanged. For pre-defined agents, "Restore default" sets `prompt` to the current text in `default_prompts.py` (slice 1). For layers, it removes the stored value (slice 2).

### Composition rule (runtime, slice 2)

For a working agent in modality `m` (`text` or `speech`→`voice`), the instructions are the following parts, each followed by a blank line:

1. `m` platform rules
2. `m` global prompt
3. `prompt`
4. the agent's `m` addition

Empty parts are skipped. The composed text must be at most 36 KiB. The speaking voice model never receives any of these (FR-028); it receives `voiceInstructions` plus the delegation suffix (R2).

### Schema version

| Version | Written when | Read by |
|---------|-------------|---------|
| `2.0.0`, `2.1.0` | Existing revisions | Management and runtime (unchanged) |
| `2.2.0` | Management writes it when at least one 2.2.0 field is present in the draft; otherwise it keeps the stored version | Management and runtime after slice 2 |

## Runtime projection (`GET /v2/runtime/configuration`)

From slice 2 on, the projection always carries resolved values, with management filling in shipped defaults:

- `conversationSettings` gains:
  - `voiceInstructions`;
  - `voicePlatformRules`, `voiceGlobalPrompt`;
  - `textPlatformRules`, `textGlobalPrompt`;
  - `quietHandover`.
- `agents[]` gain `voiceAddition` and `textAddition` (empty string when absent).
- `policyHash` is computed over the resolved projection.

The runtime uses its own slice 1 constants only if a field is missing from the projection. That happens only while management still runs slice 1.

## Shipped defaults (code, not stored)

| Default | Owner | Used by |
|---------|-------|---------|
| Welcome, Routing and Goodbye prompts | `management_app/services/default_prompts.py` | Bootstrap, the one-time apply command, Restore default |
| Voice conversation instructions, voice and text platform rules, voice and text global prompts | `management_app/services/default_prompts.py` | Settings display and restore, projection defaults |
| Voice conversation instructions, voice and text platform rules (slice 1 copies) | `caller_runtime/services/modality_defaults.py` | GPT-Live start and agent instructions in slice 1; a contract test asserts they equal the management defaults |
| Greeting inputs (voice and text), text fallback greeting | `caller_runtime/services/greeting.py` | The runtime only; not editable. The greeting text itself is written by the Welcome agent |
| Quiet handover text, dropped-parts prefix | `caller_runtime/services/speech_parts.py` | The runtime only |

## Session-scoped runtime state (not stored beyond today's records)

### Greeting state (per test session)

| Field | Meaning |
|-------|---------|
| `greeted` | `False` until a greeting has been delivered or skipped; never reset within the session |
| `ready_at` | When the session became ready; the start for `greeting_seconds` |
| `first_user_input_at` | Voice only: first verified caller speech or delegation; set before a greeting means the greeting request is cancelled and skipped (FR-004). In text the greeting always comes first, and an early message is answered right after it |

Transitions:

```text
pending ──(greeting delivered)──▶ greeted
pending ──(voice only: caller speech or delegation first)──▶ skipped
pending ──(5 s passed, voice)───▶ missed (logged, no retry)
```

### Spoken reply parts (per speech work item)

| Field | Meaning |
|-------|---------|
| `parts` | Ordered parts of at most 500 bytes |
| `next` | Index of the next part to send |
| `awaiting_ack` | `client_event_id` of the last sent part |
| `dropped` | Set when user speech or a new delegation interrupts; the remaining text goes into one quiet note of at most 1,500 bytes |

### Progress update (per work item, slice 2)

| Field | Values / limit |
|-------|----------------|
| `state` | `still_working`, `waiting_for_input`, `completed`, `failed`, `cancellation_confirmed` |
| `message` | 1–200 characters, plain text |
| `task_id` | The work item it belongs to; dropped if no longer current (FR-024) |
| `last_state`, `last_still_working_at` | Rate limit (FR-025): deliver on a state change; repeat `still_working` at most once per 10 s |
| `final_reported` | Set by `completed`, `failed` or `cancellation_confirmed`; when unset at failure, the fixed failure line is used (FR-023) |

Delivery:

- **Text**: a `progress` stream event. The final and waiting states are appended to the executor history as "Status (<state>): <message>" (FR-040).
- **Voice**: `session.commentary.append` (delegation ID, at most 500 bytes) plus a `progress` control event to the browser.

### Text conversation history (existing store)

The open request stores the greeting as the first assistant turn. The existing size limits apply. No new container or field is needed: the turn has the same shape as today's assistant turns.

## Formatted text tree (management relay → browser, slice 1)

| Node | Children | Attributes |
|------|----------|-----------|
| `p`, `h1`–`h3`, `li`, `blockquote`, `th`, `td`, `strong`, `em` | nodes or text | none |
| `ul`, `ol`, `table`, `thead`, `tbody`, `tr` | nodes | none |
| `pre`, `code` | text | none |
| `a` | text | `href` (string; the browser keeps it only if https) |
| `img` | none | `src` (string; the browser keeps it only if https), `alt` (string) |
| `br`, `hr` | none | none |

The tree is limited to 2,000 nodes and depth 12, with text nodes of at most 64 KiB in total. Anything else, including any node type not listed, falls back to the plain-text row.
