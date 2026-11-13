# Feature Specification: Modality-Aware Agent Interaction

**Feature Branch**: `006-modality-aware-agents`

**Created**: 2026-11-12

**Status**: Draft

**Input**: User description: "Use the handoff summary in `.specify\assessments\agent-interaction-enhancements\decision.md` to specify the concept." The concept (Option B, two slices) is:

- **Slice 1.**
  - Welcome greets first in voice and text.
  - Long spoken answers are delivered in parts instead of failing.
  - The Test page text chat renders formatted answers.
  - Best-practice defaults for Welcome, Routing and Goodbye replace today's instructions.
  - The voice-side conversation instructions follow the published structure.
  - A quiet update on delegation reduces automatic acknowledgements on a best-effort basis.
- **Slice 2.**
  - Layered behavior settings per modality: platform rules, a global prompt, and an optional per-agent addition, all editable by editors.
  - Agent-written progress in both modalities, with the runtime's fixed lines kept only as a fallback.
  - An on/off setting for the quiet update.

## Clarifications

### Session 2026-11-12

- Q: When an agent has not reported progress within 5 s, should the system send a generic "still working" update? → A: No. Only the agent reports progress, and the system sends no update of its own; a missed update when an agent stays silent is an accepted risk (FR-021, SC-004).
- Q: Which greeting languages should editors be able to choose from in Settings? → A: English only. There is no greeting-language setting, and conversations still switch to the user's language once the user speaks or writes in another language (FR-006).
- Q: Should the shipped default instructions for Welcome, Routing and Goodbye overwrite the stored ones only once, or on every release with changed defaults? → A: Only once, on the first deployment of this feature. Later releases never overwrite them, and editors can restore the shipped default by hand (FR-013).
- Q: Should the voice layers shape both the speaking voice model and the working agents, or only the agents? → A: Only the working agents. The speaking model is guided by separate voice conversation instructions, which ship with a best-practice default and are editable in the voice section of Settings (FR-014, FR-028, FR-037).
- Q: Should formatted text answers show clickable links, and should they render images? → A: Yes, both, but only for `https` addresses. Links open in a new tab on click; all other addresses are neither loaded nor clickable (FR-011, SC-006).
- Q: When a caller interrupts a multi-part spoken answer with a new request, should the remaining parts be dropped? → A: Yes. The remaining parts are not spoken, and a quiet, non-spoken update tells the speaking model that the rest of the earlier answer was dropped and what it contained (FR-008, Edge Cases).
- Q: Should the speaking voice model be told which agent is currently active? → A: Yes, through a short automatic quiet note at session start and on every agent change. It names the active agent and role, plus the reachable communities with their routing descriptions, and never includes agent instructions or layers (FR-038).
- Q: Should the delivered greeting become part of the conversation history the agents see? → A: Yes, in both modalities. In text, this is the agent greeting or the fallback; in voice, the transcribed spoken greeting (FR-039).
- Q: Should editing the voice conversation instructions ship in slice 1 rather than slice 2? → A: No, it ships in slice 2. Slice 1 uses the shipped default, so slice 1 needs no stored-configuration change (FR-014, FR-037).
- Q: How should the 90%-within-5-seconds voice greeting target be measured before release? → A: From the content-free greeting events on int, over at least 20 voice test sessions (SC-001, FR-035).
- Q: Should slice 1 keep the fixed "One moment, I'm checking that." line until slice 2? → A: Yes. Slice 1 keeps today's fixed holding and failure lines; from slice 2 on, they are used only as the fallback (FR-023).

### Session 2026-11-13

