# Research: Modality-Aware Agent Interaction

**Feature**: [spec.md](spec.md) | **Date**: 2026-11-13

**Inputs**:

- The assessment `.specify/assessments/agent-interaction-enhancements/` (intake, research including the int probe of 2026-11-12, problem, concept, decision).
- The current code under `code/backend` and `code/management`.
- The GPT-Live guidance pages recorded in the assessment research: live-conversations, live-delegation, live-prompting, and the Foundry gpt-live-delegation page.

The spec has no open `NEEDS CLARIFICATION`. Each decision below answers a design unknown from the plan's Technical Context.

## R1: Voice greeting sequence

**Decision** (revised 2026-11-13 by the requester): the **Welcome agent writes the greeting text** and the speaking model says it (FR-001).

1. When the voice session becomes ready (generation 1 only), the runtime starts one greeting request to the Welcome agent. This is the same request as the text greeting (R5): Welcome instructions plus the speech-mode layers, `reasoning_effort="low"`, `max_output_tokens=256`, no tools. The greeting input asks for at most two short spoken sentences in English.
2. The request runs **after** `ready`, outside the admission startup budget. This avoids repeating the int incident where a slow Welcome readiness request held the slot.
3. When the text arrives, the runtime sends it through `session.commentary.append` with `delegation_id: null`, split by the R4 splitter if it is over 500 bytes. The speaking model may paraphrase it (FR-007).
4. If the caller speaks or a delegation arrives first, the request is cancelled and no greeting is sent (FR-004).
5. Later segments, started on an agent change, never greet (FR-005).
6. **The 5 s deadline applies to the first greeting audio.** Only the time to the first word counts, and the greeting may keep playing after 5 s. If the greeting text has not arrived by 5.0 s after ready, the request is cancelled and the greeting is never spoken later. The miss counts as `deadline` against SC-001, and there is **no fixed fallback**. On an error, the failure is logged and the session simply listens.

`session.input` stays `[]`, so the `session.started` validator is unchanged.

**Rationale**:

- The requester wants the Welcome agent to own what is said, so editors shape the greeting through the Welcome instructions.
- Commentary is the documented way to give the speaking model text to say aloud.

**Risk**: a full-effort Welcome turn took 4.4–10.5 s on int. Low effort and the 256-token cap should shorten it, but SC-001 (at least 90% within 5 s) is at risk. The `test.greeting` measurements show this on int (T091).

**Alternatives considered**:

- A fixed greeting instruction in `session.input` plus commentary (about 0.7 s on int): rejected by the requester, because the Welcome agent should decide what is said.
- The speaking model greets from the Welcome agent's instructions without a model call: rejected for the same reason.
- The Welcome agent writes the greeting with a fixed fallback after about 3 s: rejected by the requester.
- A pre-recorded clip: rejected, because exact wording is out of scope.

## R2: What the speaking model is told

**Decision**: GPT-Live `instructions` become the **voice conversation instructions**. They no longer contain the active agent's prompt (FR-014).

- **Slice 1** uses a shipped default constant in the runtime (`services/modality_defaults.py`). It follows the published structure: personality, backchannel policy, interruption policy, and a delegation policy using `Generate <|SpawnThinking|>` wording.
- **Slice 2** reads `conversationSettings.voiceInstructions` from the runtime projection. Management fills in the shipped default when the value is absent (R9).

The **context note** (FR-038) is sent with `session.thinking.append` and `delegation_id: null` right after `session.started` on every segment, which means at session start and after every agent change.

- Each line is `name: routing description`, with the description cut to 160 characters.
- The note is capped at 2,000 UTF-8 bytes. If it runs over, the last line is "…and N more communities".
- The note is split into parts of at most 500 bytes (R4 splitter).

**Rationale**:

- The requester chose to keep agent instructions out of the speaking model (2026-11-12, Q3) but to give it routing context (2026-11-12, Q1 of the second session).
- Thinking appends are documented as quiet. A `developer` message in `session.input` on later segments might prompt speech, as it does for the greeting.
- Communities carry a `routingDescription` of up to 4 KiB and agents have no description field, so the note uses the name and role for agents and truncated descriptions for communities.

