# Problem Definition: Speech and text conversations with the same agents behave inconsistently and leave users waiting

- **Slug**: agent-interaction-enhancements
- **Created**: 2026-11-12
- **Inputs used**: intake.md, research.md (including the live probe on int from 2026-11-12)

## Problem Statement

People who talk to the Call AI agents get a different and sometimes broken experience depending on whether they speak or type:

- **Voice openings depend on the model.** Nothing in the runtime makes Welcome speak first, so the greeting depends on the model choosing to speak.
- **Text conversations never open.** They always wait for the user to write first.
- **Answers are not shaped for the modality.** Answers are the same whether spoken or read, and a reply that suits reading can fail outright when spoken.
- **Feedback during longer work is minimal.** Voice callers get one fixed holding line, and text users get nothing.

Editors cannot fix this without code changes. Each agent has only one prompt shared by both modalities, and the default agents ship with one-line prompts.

This matters now because:

- the greeting requirement from spec 001 has never been met on the GPT-Live path;
- the owner has observed both the inconsistency and silent starts on int;
- the product is still pre-production, so behavior and defaults can change without migrating real users.

## Affected Users & Stakeholders

- **Users**:
  - **Voice caller** (today, a tester using the voice Test session). They may start a call in silence, can hear a failure line when an answer is too long to speak, and get only a generic "One moment" during work. — [research: Users & Demand; Data & Constraints, 500-byte limit; Prior Art, holding line]
  - **Text chat user** (today, a tester using the text Test session). They must start the conversation themselves. They see no status while work runs. Answers formatted for reading (tables, bullets) arrive as raw characters because the chat does not render Markdown. — [research: Prior Art, text path and Test page rendering]
  - **Editor** (the existing management role that edits agents). They cannot tune how agents behave per modality, and they start from minimal default prompts for Welcome, Routing and Goodbye. — [intake: Q3; research: Prior Art, default prompts]
- **Stakeholders**:
  - **Project owner**: raised the idea, owns scope and priority, and decided that existing default-agent instructions may be overwritten (no production data). — [intake: Origin, Q11]
  - **Platform owner, Azure AI Foundry GPT-Live**: an external constraint. It decides which session features exist; for example, it rejects `acknowledgement_delay_ms`. — [research: int probe, G1]
  - No other customer or end-user group shapes the defaults or greeting language for now; the project owner decides alone. — [requester, 2026-11-12]

## Goals

- **Greeting in voice.** Every voice conversation opens with the Welcome agent greeting the caller before they speak.
- **Greeting in text.** Every text conversation opens with a Welcome greeting before the user's first message.
- **Answers that fit the modality.** The same agents give answers suited to how they are received: brief and natural when spoken, readable and structured where useful when read. A user asking the same thing in either modality gets the same substance.
- **No length failures in voice.** No spoken answer fails or is cut off because it is too long for the speech channel.
- **Informed during longer work.** Users in both modalities are told what is happening while longer work runs. The states covered are still working, waiting for their input or confirmation, completed, failed, and cancellation confirmed.
- **No unwanted acknowledgements.** Users do not hear filler or duplicate acknowledgements that compete with the agents' own updates. This is best effort: GPT-Live's own acknowledgement may sometimes still be heard, because Foundry offers no supported way to suppress it reliably. — [requester, 2026-11-12; research G1]
- **Editor control.** Editors can adjust modality-specific and default-agent behavior themselves. The system ships with defaults based on published best practice, so it behaves well without any editing.

## Non-Goals

- **Delegation and provider.** Changing the delegation mode or the voice provider. Speech stays on Azure AI Foundry GPT-Live with client delegation. — [research: Data & Constraints, Foundry only]
- **Exact greeting wording.** A paraphrased greeting is acceptable. — [intake: Q9]
- **Guaranteed silence.** No guaranteed silence or application-controlled playback during work. — [research: live-delegation guidance; Evidence Against]
- **Runtime checks of progress.** The runtime will not verify agent-reported progress or outcomes; the requester accepted this risk. — [research: Resolutions G6]
- **Keeping existing instructions.** Existing Welcome, Routing and Goodbye instructions will not be preserved or migrated. — [intake: Q11]
- **New roles.** No new management role; a separate admin role may come later. — [intake: Q3]
- **Before-change baseline.** Silent starts will not be measured before the change. — [research: Resolutions G4]
- **Phone channel.** No phone or telephony channel. The current runtime has none, and this work concerns the existing voice and text sessions. — [repo: no telephony code in `code/backend/src`]

## Success Metrics

- **Voice greeting time.** A voice session's greeting is heard within **5 s** of the session becoming ready, in at least **90%** of sessions.
  - Baseline: unknown and deliberately not measured (G4).
  - Probe on int: one session, first speech about 0.7 s after start, using the documented greet-first sequence.
- **Text greeting.** A text session shows a Welcome greeting within **5 s** of the session becoming ready and before the user's first message, in 100% of sessions.
  - Baseline: 0%, because text never greets today.
  - Tension: an int Welcome turn on the reasoning deployment took 4.4 to 10.5 s.
- **Voice length failures.** Spoken answers that fail because of length: **0**.
  - Baseline: unknown, since no metric exists. The failure is possible by construction today (500-byte limit, single append).
- **Progress updates.** Any work running longer than **5 s** gives the user at least one status update in both modalities, and the final state matches what actually happened. Qualitative, checked manually on the Test page.
  - Baseline: voice has one fixed line at 5 s; text has none.
- **Consistency across modalities.** For the fixed scenarios below, run once by voice and once by text on the Test page, the person checking judges the substance consistent and the form suited to each modality. Qualitative, manual Test-page check.
  - Baseline: not assessed.
  - Scenarios:
    1. Greeting.
    2. Routing to a custom community.
    3. A tool or MCP call with progress.
    4. A failing call.
    5. A long, structured answer.
    6. Goodbye.
- **Mechanics covered by automated tests.** Automated tests with test doubles cover the mechanics on every build: greeting sent first, progress events emitted, long voice replies split, Markdown rendered.
  - Baseline: none of these mechanics exist today.
- **Duplicate acknowledgements.** No duplicate or competing spoken acknowledgement is heard during work. Best effort, checked manually on the Test page.
  - Baseline: not assessed.

## Cost of Inaction

The product stays pre-production, so no real users are harmed today, but every demo and test session keeps showing the gaps:

- Voice openings remain model-dependent.
- Text conversations stay silent until the user writes.
- Long or structured answers can fail in voice.
- Feedback during work stays a single generic line in voice and nothing in text.
- Editors stay limited to one prompt per agent with minimal defaults.

The spec 001 greeting requirement remains unmet and unmeasured. The longer this lasts, the more agent prompts editors write without modality guidance, all of which would later need revisiting. — [research: Market & Context, Cost of doing nothing]

## Open Questions

None. All were resolved by the requester on 2026-11-12:

| # | Question | Resolution |
|---|----------|------------|
| P1 | Is GPT-Live's own acknowledgement acceptable when it is sometimes heard? | Yes. Avoiding it is best effort, not a hard requirement. |
| P2 | What is the voice greeting target rate? | 90% of sessions within 5 s. |
| P3 | How fast must the text greeting appear? | Within 5 s of the session being ready, the same as voice. |
| P4 | What counts as "longer work"? | Work running longer than 5 s, which matches today's holding line. |
| P5 | How is consistency checked? | Automated tests with test doubles cover the mechanics, and a manual Test-page check covers consistency using the six fixed scenarios. |
| P6 | Do other stakeholders shape the defaults? | No. The project owner alone shapes the defaults and greeting language for now. |