- Q: Should the text greeting come from the conversation service for every client, or only from the Test page? → A: From the text conversation service, at the start of every new text conversation for any client. The Test page only displays it (FR-002).
- Q: Must every voice progress update be heard, or is sending it to the voice model to say enough? → A: Best effort. Every update is sent to be spoken and is always shown next to the captions, but the speaking model may reword, merge or occasionally skip it (FR-020).
- Q: Should agents' progress updates become part of the conversation history the agents see later? → A: Only the final and waiting states (completed, failed, cancellation confirmed, waiting for input or confirmation); "still working" updates are not (FR-040).
- Q: For text clients, when does a conversation count as ready for the 5-second greeting limit? → A: When the client opens the conversation, with no user message. The agent's first reply is the greeting and must arrive within 5 seconds (FR-002, Assumptions: Timing).
- Q: Should screen readers announce the greeting, answers and progress status lines on the Test page? → A: Yes, politely, with status lines labelled as status (FR-041).
- Q: In text, what happens when the user writes before the greeting appears? → A: The greeting is always shown first, and the early message is answered right after it. In voice, a caller who speaks first still skips the greeting (FR-004).
- Q: Who decides what the voice greeting says? → A: The Welcome agent writes the greeting text in one model call, and the speaking voice model says it. There is no fixed fallback; a late greeting counts against SC-001 (FR-001).
- Q: How can FR-021 be made testable? → A: Agents report "still working" before any tool, MCP or external call (FR-021).
- Q: What happens when the Welcome agent's voice greeting text arrives late? → A: The request is cancelled at 5 s and a late greeting is never spoken. Only the time to the first greeting audio counts; the greeting may keep playing after 5 s (FR-001, SC-001).
- Q: Which capacity target applies to this feature's call paths? → A: Concurrency is unchanged: one active test per manager and at most 5 occupied slots. The greeting adds one bounded model call per new voice session and per new text conversation (Assumptions: Capacity).
- Q: How is Principle VI's load-test requirement met for the greeting target? → A: By adding load and failure tests at the declared concurrency (5 occupied slots) with test doubles, plus the int measurement of real model latency. The constitution stays unchanged (Assumptions: Capacity).

## User Scenarios & Testing *(mandatory)*

Slice 1 is User Stories 1–4 (P1). Slice 2 is User Stories 5–7 (P2/P3). Each slice can be built, tested, deployed and demonstrated on its own.

### User Story 1 - Voice conversations open with a greeting (Priority: P1)

A tester starts a voice session on the Test page and says nothing. Within a few seconds, the Welcome agent greets them in English, introduces the service, asks how it can help, and then listens. If the tester starts speaking during the greeting, the assistant yields and listens. Once the tester speaks another language, the conversation continues in that language.

**Why this priority**: The greeting has been a product requirement since spec 001 and is still unmet on the voice path. Silent starts are one of the two triggers for this feature.

**Independent Test**: Start voice sessions without speaking and time the first audible Welcome speech. This works with only this story built.

**Acceptance Scenarios**:

1. **Given** a ready voice session and a silent caller, **When** the session becomes ready, **Then** the first greeting audio starts within 5 seconds, in English, and the greeting ends with an invitation to speak.
2. **Given** the greeting is playing, **When** the caller starts speaking, **Then** the assistant stops or yields and responds to what the caller said, without restarting the greeting.
3. **Given** the English greeting, **When** the caller answers in German, **Then** the following replies are in German.
4. **Given** a voice session that has already greeted, **When** the conversation moves to Routing or another agent, **Then** no second Welcome greeting is played.

---

### User Story 2 - Text conversations open with a greeting (Priority: P1)

A tester starts a text session on the Test page. Before they type anything, the Welcome agent's greeting appears in the conversation, written in English and inviting the tester to describe what they need.

**Why this priority**: Text conversations never greet today, and both modalities should feel like the same assistant from the first moment.

**Independent Test**: Start text sessions without typing and time the appearance of the greeting.

**Acceptance Scenarios**:

1. **Given** a ready text session, **When** the tester types nothing, **Then** a Welcome greeting appears within 5 seconds of the session becoming ready.
2. **Given** the Welcome agent cannot produce its greeting within 5 seconds, **When** the limit passes, **Then** a short default English greeting is shown instead, and no second greeting appears afterwards.
3. **Given** the tester sends a message before any greeting appears, **When** the message arrives, **Then** the greeting is shown first and the message is answered right after it.
4. **Given** the English greeting, **When** the tester writes in German, **Then** the following replies are in German.