**Alternatives considered**:

- Putting the note in `session.input`: risk of the model speaking.
- Sending full routing descriptions: up to 50 × 4 KiB, far beyond what the voice model needs.

## R3: Quiet update on handover

**Decision**: When `session.delegation.created` arrives, the runtime immediately sends `session.thinking.append` with that delegation ID and the fixed content "Request received; the application is working on it."

- **Slice 1** always sends it.
- **Slice 2** sends it only when `conversationSettings.quietHandover` is not `false`. The default is `true` (FR-016, FR-017).

**Rationale**:

- Foundry rejects `delegation.acknowledgement_delay_ms` (`unknown_parameter`, int probe), so the delay window is not available.
- The guidance states that a factual thinking append cancels the pending automatic acknowledgement.
- `context_event` refuses empty content, and a short factual text is equally valid, so the empty append is not used.
- Suppression is best effort (spec P1/SC-009).

**Alternatives considered**: An empty thinking append: rejected because the existing validator refuses it and it gives the model no information.

## R4: Delivering long spoken answers

**Decision**: A new splitter, `services/speech_parts.py`, cuts a spoken reply into parts of at most 500 UTF-8 bytes. It prefers sentence ends, then whitespace, then code-point boundaries.

**Sending.** The executor sends parts in order through `session.commentary.append` with the same delegation ID.

- Each next part waits for the previous part's `session.commentary.appended` acknowledgement, matched by `client_event_id`.
- It also waits until the previous part's output transcript has started, or 2 s have passed.

**Interruption.** The remaining parts are dropped if either of these happens before the last part is sent:

- a verified user input transcript (the existing speech-recency rule) arrives;
- a new `session.delegation.created` arrives.

The runtime then sends the dropped text through `session.thinking.append`. It is prefixed "The rest of the earlier answer was not spoken: " and capped at 1,500 bytes, so at most three appends (FR-008).

**Other bounds.** The existing 64 KiB reply limit and the work budget still bound the total. When the work budget runs out, the remaining parts are dropped without a note.

**Rationale**:

- Today the single append fails above 500 bytes (`gpt_live.py` L127), which surfaces as the failure line.
- Pacing on the acknowledgement keeps the parts in the order they were sent and avoids flooding the context.
- The guidance recommends streaming coherent, verified chunks with `session.commentary.append`.

**Alternatives considered**:

- Truncating the reply: violates FR-008.
- Asking the agent to retry shorter: an extra model call, still not guaranteed.

## R5: Text greeting when a conversation opens

**Decision**: The text endpoint accepts an **open request**: no `conversationId` and no `text`.

**Normal path.** The service creates the conversation and runs the Welcome agent once, with a fixed greeting input ("The user has just opened the conversation. Greet them in English…"), using:

- the text-modality layers (slice 2);
- `reasoning_effort="low"`;
- `max_output_tokens=256`;
- no tools.

**Fallback.** If no complete greeting is ready 4.0 s after the request arrives, the service cancels the model call and streams the fixed fallback "Hello and welcome! How can I help you today?" (FR-003). The 4.0 s leaves 1 s for relay and rendering inside the 5 s target.

**Storage.** The greeting that was sent is stored as the conversation's first assistant turn (FR-039).

**Readiness.** If the model greeting succeeds, it proves backend readiness; otherwise the first user turn runs the existing readiness request.

**Other requests.**

- A request with `text` and no `conversationId` still starts a conversation without a greeting. This is kept only for backward compatibility with existing clients; the Test page always opens with the open request, so its greeting comes first and an early user message is answered right after it (FR-004).
- The management text relay sends the open request as soon as the console session is ready, before any queued user turn.

**Rationale**:

- One request per turn stays the model (`api/text.py`), and the open request is simply "turn 0".
- Low effort plus a hard deadline keeps the 5 s target (SC-002) even though int measured 4.4–10.5 s for a full-effort Welcome turn.
- Replacing readiness avoids two model calls before the greeting.

**Alternatives considered**:

