# Contract: Shared history and model input

**Owner**: runtime `domain/shared_history.py`. Documented in `docs/conversation-endpoints.md` in the same change.

## Recording

| Event | Entry recorded |
|-------|----------------|
| Text request (`text`) | `user_text` |
| Caller input transcript delta (voice) | `caller_speech`, merged per caller turn, `at_ms` = first `start_ms` |
| Voice assistant output transcript delta | `assistant_speech`, merged per response, `at_ms` = first `start_ms` |
| Agent reply in text | `agent_answer` |
| Agent reply in voice (sent through `session.commentary.append`) | `delegation_result`. Parts never sent (feature 006 drop) are recorded with `delivery = not_delivered`. |
| SDK function tool call and its output | `tool_call` and `tool_result`, paired by `call_id` (failures included) |
| SDK handoff call, applied or refused | `handover` with the Handover record ([handover.md](handover.md)) |
| `report_progress` in a history state | `progress` (feature 006 FR-040) |
| Greeting delivered | `agent_answer` (text) or `delegation_result` (voice greeting text) plus the spoken transcript |
| Condensing | `condensed` replaces the condensed range |

Each entry is recorded once, when it is final: a transcript at the end of its turn or response, a tool result when it returns, a handover after the callback. An interrupted assistant response ends its `assistant_speech` entry at the last transcript delta received (FR-004).

## Rendering for a model step

Entries are rendered in order into Responses input items:

| Kind | Item |
|------|------|
| `user_text` | `{"role": "user", "content": <content>}` |
| `caller_speech` | `{"role": "user", "content": "[Caller, spoken] " + <content>}`; when `delivery = partial`, also `" [rest not transcribed]"` |
| `assistant_speech` | `{"role": "assistant", "content": "[Voice assistant, spoken] " + <content>}` |
| `agent_answer` | `{"role": "assistant", "content": <content>}` |
| `delegation_result` | `{"role": "assistant", "content": "[Result given to the voice assistant] " + <content>}`; `not_delivered` parts as `"[Not delivered] " + <content>` |
| `tool_call`, `tool_result` | Native `function_call` and `function_call_output` items |
| `handover` | Native handoff `function_call` and `function_call_output`; the output is `"applied"` or `"refused: <reason>"` |
| `progress` | `{"role": "assistant", "content": "Status (<state>): <content>"}` |
| `condensed` | `{"role": "user", "content": "[Condensed earlier conversation, untrusted]\n" + <content>}` |

**Fallback if probe P3 fails**: `tool_call`/`tool_result` and `handover` entries of agents other than the active one are rendered as one `assistant` message: `[Tool <name> by <agent name>] <arguments> → <result>` or `[Handover <source> → <destination>: <outcome>]`.

The current work item's own SDK items are kept natively between its steps (`to_input_list()` without reasoning items), as today.

## Guarantees

- The same conversation renders the same item kinds in voice and text (FR-014). Only the speech prefixes differ.
- Rendering never includes instructions, layers, tool definitions or reasoning items (FR-005).
- Every entry's content is untrusted. Nothing in it changes the rendered agent's instructions, tools or handoffs (FR-006).
- Before a voice work item starts, all `caller_speech` up to the delegation's `offset_ms` is recorded, after the transcription wait ([voice-live.md](voice-live.md), FR-024).