---

### User Story 3 - Answers fit how they are received (Priority: P1)

The same agents answer the same requests in a form that suits each modality:

- **Spoken:** answers are brief and natural. A spoken answer that is longer than one speech update is still delivered completely and in order instead of failing.
- **Written:** answers may use lists, tables, headings and emphasis, and the Test page text chat shows them formatted rather than as raw characters.

**Why this priority**: Today a long or structured answer can fail in voice, and the caller then hears a failure line. Formatted text also shows as raw characters in the chat.

**Independent Test**: Ask for something that produces a long, structured answer once by voice and once by text. Voice delivers a short spoken version without failing; text shows a formatted table or list.

**Acceptance Scenarios**:

1. **Given** a voice session, **When** an agent's answer exceeds what fits in one speech update, **Then** the caller hears the whole answer in order, and no "could not finish" message is played because of length.
2. **Given** a text session, **When** an answer contains a list or a table, **Then** the chat shows it formatted.
3. **Given** a text answer that contains a script, embedded HTML, or a link or image whose address is not `https`, **When** it is shown, **Then** nothing executes, no non-`https` content loads, and the markup is displayed as harmless text or removed. `https` links and `https` images are shown.
4. **Given** the same request in voice and in text, **When** both answers are compared, **Then** they contain the same substance, short and spoken in voice, structured where useful in text.

---

### User Story 4 - Good default behavior out of the box (Priority: P1)

After this feature is deployed, the pre-defined Welcome, Routing and Goodbye agents use new best-practice default instructions instead of today's one-line prompts. The voice side of every conversation follows the published structure for live voice conversations: personality, listening cues, interruption handling, and when to hand work to the agents. When work is handed to the agents in voice, the system right away sends a quiet update that reduces the voice service's own generic acknowledgement, where the service allows it.

**Why this priority**: Without good defaults, every other improvement depends on editors writing prompts. Today's defaults are too thin to deliver the greeting, routing and closing behavior expected.

**Independent Test**: Deploy into an environment with existing configuration, open the Community and Agent page, and confirm that Welcome, Routing and Goodbye show the new defaults. Run the six review scenarios.

**Acceptance Scenarios**:

1. **Given** an environment whose Welcome, Routing and Goodbye instructions were edited earlier, **When** this feature is deployed, **Then** their instructions are replaced by the new defaults, without notice or backup.
2. **Given** the new defaults, **When** an editor edits a pre-defined agent's instructions and publishes, **Then** the edit is kept and used, as editing worked before.
3. **Given** a voice session in which work is handed to the agents, **When** the handover happens, **Then** a quiet update is sent at once. The voice service's generic acknowledgement may still sometimes be heard; this is best effort.

---

### User Story 5 - Users are kept informed while work runs (Priority: P2)

While an agent works on a request, for example calling a tool or an external service, it tells the user what is happening. The states are:

- still working;
- waiting for the user's input or confirmation;
- completed;
- failed;
- cancellation confirmed.

How each modality shows them:

- **Text:** each update appears as a status line in the conversation, visually distinct from answers.
- **Voice:** each update is sent to be spoken (best effort), and the Test page always shows it next to the captions.
- **Complete failure:** if the agent fails completely, the runtime's existing fixed line is used as a fallback.

**Why this priority**: Users currently get one generic voice line after 5 seconds and nothing in text. It ranks below slice 1 because it depends on the agent behavior settings and carries the larger technical unknown.

**Independent Test**: Run the "tool or MCP call with progress" and "failing call" scenarios in both modalities and check that the status updates and final states match what happened.

**Acceptance Scenarios**:

1. **Given** work that needs a tool, MCP or external call, **When** the agent follows its instructions, **Then** it reports "still working" before the call, in text and in voice. The system adds no update of its own.
2. **Given** a text session, **When** an agent reports "still working", **Then** a status line appears in the conversation, distinct from answer messages, and stays in the conversation history.
3. **Given** a voice session, **When** an agent reports any of the five states, **Then** the update is sent to be spoken and the Test page shows it next to the captions. The caller normally hears it, though the speaking model may reword, merge or occasionally skip it.
4. **Given** a capability call that fails, **When** the agent reports the outcome, **Then** the user is told it failed and is never told it completed.
5. **Given** an agent that fails completely, without reporting anything, **When** the work ends, **Then** the user receives the runtime's fixed fallback line.