- The relay renders a fixed greeting itself: violates FR-002, because the service must greet for every client.
- Prepending the greeting to the first reply: rejected by the requester (2026-11-13, Q1).

## R6: Formatted text in the Test page

**Decision**: Markdown is rendered **in the management relay**, never as HTML in the browser.

**Relay.** On `text.completed`, the relay parses the full reply with `markdown-it-py`: CommonMark with tables, `html=False`, `linkify=False`. It converts the tokens into a bounded JSON tree of allowlisted nodes:

- block: `p`, `h1`–`h3`, `ul`, `ol`, `li`, `blockquote`, `pre`, `table`, `thead`, `tbody`, `tr`, `th`, `td`, `hr`;
- inline: `code`, `strong`, `em`, `br`, `a`, `img`.

It sends this tree to the browser as a new `text.formatted` control event.

**Browser.** The browser replaces the streamed plain-text row with nodes built through `createElement` and `textContent`. It never parses HTML.

**Links and images.**

- `a.href` and `img.src` are kept only when `new URL(value).protocol === "https:"`; otherwise they show as plain text (FR-011).
- Links get `target="_blank"`, `rel="noopener noreferrer"` and `referrerPolicy="no-referrer"`.
- Images get `referrerPolicy="no-referrer"`, `loading="lazy"` and alt text.

**Bounds.** The tree is limited to 2,000 nodes and depth 12. Anything over the limit falls back to plain text.

**Rationale**:

- No HTML string ever crosses into the DOM, so there is no sanitizer to configure and no XSS through markup (Constitution II).
- Rendering stays in Python (Constitution V).
- `markdown-it-py` is already in the management lock file as a transitive dependency and becomes a direct pin.
- Streaming stays plain text, and formatting appears once the reply is complete.

**Alternatives considered**:

- `rx.markdown`: chat rows are not Reflex state; they are built by `live_audio_js.py`.
- Browser-side Markdown with an HTML sanitizer: adds a JavaScript dependency and an HTML injection surface.
- Server-side HTML with `nh3`: a new dependency, and HTML still reaches the DOM.

## R7: One-time replacement of the default agent instructions

**Decision**: The new Welcome, Routing and Goodbye defaults live in `management_app/services/default_prompts.py`. Bootstrap uses them.

A new operator command, `python -m management_app.services.apply_default_prompts`, run once in the slice 1 deployment runbook, publishes one new revision. That revision:

- replaces the three protected agents' `prompt` with the defaults;
- keeps everything else;
- goes through the normal publication path, so it gets audit and history (FR-036).

The command is idempotent: if all three prompts already equal the defaults, it publishes nothing. No later release runs it (FR-013).

The agent settings panel gains "Restore default" for pre-defined agents. It sets the draft prompt to the current shipped default.

**Rationale**:

- A one-time marker in the stored document would change the configuration format in slice 1. The decision keeps slice 1 format-free (2026-11-12, Q3 of the second session).
- Bootstrap already runs only on explicit operator call. This follows the same pattern and is documented in the configuration runbook.

**Alternatives considered**:

- An automatic migration at management startup: no reliable "already applied" signal without a marker, and it would run again on rollback and redeploy.
- A marker field: needs a format change in slice 1.

## R8: Agent-reported progress inside the executor

**Decision**: Every agent gets a `report_progress(state, message)` function tool (slice 2).

**The turn keeps going.** Today the executor runs each agent with `tool_use_behavior="stop_on_first_tool"` and `max_turns=1`. When a tool was called (`called[0]`), it continues the loop with `run.to_input_list()` (`services/conversation.py` L640–L664). A progress call therefore ends one model step, and the loop resumes with the tool result `"reported"`. The agent keeps working in the same turn, and no executor redesign is needed.

**The handler**:

1. Validates `state` ∈ {`still_working`, `waiting_for_input`, `completed`, `failed`, `cancellation_confirmed`} and a message of 1–200 characters.
2. Drops the update if its work item is no longer current (FR-024).
3. Applies the rate limit (FR-025).
4. Emits it:
   - text: a `progress` stream event;
   - voice: `session.commentary.append` with the delegation ID, plus a `progress` control event to the browser.
