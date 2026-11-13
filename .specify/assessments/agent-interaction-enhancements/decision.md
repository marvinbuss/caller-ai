# Decision: Speech and text behavior per modality for the same agents

- **Slug**: agent-interaction-enhancements
- **Decided**: 2026-11-12
- **Verdict**: go
- **Artifacts reviewed**: intake.md | research.md | problem.md | concept.md | `.specify/memory/constitution.md`

## Scorecard

| Criterion | Rating | Justification |
|-----------|--------|---------------|
| Problem validity | adequate | The problems are real: the spec 001 greeting requirement is unmet on the GPT-Live path, voice length failures are possible by construction (500-byte single append), text never greets, and speech and text share one prompt per agent. All are cited in research. Severity is limited because the product is pre-production and only one stakeholder has reported it. |
| Evidence strength | adequate | Code facts are cited with file and line. The platform behavior was checked live on int: Foundry rejects `acknowledgement_delay_ms` and supports the greet-first sequence. Demand evidence is thin: one stakeholder, no usage data, and deliberately no baseline (G4). |
| Value vs. inaction | adequate | Doing nothing costs only demo and test quality today and leaves the spec 001 greeting requirement unmet. Slice 1 is cheap and fixes the measurable gaps; slice 2's editor control has value mainly once editors write custom agents. |
| Feasibility / appetite | adequate | Slice 1 is small, needs no configuration format change, and its greeting mechanism is proven on Foundry. Slice 2 is medium, and its largest unknown, progress reported mid-turn under the current `stop_on_first_tool` / `max_turns=1` executor, is named with a fallback. |
| Strategic fit | strong | Fits the constitution: Azure-first (Foundry capability verified before design, Principle I), Python/Reflex (V), measurable latency targets (5 s greeting, 90% voice / 100% text, VI), and automated tests for the mechanics (IX, matching P5). |
| Risk posture | adequate | The key risks are identified, with mitigations named: configuration format change (lesson from PR #42 and #43), editable platform rules breaking voice, trusted agent outcomes (accepted, G6), acknowledgement suppression best effort (accepted, P1), text greeting latency, and safe Markdown rendering of untrusted model output (Principle II). The mitigations are not yet proven and remain assumptions to validate in the spec. |

## Verdict & Rationale

**Go.** The problem is valid, the evidence is adequate (code-cited and live-probed), and a recommended concept exists (Option B in two slices) whose appetite matches the value.

- **Slice 1** directly meets the measurable success metrics at small cost and without touching the stored configuration format. That alone beats doing nothing.
- **Slice 2** addresses the goals the requester explicitly chose: editor control, progress in both modalities, and answers shaped per modality for custom agents.

Slice 2 carries the real risk: a configuration format change, plus the executor's one-tool-per-turn flow. The specification should therefore treat the slices as separately testable increments and decide the mid-turn progress question early.

No criterion is rated `weak` or `unknown`. The thin demand evidence is acknowledged; it is offset by the existing spec 001 requirement and the low cost of slice 1.

## If needs-clarification

Not applicable.

## If go: Handoff to `/speckit-specify`

- **Problem**: People using the same Call AI agents get an inconsistent and sometimes broken experience between voice and text:
  - voice openings depend on the model;
  - text never greets;
  - the same answer is used for speaking and reading, so long spoken answers can fail;
  - there is little feedback during work.

  Editors cannot shape behavior per modality and start from one-line default prompts.

- **Chosen approach**: Concept Option B, delivered in two slices.
  - **Slice 1:**
    - Welcome greets first in voice (documented greet-first sequence) and text.
    - Long spoken answers are delivered in parts instead of failing.
    - The Test page text chat renders Markdown.
    - Best-practice default prompts for Welcome, Routing and Goodbye overwrite the current ones.
    - GPT-Live voice instructions follow the published structure.
    - A short quiet update is sent on delegation to reduce automatic acknowledgements (best effort).
  - **Slice 2:**
    - Settings gains layered behavior per modality (voice and text). Each has platform rules, a global prompt, and an optional per-agent addition that is appended. All layers are editable by editors, and best-practice defaults ship.
    - Agents report progress themselves: still working, waiting for input or confirmation, completed, failed, cancellation confirmed. It shows as inline status lines in text chat and next to the captions in voice, and every state is spoken in voice.
    - The runtime's fixed lines become a fallback used only when an agent fails completely.
    - An on/off setting (default on) controls the quiet update on delegation.

- **In scope**: the voice and text Test sessions; the runtime; management Settings; agent editing on the Community and Agent page; the Test page; the default agent prompts; the configuration format version step needed for slice 2.

- **Out of scope**:
  - Changing the provider or delegation mode.
  - Exact greeting wording or recorded clips.
  - Guaranteed silence or application-controlled playback.
  - Runtime checks of agent-reported outcomes.
  - Keeping or migrating the existing default-agent instructions.
  - A new management role.
  - A before-change baseline.
  - A phone channel.
  - An acknowledgement delay setting (Foundry rejects it).
  - Prompt version history beyond published revisions.
  - Automated cross-modality evaluation with a grader model.
  - Markdown outside the Test page text chat.

- **Success metrics**:
  - Voice greeting heard within 5 s of the session being ready in at least 90% of sessions.
  - Text greeting shown within 5 s of the session being ready, before the first user message, in 100% of sessions.
  - Zero voice answers failing because of length.
  - At least one status update in both modalities for any work running longer than 5 s, with the final state matching the outcome.
  - Consistency across modalities, judged by a manual Test-page check of six fixed scenarios, each run by voice and by text: greeting, routing to a custom community, a tool or MCP call with progress, a failing call, a long structured answer, goodbye.
  - No duplicate spoken acknowledgements (best effort, manual).
  - Automated tests with test doubles for the mechanics: greeting sent first, progress events emitted, long voice replies split, Markdown rendered.

- **Carried-forward open questions** (assumptions to validate during specification and planning, from concept.md):
  1. Does the greet-first sequence reach the 90% within 5 s target over repeated Foundry sessions? One probe showed about 0.7 s.
  2. Does a quiet update right after delegation, without a delay window, noticeably reduce GPT-Live's automatic acknowledgement on Foundry?
  3. How can the Welcome text greeting be produced within 5 s (low reasoning, an output cap, or a fallback greeting), given the measured 4.4 to 10.5 s?
  4. Does GPT-Live speak several consecutive updates for one delegation as one coherent answer?
  5. Can agents report progress mid-turn under the current one-tool-per-turn executor? If not, use the fallback: the runtime reports "Still working" and agents write the outcome texts. Decide early in the spec.
  6. How do platform rules prevent false "Completed" or "Failed" reports, given that outcomes are trusted? Validate with the "failing call" scenario.
  7. How is Markdown from untrusted model output rendered safely on the Test page, with no raw HTML or script (Principle II)?
  8. How is the configuration format version step for the slice 2 layers made compatible with revisions published by earlier releases (lesson from PR #42 and #43; Principle IV migration plan)?
  9. How are edits to the platform rules guarded so that a broken edit is rejected or clearly visible rather than silently breaking voice?