---

### User Story 6 - Editors shape behavior per modality (Priority: P2)

An editor opens Settings and finds a voice section and a text section. Each section has two layers, both with shipped best-practice defaults:

- **Platform rules**: how agents must behave in that modality, for example brevity and progress reporting in voice, and formatting in text.
- **Global prompt**: guidance that applies to every agent in that modality.

On the Community and Agent page, each agent's settings offer an optional voice addition and text addition. These are added after the agent's own instructions and never replace the layers above them.

The voice section also holds the **voice conversation instructions**: the instructions for the model that speaks with the caller, covering personality, listening cues, interruptions, and when to hand work to the agents. They ship with a best-practice default. The voice and text layers and the agent additions shape only the agents that do the work; the speaking model does not receive them.

Editing the platform rules shows a warning and asks for confirmation. Every layer can be restored to its shipped default. Changes are published with the configuration and apply to new conversations.

**Why this priority**: This gives editors control without code changes, especially for custom agents. It depends on slice 1's defaults being in place.

**Independent Test**: Change the text global prompt (for example to "answer in bullet points") and an agent's voice addition, publish, and start new sessions in both modalities. Each change shows up only in its own modality.

**Acceptance Scenarios**:

1. **Given** the shipped defaults, **When** an editor views Settings, **Then** voice and text each show platform rules and a global prompt containing the shipped default text.
2. **Given** an editor changes the text global prompt and publishes, **When** a new text session starts, **Then** answers follow the new prompt, and voice answers are unaffected.
3. **Given** an editor adds a voice addition to a custom agent and publishes, **When** that agent answers in voice, **Then** the addition is applied, and the agent's text answers are unaffected.
4. **Given** an editor starts editing platform rules, **When** they try to save, **Then** they are warned that changes can break voice or text behavior and must confirm.
5. **Given** an edited layer, **When** the editor restores its default, **Then** the shipped default text returns and can be published.
6. **Given** voice conversation instructions longer than a voice session accepts, or an agent whose combined layers, instructions and addition are longer than an agent accepts, **When** the editor tries to publish, **Then** publication is refused with a message naming the text to shorten.
7. **Given** a tester without the editor role, **When** they open the application, **Then** they cannot see or change these settings.
8. **Given** an editor changes the voice conversation instructions and publishes, **When** a new voice session starts, **Then** the speaking model follows the new instructions, and text sessions and the agents' own instructions are unaffected.

---

### User Story 7 - Editors control the quiet update on handover (Priority: P3)

In the voice section of Settings, an editor can switch the quiet update on handover off or on. It is on by default.

**Why this priority**: The requester asked for it, but it only refines best-effort behavior that slice 1 already provides with a sensible default.

**Independent Test**: Switch it off, publish, start a voice session, trigger work, and confirm that no quiet update is sent. Switch it back on and confirm that it is sent.

**Acceptance Scenarios**:

1. **Given** a new installation, **When** an editor views voice settings, **Then** the quiet update on handover is shown as on.
2. **Given** an editor switches it off and publishes, **When** work is handed to the agents in a new voice session, **Then** no quiet update is sent.

---

### Edge Cases