5. Records the final and waiting states in the executor history as an assistant line "Status (<state>): <message>" (FR-040).

**Fixed lines.** The fixed holding line is no longer scheduled in slice 2. The fixed failure line is used only when the work item fails and no `completed`, `failed` or `cancellation_confirmed` was reported for it (FR-023).

**Rationale**:

- This settles the concept's biggest open question: the existing one-tool-per-step loop already continues after tools.
- The step loop is bounded by `item.step()` and the work budget.

**Alternatives considered**:

- The runtime reports "still working": rejected by the requester (FR-021).
- A text prefix parsed from the reply stream: fragile, and it mixes progress with the answer.

## R9: Where the slice 2 settings live, and the schema change

**Decision**: Schema `2.2.0` adds optional fields only.

- `conversationSettings` gains:
  - `voiceInstructions`;
  - `voicePlatformRules`, `voiceGlobalPrompt`;
  - `textPlatformRules`, `textGlobalPrompt`;
  - `quietHandover`.
- Each `Agent` gains `voiceAddition` and `textAddition`.

**Absent means default.** A field that is not stored means "use the current shipped default", and "Restore default" removes the stored value. Management stores only explicit edits. The runtime projection (`/v2/runtime/configuration`) always carries resolved values: management fills in the shipped defaults for absent fields before computing the projection's `policyHash`.

**Versions.** The runtime accepts 2.0.0, 2.1.0 and 2.2.0. Management writes 2.2.0 only when a 2.2.0 field is set; otherwise it keeps the stored version.

**Rationale**:

- The pattern is additive and optional, as in 2.1.0.
- Absent fields are never written with defaults, so revisions from earlier releases keep their stored shape and their two-form hash check still holds (lessons from PR #42 and PR #43).
- Defaults have a single owner (management). The runtime's slice 1 constant is checked against the management default by a contract test.

**Alternatives considered**:

- Writing defaults into stored revisions: this repeats the `routable` incident.
- A separate behavior document: a second publication path, against spec 005's one write path.

## R10: Limits (FR-033)

**Decision**:

| Text | Limit (UTF-8) |
|------|---------------|
| `voiceInstructions` | at most 12 KiB, which leaves room under the 16 KiB GPT-Live instructions cap for the delegation suffix |
| Each platform-rules or global-prompt layer | at most 8 KiB |
| Each agent addition | at most 4 KiB |
| Composed instructions per agent and modality (layers + prompt + addition) | at most 36 KiB |

Management refuses publication with `configuration_text_too_large` and a field path. The runtime re-checks on load.

**Rationale**: These sit within the existing per-agent prompt limit of 16 KiB and the text adapter's input budget (`max_input_tokens` ≥ 16384). They are generous for prompt text and give one clear message per field.

## R11: Observability

**Decision**: Three new closed-set operations are added to `observability.py` (runtime), without any content:

| Operation | Reasons | Measurement |
|-----------|---------|-------------|
| `test.greeting` | `ready`, `fallback`, `deadline`, `skipped` | `greeting_seconds`: ready → first greeting audio or output transcript delta (voice), or first `text.delta` (text) |
| `test.speech_parts` | `ready` | `parts`; `dropped_parts` when interrupted |
| `test.progress` | `still_working`, `waiting_for_input`, `completed`, `failed`, `cancellation_confirmed`, `fallback_line`, `rate_limited`, `stale` | none |

SC-001 is measured from `test.greeting` with mode `speech` over at least 20 sessions on int, using a Log Analytics query in the monitoring runbook.

**Rationale**: This follows the existing content-free telemetry pattern (Constitution VII). No message text is logged (FR-035).

## R12: Accessibility (FR-041)

**Decision**:

- `#test-messages` stays `role="log"` with `aria-live="polite"`.
- Status rows are separate elements with `data-kind="status"`, a visible "Status:" prefix and a distinct style. They are announced through the log as "Status: <message>".
- The greeting is an ordinary assistant row.
- Formatted replies replace the row content in place, without re-announcing.

**Rationale**: This keeps the single polite live region from spec 005. A second `role="status"` region would double-announce.