- **Greeting interrupted (voice).** The caller speaks before or during the greeting. The assistant yields and the greeting is not repeated.
- **Early message (text).** The tester sends a message before the greeting arrives. The greeting is shown first, and the message is answered right after it.
- **Silent model.** No greeting audio starts within 5 seconds, for example because the Welcome agent's text is late. The greeting request is cancelled, nothing is spoken later, and the session continues and listens. The miss counts against the 90% target and is not retried.
- **Very long spoken answer.** The answer is spoken in parts and in order. If the session's work time runs out mid-way, the remaining parts are dropped and the existing time-limit behavior applies.
- **New request mid-answer.** The caller interrupts a multi-part spoken answer with a new request. The remaining parts are not spoken. Instead, a quiet, non-spoken update tells the speaking model that the rest of the earlier answer was dropped and what it contained, so it can use that content if the caller asks for it.
- **Progress after the work changed.** An agent reports progress for work the user has replaced or cancelled. The outdated update is not shown or spoken.
- **Too many updates.** An agent sends many "still working" updates in a row. At most one update per state change, plus at most one repeat "still working" per 10 seconds, reaches the user.
- **Wrong "completed".** An agent reports "completed" although the call failed. This is an accepted risk; the platform rules instruct otherwise, and the "failing call" review scenario checks it.
- **Silent agent.** An agent stays silent during long work. The user receives no progress update until the agent reports or answers; the system adds none. If the agent then fails completely, the fixed fallback line applies (FR-023).
- **Unsafe formatted text.** Model output in text contains links, images or HTML. No script or embedded HTML runs. Links and images with an `https` address are shown; any other address, such as `http`, `data` or `javascript`, is neither loaded nor made clickable. Links open in a new tab, without passing the referring page, only when the user clicks them.
- **Configurations from earlier releases.** These have no layers or quiet-update setting. They load and behave with the shipped defaults; publishing them adds the defaults and fails nothing.
- **Empty layer.** An editor clears a global prompt or an agent addition. It is treated as empty; the other layers still apply.
- **Cleared platform rules.** An editor clears the platform rules entirely. Publication is allowed after the confirmation warning, and the Test page shows a visible notice that platform rules are empty.
- **Edit while a session runs.** Settings change during a running session. The running session keeps its behavior, and new sessions use the published change.

## Requirements *(mandatory)*

### Functional Requirements

#### Greeting (slice 1)

- **FR-001**: In a voice session, the Welcome agent MUST greet the caller without waiting for the caller to speak, and the first greeting audio MUST start within 5 seconds of the session becoming ready; the greeting may keep playing after that. The assistant MUST then listen. The Welcome agent writes the greeting text, and the speaking voice model says it. If the greeting text is not available by 5 seconds, the request MUST be cancelled and the greeting MUST NOT be spoken later.
- **FR-002**: In a text session, a Welcome greeting MUST appear before the user's first message, within 5 seconds of the session becoming ready. The text conversation service MUST produce the greeting at the start of every new text conversation, for any client. The Test page only displays it. A text conversation opens without any user message, and the first reply from the agent MUST be the greeting, arriving within 5 seconds of the opening.
- **FR-003**: If the Welcome agent's text greeting is not available within 5 seconds, the system MUST show a short default English greeting instead. A later agent greeting MUST NOT also appear.
- **FR-004**: In voice, if the caller speaks before the greeting is delivered, the system MUST handle the caller's input normally and MUST NOT deliver the greeting afterwards; a greeting already in progress yields to the caller. In text, the greeting MUST always be shown first, and a message the user sends before it appears MUST be answered right after the greeting.
- **FR-005**: A session MUST greet at most once, regardless of later agent changes in that session.
- **FR-039**: In both modalities, the greeting actually delivered MUST become part of the conversation history the agents see, so they do not introduce the service again. This is the agent greeting or the default fallback greeting in text, and the spoken greeting as transcribed in voice.
- **FR-006**: The greeting MUST be in English in both modalities, and there is no greeting-language setting. Conversations MUST switch to the user's language once the user speaks or writes in another language.
- **FR-007**: The greeting wording MAY vary between sessions (paraphrased). Exact wording is not required.

#### Answer form (slice 1)

- **FR-008**: A spoken answer MUST be delivered completely and in order regardless of length. No spoken answer may fail because it is too long for one speech update. If the caller makes a new request before all parts are delivered, the remaining parts MUST NOT be spoken. The system MUST instead send a quiet, non-spoken update telling the speaking model that the rest of the earlier answer was dropped and what it contained.
- **FR-009**: Voice behavior guidance MUST ask agents for brief, natural spoken answers without visual formatting such as tables or markup.
- **FR-010**: The Test page text chat MUST display formatted answers, at least lists, tables, headings, emphasis, code and links.
- **FR-011**: Formatted output from models MUST be treated as untrusted:
  - It MUST NOT execute scripts or embedded HTML.
  - Links MUST be clickable only when their address is `https`. They MUST open in a new tab, without passing the referring page, and only on explicit user action.
  - Images MUST be shown only when their address is `https`.
  - Links and images with any other address MUST NOT be loaded or made clickable.
- **FR-012**: For the same request, voice and text answers MUST carry the same substance, differing only in length and form as their modality guidance requires.
- **FR-041**: On the Test page, screen readers MUST politely announce the greeting, answers and progress status lines as they appear, without interrupting the user. Status lines MUST be labelled as status, not as answers.

#### Defaults (slice 1)

- **FR-013**: The product MUST ship best-practice default instructions for the Welcome, Routing and Goodbye agents. When this feature is first deployed, these MUST replace the instructions currently stored for those agents, with no notice and no backup. This replacement MUST happen only once: later releases MUST NOT overwrite these agents' instructions, even if their shipped defaults change. Editors MUST be able to restore a pre-defined agent's instructions to the current shipped default by hand.
- **FR-014**: The model that speaks with the caller in voice MUST be guided by the **voice conversation instructions** and the context note from FR-038. The voice conversation instructions are one service-wide text, separate from all agent instructions, layers and additions. The shipped default MUST follow the published structure for live voice conversations: personality and speaking style, a listening-cue policy, an interruption policy, and a policy for when to hand work to the agents. The greeting instructions are added only at session start. In slice 1, the shipped default is used and cannot be edited; editing arrives in slice 2 (FR-037).
- **FR-038**: The speaking voice model MUST also receive a short, automatically generated, quiet (non-spoken) context note at session start and whenever the active agent changes. The note contains:
  - the active agent's name and role;
  - the communities the conversation can be routed to from there, by name and routing description.

  It MUST NOT contain any agent's instructions, layers or additions.
- **FR-015**: Editors MUST remain able to edit the instructions of the pre-defined agents after the defaults are applied, as defined in spec 005.

#### Acknowledgement handling (slices 1 and 2)

- **FR-016**: When work is handed from the voice conversation to the agents, the system MUST immediately send a quiet, non-spoken update that reduces the voice service's own generic acknowledgement. This is best effort: an occasional generic acknowledgement is acceptable.
- **FR-017** *(slice 2)*: Editors MUST be able to switch the quiet update on handover on or off in the voice section of Settings. The default MUST be on.

#### Progress reporting (slice 2)

- **FR-018**: Agents MUST be able to report the progress of work in one of five states, each with a short message:
  - still working;
  - waiting for input or confirmation;
  - completed;
  - failed;
  - cancellation confirmed.
- **FR-019**: In text, each progress update MUST appear as a status line in the conversation that is visually distinct from answers and remains in the conversation history.
- **FR-020**: In voice, every progress update MUST be sent to the speaking model to be said aloud, and the Test page MUST always show it next to the captions. Hearing it is best effort: the speaking model may reword, merge or occasionally skip an update.
- **FR-021**: Agent instructions MUST require agents to report "still working" before any tool, MCP or external call. The system MUST NOT send its own progress update while an agent is still working; only the agent reports progress. A missed update when an agent stays silent is an accepted risk.
- **FR-022**: Agent instructions MUST require that "completed" and "failed" are reported only after the outcome is known. The system does not verify agent-reported outcomes; this is an accepted risk.
- **FR-023**: When an agent fails completely without reporting, the system MUST deliver the existing fixed fallback line. Otherwise the fixed holding and failure lines MUST NOT be used. Until slice 2 is delivered, slice 1 keeps today's behavior: the fixed holding line after 5 seconds of voice work, and the fixed failure line.
- **FR-024**: Progress updates for work the user has since replaced or cancelled MUST NOT be shown or spoken.
- **FR-025**: The system MUST deliver at most one update per state change, plus at most one repeated "still working" per 10 seconds, for any one piece of work.
- **FR-040**: Progress updates in the states completed, failed, cancellation confirmed, and waiting for input or confirmation MUST become part of the conversation history the agents see on later turns. "Still working" updates MUST NOT.

#### Behavior layers (slice 2)

- **FR-026**: Settings MUST provide two layers for each modality (voice and text): **platform rules** and a **global prompt**. Each MUST ship with best-practice default text.
- **FR-027**: Each agent, including the pre-defined agents, MUST support an optional voice addition and an optional text addition.
- **FR-028**: For work done in a given modality, a working agent's behavior MUST combine, in this order: that modality's platform rules, that modality's global prompt, the agent's own instructions, then the agent's addition for that modality. An addition MUST NOT replace any earlier part. None of these MUST be given to the speaking voice model.
- **FR-029**: All layers and additions MUST be editable by users with the editor role and MUST NOT be visible or editable to users with only the tester role.
- **FR-030**: Saving a change to platform rules MUST require an explicit confirmation after a warning that the change can break voice or text behavior.
- **FR-031**: Editors MUST be able to restore any layer to its shipped default.
- **FR-032**: Layer and addition changes MUST be published with the configuration and apply to new conversations only. Running sessions keep their behavior.
- **FR-033**: Publication MUST be refused, with a message naming the text to shorten, when the voice conversation instructions exceed what a voice session accepts, or when any agent's combined layers, instructions and addition exceed what an agent accepts.
- **FR-034**: Configurations published by earlier releases, which have no layers, additions, voice conversation instructions or quiet-update setting, MUST continue to load, be served and run, using the shipped defaults. Publishing such a configuration again MUST NOT fail because these values are missing.
- **FR-037** *(slice 2)*: Users with the editor role MUST be able to edit the voice conversation instructions in the voice section of Settings and restore them to the shipped default. Saving a change MUST require the same warning and confirmation as platform rules (FR-030). Users with only the tester role MUST NOT see or change them.

#### Observability and security (both slices)

- **FR-035**: The system MUST record content-free events for these occurrences:
  - greeting delivered or missed, with time to first speech or text;
  - spoken answers split into parts;
  - progress updates by state;
  - fallback lines used.

  Greeting text, answer text and progress messages MUST NOT be logged.
- **FR-036**: Changes to behavior layers, additions, the voice conversation instructions and the quiet-update setting MUST be attributable to the publishing editor through the existing publication history.

### Key Entities *(include if feature involves data)*

- **Modality behavior layer**: Guidance that applies to all working agents in one modality. Its attributes are the modality (voice or text), the kind (platform rules or global prompt), the text, and whether it matches the shipped default. It is published with the configuration and never given to the speaking voice model.
- **Voice conversation instructions**: The single service-wide instructions for the model that speaks with the caller in voice. The shipped default follows the published structure for live voice conversations. It is editable in the voice section of Settings, can be restored to the default, and is published with the configuration.
- **Speaking-model context note**: An automatically generated, quiet note for the speaking voice model. It is sent at session start and on every agent change, and holds the active agent's name and role and the reachable communities with their routing descriptions. It never holds instructions or layers.
- **Agent modality addition**: Optional guidance for one agent in one modality, added after that agent's own instructions. It belongs to exactly one agent.
- **Quiet-update setting**: An on/off switch for the quiet update on handover in voice, a service-wide setting that is on by default.
- **Progress update**: A short message from an agent about one piece of work. Its attributes are the state (one of five), the message, the work it belongs to, and when it was reported. It is shown or spoken to the user, never logged with its content.
- **Default agent instructions**: The shipped best-practice instructions for Welcome, Routing and Goodbye, applied once on first deployment.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In at least 90% of voice sessions where the caller stays silent, the first greeting audio starts within 5 seconds of the session becoming ready; the greeting may finish later. This is measured before release from the content-free greeting events (FR-035) over at least 20 voice test sessions on int.
- **SC-002**: In 100% of text sessions, a greeting is shown within 5 seconds of the session becoming ready and before the user's first message.
- **SC-003**: Zero spoken answers fail because of their length, in automated tests and in the six manual review scenarios.
- **SC-004**: In at least 5 of 6 reviewed runs (the six review scenarios, both modalities) where work lasts longer than 5 seconds, the user receives an agent-reported progress update within 5 seconds of the work starting. Progress is agent-only, so an occasional miss is accepted.
- **SC-005**: In the six review scenarios, run once by voice and once by text, the reported final state matches the actual outcome in 6 of 6 runs, and reviewers judge the substance consistent and the form suited to each modality in 6 of 6 pairs. The scenarios are:
  1. greeting;
  2. routing to a custom community;
  3. a tool or external-service call with progress;
  4. a failing call;
  5. a long, structured answer;
  6. goodbye.
- **SC-006**: 100% of reviewed text answers containing lists or tables are displayed formatted. Zero test inputs containing scripts, embedded HTML, or non-`https` links or images execute, load or become clickable.
- **SC-007**: An editor can change any behavior layer, agent addition or the voice conversation instructions and see the effect in the next new session after one publication, with no code change or redeployment.
- **SC-008**: 100% of configurations published by earlier releases load, are served and run after the upgrade.
- **SC-009**: In the six review scenarios, reviewers hear no duplicate or competing spoken acknowledgement in at least 5 of 6 voice runs. This is best effort.
- **SC-010**: Automated tests cover, on every build:
  - the greeting being sent first in both modalities;
  - spoken answers split into parts;
  - progress updates emitted and shown;
  - formatted text rendered safely;
  - layer composition and limits;
  - loading configurations from earlier releases.

## Assumptions

- **Scope.** This feature covers the voice and text sessions available on the management Test page. The text greeting also applies to any other client of the text conversation service (FR-002). There is no phone channel, and nothing here adds one.
- **Voice service.** The voice service is Azure AI Foundry GPT-Live with client delegation. In voice, the Welcome agent writes the greeting text and the speaking voice model says it, paraphrasing allowed.
  - A probe on int on 2026-11-12 confirmed that the documented greet-first sequence works.
  - The same probe confirmed that the documented acknowledgement-delay setting is rejected. That is why no delay value is offered and acknowledgement handling is best effort.
- **Roles.** "Editor" and "tester" are the existing management roles from spec 005. No new role is introduced; a separate admin role may come later.
- **Settings page.** The Settings page from spec 005 hosts the behavior layers and the quiet-update switch, next to the existing voice, reply-length and idle-timeout settings, with the same publication flow.
- **Timing.** In voice, "session becoming ready" is the moment the Test page reports the session as ready. In text, it is the moment a client opens the conversation, before any user message. Today's session startup budget is unchanged.
- **Capacity.** Concurrency for these call paths is unchanged: one active test per manager and at most 5 occupied slots, as today. The greeting adds one bounded model call per new voice session (cancelled at 5 s) and per new text conversation (fallback at 4 s). Before release, load and failure tests verify the greeting targets at this concurrency, and the int measurement (SC-001) adds real model latency.
- **Greeting language.** The greeting is English only in both modalities. More greeting languages may be added in a later feature.
- **Status lines.** Status lines remain in the text conversation history (requester choice), as opposed to transient indicators.
- **No version history.** Editable layers have no version history beyond what published configuration revisions already keep.
- **Manual review.** Consistency across modalities is judged by a manual Test-page review of the six scenarios. Mechanics are covered by automated tests with test doubles. Automated grading against real models is out of scope.
- **Replaced defaults.** The overwrite of the Welcome, Routing and Goodbye instructions is acceptable because the product is not in production.
- **Spoken updates.** Speaking every progress state, including "still working", is the requester's choice; FR-025 limits how often updates repeat.
- **Out of scope:**
  - changing the voice provider or delegation mode;
  - exact greeting wording or recorded clips;
  - guaranteed silence or application-controlled playback;
  - system verification of agent-reported outcomes;
  - formatted answer display outside the Test page text chat;
  - a before-change baseline measurement.
