# Feature Specification: Shared Conversation History Across Agents

**Feature Branch**: `007-shared-conversation-history`

**Created**: 2026-11-19

**Status**: Draft

**Input**: User description: "Use the handoff summary in `.specify\assessments\voice-with-text-agents\decision.md` to specify the approved new voice to text agent interaction concept." The approved concept is Option C: full shared conversation history through native handovers.

- Every agent reached in a conversation sees the whole conversation, including after community transfers. This covers user and agent messages, spoken exchanges that were never delegated, and earlier agents' tool calls and results.
- Agent changes use the agent framework's built-in handovers, limited to the published agent graph. The existing runtime safeguards stay in force, adjusted only where the voice session now lasts the whole call (FR-010, FR-013).
- Seeing the history grants no new authority. Agent instructions, credentials and configuration are never part of the history.

## Clarifications

### Session 2026-11-19

- Q: Should text conversations store earlier agents' tool calls and results across requests? → A: Yes. They are stored with the messages, under the same 30-day retention, and the retention and threat documentation are updated (FR-015).
- Q: What should happen when a conversation's shared history reaches the size limit? → A: Older parts are condensed, with a visible notice. Handover decisions, refusals and user confirmations are kept (FR-016).
- Q: Who approves the wider visibility of tool results and transcripts across communities before release? → A: The requester's decision is sufficient. The threat assessment and documentation are still required (FR-017).
- Q: Does this feature change how a voice delegation reaches the right agent on the text side? → A: Yes. The voice conversation can move ahead of what the agents know, so a delegation must reach the specific agent that fits the conversation as it stands, including progress made only in speech (FR-023, FR-024, scenario A9). How this is achieved is decided in the 2026-11-23 session below.
- Q: What should the voice assistant see of the shared history, after an agent change and when a session continues? → A: Only its own conversation: what was said, as transcribed, plus the delegation results and delegation updates it was given. Tool calls, tool results and handover entries stay with the working agents (FR-013). Updated 2026-11-23: the voice session is no longer replaced, and delegation updates replace the quiet context note.
- Q: When the conversation fits more than one agent equally well, what should happen to the request? → A: The agent handling it asks the user one short question to decide, then hands over. It never guesses between agents or communities (FR-011).
- Q: How is an interrupted or partly delivered answer recorded? → A: In voice, the record of what was said is the transcript of the caller and the voice assistant. An agent's answer is a delegation result handed to the voice assistant as a hint, not a transcript. The voice assistant's wording may differ from it, so the history keeps the result apart from the transcript, and a new delegation relies on the transcript for what was actually said and how the caller replied. In text there is no partial delivery: every answer is recorded as delivered in full (FR-002, FR-003, FR-004).
- Q: How should a voice caller be told that older parts of the conversation were condensed? → A: With a status line on the Test page, in both modalities. In voice it is not spoken (FR-016).
- Q: When the voice assistant delegates while the caller's latest words are still being transcribed, should the work wait for that transcription? → A: Yes, briefly: 0.5 seconds by default. Operators can change the wait through the environment configuration, within a bounded range. After the wait, work starts with what is transcribed and the rest is marked as untranscribed (FR-024).
- Q: What should happen to text conversations that were stored before this release when a user continues them? → A: They are closed at the upgrade. The next request starts a new conversation from Welcome (FR-025).

### Session 2026-11-23

- Q: Which approach gets a request to the right agent in this feature? → A: The active agent decides with the whole shared history and hands over through the agent framework's built-in handovers (option C in the plan research). A dispatch step that replaces the voice clarity check (option A) is a follow-up, decided on this feature's measurements (Out of Scope).
- Q: Should the voice session be replaced on an agent change? → A: No. One voice session lasts the whole call. Its prompt is static. At session start, and after an agent change that changes the communities that can help, an appended instruction tells it what the backend can do and what to say when nothing can, and states that it overrides earlier delegation updates (refined by the later entries on when updates are sent and on community names). If such updates interrupt the voice assistant's speech, they are not used, and the static prompt states the delegation rules and the communities upfront. The safety checks are adjusted accordingly (FR-010, FR-013).
- Q: How does a request move to another community? → A: Directly to that community's entry agent when the communities are connected. Otherwise only through Routing, and only when the agent names a community that Routing can route to. A missing connection no longer blocks a transfer through Routing (FR-008).
- Q: When may an entry agent or Routing speak to the user? → A: Handovers are quiet. Entry agents and Routing hand the request on to the best-fitting agent and speak only to resolve something unclear. An entry agent answers itself when it can handle the request and no other agent fits better (FR-026, FR-027).
- Q: Should handovers be announced to the user? → A: Not by default. An "Announce agent changes" setting turns announcements on, and agents may say who is helping when the user asks explicitly (FR-026).
- Q: How do agents know where they can hand over? → A: Shipped handover rules in the platform rules, and each agent is offered exactly its permitted destinations, described by an optional agent description or the community's routing description (FR-028, FR-029).
- Q: When does the Welcome agent hand over? → A: Immediately after its greeting, to Routing, in both modalities. Welcome does not handle requests (FR-030).
- Q: How fast must handovers be in this feature? → A: Working agents run with a very low reasoning effort for now. The delay that handovers add is measured and recorded, without a tighter limit in this feature (SC-005).
- Q: Should the acceptance runs also check that handovers stay quiet, and how strictly? → A: Yes, as best effort. With "Announce agent changes" off, at least 16 of 18 runs have no announced handover, and entry agents and Routing speak only to resolve something unclear (SC-011).
- Q: Which declared latency target do handovers have? → A: The answer after a handover starts within the existing work budget (20 s text, 30 s voice) in all runs. The added delay is recorded at the 50th and 90th percentiles (SC-005).
- Q: When is a voice delegation update sent? → A: At session start, and after an agent change only when the communities that can help have changed (FR-013).
- Q: Does the voice assistant need the names of the communities? → A: No. Following the delegation policy of the GPT-Live prompting guide, it gets only what the backend can do: the routing descriptions of the communities that can help. A community is listed by its name only when its routing description is empty. The agents choose the community. If not all fit, it is told that more exist and to delegate other requests too (FR-013).
- Q: May Routing hand over to Goodbye? → A: Yes, as today (FR-008).
- Q: Is a separate "handover requested" event needed? → A: No. Every attempt ends in exactly one applied or refused event; request details are available in the application logs (FR-018).
- Q: Which replies count as user confirmations when a long conversation is condensed? → A: The user's or caller's next entry after an agent answer or delegation result that ends with a question mark, or after a "waiting for input" progress update (FR-016).
- Q: What do the Test page's audio controls do? → A: The caller interrupts the voice assistant by speaking over it. The voice model is full duplex, so there is no Interrupt button. One switch turns the microphone on or off, and one turns the loudspeaker on or off. Both act only in the browser and send no message of their own to the runtime. The voice session and the agents are not affected, and progress stays visible in the chat (FR-013).
- Q: Must the voice assistant stay quiet when it delegates? → A: No. It may tell the caller that it is checking something, as in feature 006. Quiet handovers apply to the agents that work on the request, including when they work on a voice delegation (FR-026).
- Q: Does a text conversation that restarts from Welcome greet again? → A: No. It moves straight to Routing with the history kept (Edge Cases).

## User Scenarios & Testing *(mandatory)*

The stories below use one acceptance set of nine scenarios (A1–A9, see [Acceptance Scenario Set](#acceptance-scenario-set)). Each is run once by voice and once by text with the same published configuration.

### User Story 1 - Text agents continue the conversation after a handover (Priority: P1)

A tester chats with the service in text. The Welcome agent hands over to Routing, and Routing hands over to a community agent. The tester gives an order number, and the community agent hands the work to a colleague in the same community. The tester then asks "What is its status?". The colleague answers about that order without asking for the number again, because it sees the whole conversation: every message, and the earlier agent's lookups and their results.

**Why this priority**: Today every agent change resets the receiving agent to a short summary, so follow-ups that depend on earlier messages fail. Text is the simplest place to prove that the whole conversation carries over.

**Independent Test**: In a text conversation, take scenarios A1, A3 and A6 through at least one same-community handover and one community transfer. Check that each receiving agent uses facts from earlier in the conversation without asking for them again.

**Acceptance Scenarios**:

1. **Given** a text conversation in which the user gave an order number to agent A, **When** agent A hands over to agent B in the same community and the user asks a follow-up that only refers to "it", **Then** agent B answers about the same order without asking for the number.
2. **Given** agent A looked something up with a tool and told the user the result, **When** agent B takes over, **Then** agent B can use that result without repeating the lookup, and can tell the user what was found.
3. **Given** a conversation that moved from community X to community Y, **When** the user refers to something said while community X was active, **Then** the agent in community Y understands the reference.
4. **Given** the user continues the same text conversation in a later request, **When** an agent change happened in the earlier request, **Then** the active agent still sees the whole conversation, not only the part since the last agent change.

---

### User Story 2 - Voice agents know the whole spoken conversation (Priority: P1)

A caller talks to the service by voice. Before asking for anything, they chat with the voice assistant and mention their name and that they are calling about last month's invoice. Nothing is delegated during this exchange. When they then say "Can you check why it was so high?", the working agent understands "it" as last month's invoice, because the spoken exchanges are part of the conversation it sees. Later, the caller interrupts a long answer by speaking over it. The next agent knows which part of that answer the caller actually heard.

**Why this priority**: Today most spoken exchanges never reach the working agents. Voice requests that depend on earlier speech are rejected as unclear or answered out of context. This asymmetry with text is the root of the reported problem.

**Independent Test**: Run scenarios A4 and A5 by voice. Check that the working agent's answer uses facts the caller only said to the voice assistant, and that the record of an interrupted answer holds only what the voice assistant actually said before the interruption.

**Acceptance Scenarios**:

1. **Given** the caller mentioned a fact in a spoken exchange that was never delegated, **When** a later delegated request depends on that fact, **Then** the working agent uses it without asking again.
2. **Given** a delegated voice request whose meaning depends on earlier speech (for example, "and the second one?"), **When** it reaches the working agent, **Then** it is understood from the conversation and is not rejected as unclear only because it is not self-contained.
3. **Given** the caller interrupts a spoken answer, **When** a later agent looks at the conversation, **Then** it sees what the voice assistant actually said, as transcribed up to the interruption, and the earlier agent's delegation result kept apart as a result, not as something said to the caller.
4. **Given** the active agent changes during a voice session, **When** the caller keeps talking, **Then** the conversation continues without a new greeting or self-introduction, and the voice assistant still remembers what was said before the change.

---

### User Story 3 - The right agent handles the current request (Priority: P1)

A caller is being helped by a billing agent and suddenly asks about a technical fault. The request is handed over along the permitted path, so it reaches a technical-support agent rather than staying with the billing agent just because billing was active last. When the caller later says "Let's get back to the invoice", the conversation returns to a billing agent, which picks up where the earlier one stopped.

**Why this priority**: This is the reported failure: voice delegation stays with the most recently active agent even after the conversation has moved on. Reaching the right agent is what makes a shared conversation useful.

**Independent Test**: Run scenarios A2, A3, A8 and A9 in both modalities. Check that each request reaches the expected agent and community over a permitted path, that A8 no longer stays with the previously active agent, and that A9 reaches the specific agent the spoken conversation points to.

**Acceptance Scenarios**:

1. **Given** a community agent is active and the user changes to a topic owned by another community, **When** the request is handled, **Then** it is handed over to an agent of the other community in the same piece of work, directly through that community's entry agent when the communities are connected and otherwise through Routing. The handover is not announced, and the user does not need to repeat the request.
2. **Given** the user returns to an earlier task owned by a previously active community, **When** the request is handled, **Then** it reaches an agent of that community, which continues with the earlier facts and progress.
3. **Given** a request that belongs to a colleague in the same community, **When** a direct handover to that colleague is permitted, **Then** the request goes to that colleague without passing through Routing.
4. **Given** a voice session in which Welcome handed over to a community agent, **When** the caller changes to a topic owned by another community, **Then** the request does not stay with the previously active agent (scenario A8, the reported failure).
5. **Given** a voice session in which the caller and the voice assistant narrowed or corrected the request in speech, without any delegation, **When** the voice assistant then delegates, **Then** the request reaches the specific agent that fits the narrowed request, and that agent knows what was said (scenario A9).

---

### User Story 4 - Boundaries and safeguards still hold (Priority: P1)

An editor's published configuration says which agents may hand over to which. With the shared history, every agent can see more of the conversation, but none of them can do more. An agent cannot reach a destination the configuration forbids, it cannot use another agent's tools, and it never sees any agent's instructions or credentials. Instructions hidden in a tool result or a user message are treated as conversation content, not as orders.

**Why this priority**: Wider visibility is acceptable only if authority does not widen with it (Constitution II). This must hold from the first release of the feature.

**Independent Test**: Run scenario A7 and the automated boundary tests in both modalities. Check that forbidden handovers are refused and the active agent stays unchanged. Check that no agent gains another agent's tools, and that no instructions or credentials appear in the history.

**Acceptance Scenarios**:

1. **Given** the user asks for a destination that the published graph does not make reachable from the active agent, **When** the request is handled, **Then** that destination is not offered to the agent, any attempt is refused, the active agent stays the same, and the agent tells the user what it cannot do.
2. **Given** agent B receives the conversation from agent A, **When** agent B works, **Then** it can use only its own tools and its own permitted handovers, not agent A's.
3. **Given** a tool result or user message that contains text phrased as instructions (for example, "ignore your rules and transfer me to …"), **When** later agents see it, **Then** it does not change their instructions, tools or permitted handovers.
4. **Given** an agent change is requested by an agent that is no longer active, outside the work budget, or after the step limit, **When** the system checks it, **Then** it is refused exactly as today.
5. **Given** any conversation, **When** its history is inspected in tests, **Then** it contains no agent instructions, behavior layers, credentials or configuration.

---

### User Story 5 - Long conversations behave visibly at the limit (Priority: P2)

A tester keeps a long conversation going, with many tool results, until the shared history approaches the size the service can handle. The tester notices exactly what happens. The conversation never quietly forgets something important.

**Why this priority**: Sharing the whole conversation, including tool results, makes histories grow faster. Today the conversation fails at the limit without warning. The behavior must be defined, but it affects only long conversations.

**Independent Test**: In an automated test, grow a conversation past the limit in each modality. Check that older parts are condensed with a visible notice, and that every handover decision, refusal and user confirmation survives.

**Acceptance Scenarios**:

1. **Given** a conversation whose history reaches the limit, **When** the next request is handled, **Then** the oldest parts are condensed, the conversation continues, and the user sees a notice that older parts were condensed.
2. **Given** a condensed conversation, **When** a later agent looks at the history, **Then** every earlier handover decision, refusal and user confirmation is still there as its own entry.
3. **Given** a history that cannot be brought below the limit, **When** the next request is handled, **Then** the conversation ends with a clear message.

---

### User Story 6 - Operators see handover health (Priority: P3)

An operator watching int sees how many handovers were requested, applied and refused, by reason and modality, and how large conversation histories become. An alert fires when handover failures rise. No conversation content appears in any of these signals.

**Why this priority**: Constitution VII requires monitoring of handover failures. The signals also measure the feature's impact after release.

**Independent Test**: Run scenarios in both modalities on int. Check that each handover produces the expected content-free event, and that the documented query and alert show them.

**Acceptance Scenarios**:

1. **Given** a handover is applied or refused, **When** telemetry is inspected, **Then** one content-free event records the outcome, reason, modality and source and destination roles.
2. **Given** handover failures exceed the documented threshold, **When** monitoring evaluates them, **Then** the documented alert fires and links to the runbook.

---

### Acceptance Scenario Set

Each scenario runs once by voice and once by text (18 runs). The scenarios use a synthetic published configuration with two routable communities (Billing and Technical Support), each with two agents, plus one community that is not routable. Each community's first agent is its entry agent. Billing's two agents are connected by a permitted direct handover. Billing is connected to Technical Support, so that transfer goes directly to Technical Support's entry agent. Technical Support has no connection back to Billing, so a return goes through Routing.

| ID | Scenario | Expected destination | Context the destination must use |
|----|----------|----------------------|----------------------------------|
| A1 | Follow-up after a same-community handover | The second Billing agent | The order number the user gave the first agent |
| A2 | Topic change to another community | A Technical Support agent, through its entry agent (direct connection) | The fault the user described in the earlier Billing exchange, if referred to |
| A3 | Return to an earlier task | A Billing agent, through Routing | The invoice details and progress from the earlier Billing exchange |
| A4 | Fact mentioned only in conversation before delegation | The agent handling the delegated request | The name and invoice month the user mentioned before any delegation |
| A5 | Interrupted answer (voice: the caller speaks over it); follow-up to a long answer (text) | The next agent that answers | Voice: what the voice assistant actually said before the interruption and how the caller replied. Text: the whole earlier answer |
| A6 | Tool result reused after a handover | The receiving agent | The earlier agent's lookup result, without repeating the lookup |
| A7 | Request for a destination that is not reachable (the community that is not routable) | Stays with the active agent; no handover | Nothing new; the agent explains what it cannot do |
| A8 | Reported failure: topic change after Welcome handed over (voice), mirrored in text | A Technical Support agent, not the previously active Billing agent | The new request, without asking the user to repeat it |
| A9 | Conversation moved ahead in speech: while the first Billing agent was last active, the user and the voice assistant narrow the request, without delegating, to a topic owned by the second Billing agent, and correct one earlier fact | The second Billing agent | The narrowed request and the corrected fact, not the earlier value |

In text, A4 uses facts mentioned in earlier messages that Routing answered with a clarifying question before handing the conversation over. A5 sends a follow-up after a long answer, which is recorded as delivered in full. A9 gives the same narrowing and correction in messages to the first Billing agent.

### Edge Cases

- **Handover refused mid-work.** The active agent stays in charge, sees the refusal in the history, and answers the user within the same work budget.
- **Handover chain in one request** (Routing, then a community agent, then a colleague). Every step is checked as today, and the whole chain stays within the work budget and step limit. Each agent in the chain sees the whole history.
- **Return to an agent that was active earlier.** That agent sees the whole conversation, including everything that happened while it was not active, and not only its own earlier part.
- **Caller interrupts during an agent change in voice.** Speech heard before the interruption is recorded. The new request goes to the agent that is active once the change completes.
- **Caller speaks while no agent is working.** These exchanges are recorded with the voice assistant's spoken answers, as transcribed.
- **Microphone or loudspeaker switched off on the Test page.** The switches send no message of their own to the runtime. What the voice assistant says while the loudspeaker is off is still recorded as said. While the microphone is off, nothing reaches the voice assistant, so nothing is recorded for the caller.
- **Delegation update while the voice assistant is speaking.** If appended instructions interrupt its speech, the updates are not used, and the static prompt carries the delegation rules (FR-013).
- **No community can help.** The active agent, or the voice assistant following its delegation update, tells the user briefly what cannot be done. No handover happens.
- **Community without a connection.** The request can still reach it through Routing if Routing can route there. Otherwise it is not offered as a destination.
- **Caller corrects earlier information in speech before delegating.** The corrected value is what the destination uses. The earlier value stays in the history as said, not as current.
- **Request fits more than one agent equally well.** The agent handling it asks the user one short question, then hands over to the agent the answer points to. Asking counts as correct behavior in SC-001; guessing a destination that turns out wrong counts as a failure.
- **Transcription is missing or partial.** Only what was transcribed is recorded, marked as speech. Nothing is invented to fill the gap.
- **Tool fails or returns an error.** The failed call and the error are part of the history, so a later agent does not assume it succeeded.
- **Untrusted content in history** (prompt injection in a tool result or user message). It stays conversation content and never changes any agent's instructions, tools or permitted handovers.
- **Published configuration changes during a conversation.** Running conversations keep their configuration, as today. A destination that is no longer permitted in a new conversation is refused there.
- **Agent removed from the graph between text requests.** The stored position is revalidated as today. If it is no longer valid, the conversation restarts from Welcome with the history kept, without a new greeting: Welcome moves straight to Routing (FR-030).
- **Very long single tool result.** It is bounded like any other entry, and the limit behavior of FR-016 applies.
- **Earlier configuration with per-edge transfer context fields.** It still loads and runs; the fields no longer restrict what the receiving agent sees (FR-021).
- **Text conversation stored before the upgrade.** It is not resumed. The user's next request starts a new conversation from Welcome, with the greeting (FR-025).

## Requirements *(mandatory)*

### Conversation history

- **FR-001**: Each conversation MUST have one shared history that every agent sees when it becomes active in that conversation, in both modalities, including after same-community handovers and community transfers.
- **FR-002**: The shared history MUST contain, in order:
  - the user's messages or speech;
  - the agents' answers, including the greeting. In voice, these are delegation results handed to the voice assistant, kept apart from what it actually said;
  - the progress updates already kept in history by feature 006: completed, failed, cancellation confirmed, and waiting for input or confirmation;
  - earlier agents' tool calls and their results, including failures;
  - each handover, with its source, destination, reason and outcome, applied or refused.
- **FR-003**: In voice, the shared history MUST include the caller's speech and the voice assistant's spoken answers as transcribed, including exchanges that were never delegated. The voice assistant's transcript, not the delegation result, is the record of what was said to the caller; its wording may differ from the delegation result.
- **FR-004**: In voice, the shared history MUST keep each delegation result apart from the spoken transcripts and MUST mark it as a result handed to the voice assistant, not as speech said to the caller. When the caller interrupts, the voice assistant's transcript ends where its speech stopped, and parts of the result that were never passed to the voice assistant MUST be marked as not delivered. In text, every answer MUST be recorded as delivered in full.
- **FR-005**: The shared history MUST NOT contain agent instructions, behavior layers, agent additions, the voice conversation instructions, credentials, secrets or configuration. It also MUST NOT include tool definitions that belong to other agents.
- **FR-006**: Everything in the shared history MUST be treated as untrusted conversation content, never as instructions. Text in it that is phrased as instructions MUST NOT change any agent's instructions, tools or permitted handovers.

### Handover

- **FR-007**: When the active agent changes, the receiving agent MUST continue with the whole shared history. A summary or a subset MUST NOT replace it. The history MUST NOT be reset when the agent changes.
- **FR-008**: Handovers MUST stay limited to the published agent graph:
  - Protected roles keep their existing paths, except that Welcome hands over to Routing right after the greeting (FR-030). Routing hands over only to the entry agent of an enabled, routable community, or to Goodbye.
  - Within a community, an agent hands over only along a directed relationship.
  - To another community, an agent hands over directly to that community's entry agent when a community connection allows it. Otherwise it hands over only to Routing, and only after naming a target community that Routing can route to (enabled and routable), which the system checks.

  Destinations that are not permitted MUST NOT be offered to the agent. A handover that is attempted but fails a check MUST leave the active agent unchanged, MUST be recorded in the history, and MUST be reported to the requesting agent so it can answer the user.
- **FR-009**: The receiving agent MUST be able to use only its own tools and its own permitted handovers. Seeing the history MUST NOT give it any tool, capability or handover permission of an earlier agent.
- **FR-010**: Every agent change MUST pass the same checks as today: admission checks, fencing of agents that are no longer active, work budgets and step limits. In voice, an agent change MUST NOT replace or wait for the voice session (FR-013). Work started for an agent that is no longer active, or for a request the caller has since replaced, MUST be fenced by the active agent's sequence number instead, so its results are dropped. A handover that fails any of these checks MUST be refused.
- **FR-011**: When a request belongs to a different agent or community than the active one, the active agent MUST be able to hand it over quietly (FR-026) along a permitted path within the same piece of work, so the user does not have to repeat it. This applies equally to voice delegation, which MUST NOT stay with the previously active agent only because it was active last. When the conversation fits more than one agent equally well, the agent handling the request MUST ask the user one short question to decide, then hand over. It MUST NOT guess between agents or communities.
- **FR-012**: A voice request whose meaning depends on earlier exchanges MUST be understood using the shared history. It MUST NOT be rejected as unclear only because it is not self-contained. The check of whether a delegated voice request is clear enough to act on MUST use the whole shared history.
- **FR-013**: In voice, one voice session MUST last the whole call. It MUST NOT be replaced on an agent change, so the voice assistant never forgets the conversation, and it MUST NOT greet or introduce itself again. The caller interrupts the voice assistant by speaking over it; the Test page has no Interrupt button. Its microphone switch only stops or resumes recording, and its loudspeaker switch only silences or restores playback on the user's device. Both act only in the browser and send no message of their own to the runtime, so they do not affect the voice session, the agents or the history, and progress stays visible in the chat. The voice assistant's prompt is the static voice conversation instructions. At session start, and after an agent change that changes which communities can help, the system MUST send a delegation update as an appended instruction:
  - It lists what the backend can do from the current position (directly or through Routing): the routing description of each community that can help, without community names, except that a community with an empty routing description is listed by its name. It states how to respond when nothing listed fits; if not all communities fit in the update, it instead says that more exist and that other requests are delegated too.
  - It states that it overrides all earlier delegation updates.

  If verification on int shows that appended instructions interrupt the voice assistant's speech, delegation updates MUST NOT be used. The static prompt then lists, by the same rules, what all routable communities of the published configuration can do. The voice assistant MUST see only its own conversation: what was said, and the delegation results and updates it was given. Tool calls, tool results and handover entries MUST NOT be given to it.
- **FR-014**: The same published configuration MUST produce the same handover rules and the same kinds of history content in voice and in text. Only how answers are presented may differ, as feature 006 defines.
- **FR-023**: When the voice assistant delegates a request, the request MUST reach the specific agent that should handle it given the conversation as it stands at that moment. This includes progress made only in speech, such as the caller narrowing the topic or correcting earlier information. Reaching an agent only because it was active last, or an agent that is permitted but wrong for the current context, counts as a failure. The destination MUST be reached along a permitted path (FR-008 to FR-010), and the voice assistant MUST NOT receive agent instructions or any new authority to achieve this (FR-022).
- **FR-024**: Before the agent that handles a delegated voice request starts its work, the shared history MUST contain every spoken exchange up to that delegation, as transcribed, so the agents are not behind the voice conversation. If the caller's latest speech is still being transcribed, the work MUST wait for it for at most the configured transcription wait: 0.5 seconds by default, changeable by operators through the environment configuration within a range of 0 to 2 seconds. After the wait, the work starts with what is transcribed, and the rest MUST be marked as untranscribed, as for any missing transcription.
- **FR-026**: Handovers MUST be quiet by default. The user is not told that the agent changed, and the receiving agent answers within the same piece of work. An editor setting, "Announce agent changes" (off by default), MUST let agents announce changes. Agents MAY also say which agent or team is helping when the user explicitly asks. The Test page MAY still show testers which agent is active. This applies to handovers between agents, including while they work on a voice delegation. The voice assistant MAY still tell the caller that it is checking something when it delegates, as in feature 006.
- **FR-027**: When a request reaches a community's entry agent or Routing by handover, that agent MUST hand it on quietly to the best-fitting permitted destination. It MUST speak to the user only to resolve something unclear. An entry agent MAY answer the request itself when it can handle it and no other agent in its community fits better.
- **FR-028**: The platform rules of both modalities MUST include shipped handover rules:
  - check whether a request belongs to you;
  - hand over to a better-fitting permitted destination without telling the user;
  - never guess between destinations, and ask one short question instead (FR-011);
  - hand over to Routing only for a community that Routing can route to.

  Editors can change these rules like any platform rule (feature 006). Each agent MUST be offered exactly its permitted destinations, each described by the destination agent's description or the destination community's routing description.
- **FR-029**: Each agent MAY have an optional description of what it handles. Editors can edit it, and it is used only to describe the agent as a handover destination. Configurations without it MUST still load and run (FR-021).
- **FR-030**: In both modalities, the Welcome agent MUST hand over to Routing immediately after its greeting has been delivered (in voice: handed to the voice assistant), skipped or replaced by the fallback. The Welcome agent MUST NOT handle user requests.

### Persistence and limits

- **FR-015**: A text conversation MUST continue across requests with the whole shared history, including the parts from before earlier agent changes. Tool calls and results MUST be stored with the messages, under the same 30-day retention. Voice history MUST remain held in memory only for the session, as today.
- **FR-016**: When a conversation's shared history reaches its size limit or its entry limit, the system MUST condense the oldest parts into a shorter account and continue the conversation. The condensed account MUST keep every handover decision, refusal and user confirmation as its own entry. A user confirmation is the user's or caller's next entry after an agent answer or delegation result that ends with a question mark, or after a "waiting for input" progress update. It MUST be marked as condensed, and it stays untrusted content like the rest of the history. The user MUST see a clear notice whenever older parts are condensed: a status line on the Test page in both modalities, which is not spoken in voice. If the history cannot be brought below the limit, the conversation MUST end with a clear message instead of failing without one.

### Security, observability and documentation

- **FR-017**: A threat assessment MUST cover the wider visibility before release. It covers transcripts, tool results across communities, prompt injection through tool results and stored history, and retention. The security documentation MUST state that communities are routing and permission boundaries, not information boundaries. The requester's decision is the release approval for the wider visibility; no separate sign-off is needed, but the threat assessment and documentation MUST exist before release.
- **FR-018**: The system MUST record content-free events for each of these:
  - a handover applied or refused (every attempt ends in exactly one of these), with reason code, source and destination roles, and modality;
  - a voice delegation received, with the role of the agent that first received it, the role of the agent that handled it, whether it was handed on, and whether any spoken exchange up to the delegation was still untranscribed;
  - the history limit being reached, with modality and the behavior applied;
  - the size of the history at each agent change, recorded in size ranges.

  These events MUST NOT contain messages, transcripts, tool arguments or results, instructions or configuration.
- **FR-019**: Monitoring MUST show handover outcomes by modality and reason. An alert with a documented threshold and runbook MUST fire when handover failures rise.
- **FR-020**: The documentation MUST describe the shared history, its content and limits, and the handover flow in voice and text. It MUST include Mermaid sequence diagrams of a same-community handover, a direct community handover, a transfer through Routing, and a voice delegation with its delegation update. The text-history documentation MUST describe what is stored and for how long.

### Compatibility

- **FR-021**: Configurations published by earlier releases MUST continue to load, be served and run. The per-edge setting for which transfer context fields may pass MUST no longer limit what the receiving agent sees. Publishing a configuration that contains it MUST NOT fail, and the editor MUST NOT present it as a privacy control. Community connections in earlier configurations keep their meaning as permitted paths; they now lead directly to the target community's entry agent (FR-008).
- **FR-022**: Feature 006 behavior MUST be kept:
  - the greeting rules;
  - spoken answers in parts;
  - progress updates;
  - formatted text;
  - the behavior layers;
  - the voice assistant's service-wide instructions, which never include agent instructions. The quiet context note is replaced by the delegation updates of FR-013.
- **FR-025**: Text conversations stored before this release MUST be closed at the upgrade and MUST NOT be resumed. A user's next request MUST start a new conversation from Welcome, with the greeting. The closed conversations are not shown to any agent and expire under the existing retention.

### Key Entities

- **Shared conversation history**: The ordered record of one conversation, visible to every agent active in it. It holds entries of these kinds: user turn, agent answer, spoken exchange, delegation result (voice), tool call and result, progress update in a final or waiting state, and handover. It belongs to one conversation and one owner. It is never shared between conversations. In text it is persisted; in voice it is held in memory only.
- **History entry**: One item in the shared history. It records its kind, the agent and community active at the time, and the content. For voice it also records whether it is a transcript of what was said or a delegation result handed to the voice assistant; parts of a result that were never passed on are marked not delivered. Its content is untrusted.
- **Handover**: The change of the active agent from a source to a destination. It records the reason, the path (within a community, directly to a connected community's entry agent, or through Routing) and the outcome (applied or refused, with a reason code). It is allowed only along the published agent graph.
- **Published agent graph**: The existing configuration of agents, roles, communities and permitted handovers. It decides which handovers are possible: community connections mark direct paths, and routable communities are also reachable through Routing. Agents may carry an optional description that describes them as destinations. The graph does not decide what history the destination sees.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In all 18 runs of the acceptance set, the request reaches the expected destination over a permitted path. A destination that is technically allowed but wrong for the context counts as a failure.
- **SC-002**: In all 18 runs, the destination uses the context listed for the scenario without asking the user to repeat it.
- **SC-003**: In all 9 voice/text pairs, both runs reach the same destination and the same substantive outcome. Wording and presentation may differ.
- **SC-004**: There are zero handovers or actions outside the published permissions across the acceptance runs and the automated boundary tests. Automated tests find zero agent instructions, layers, credentials or configuration in any shared history.
- **SC-005**: In 100% of the acceptance runs on int and of the load tests, the answer after a handover starts within the existing work budget (20 s in text, 30 s in voice). The delay that handovers add before the answer starts is also measured at the 50th and 90th percentiles, per path (within a community, direct to another community, through Routing) and per modality. It is recorded in the capacity documentation, together with model input size per turn and model cost per conversation. A tighter limit is left to a follow-up that uses these results (Out of Scope).
- **SC-006**: In every automated test that grows a conversation past the limit, older parts are condensed with a visible notice, the conversation continues, and every handover decision, refusal and user confirmation remains in the history.
- **SC-007**: In every automated voice test with an interruption, the history holds the voice assistant's speech only as transcribed up to the interruption, keeps the delegation result apart as a result, and marks parts never passed to the voice assistant as not delivered.
- **SC-008**: All configurations published by earlier releases load, are served and run after the upgrade. In upgrade tests, every text conversation stored before the upgrade starts a new conversation from Welcome on its next request.
- **SC-009**: Every applied or refused handover and every voice delegation in the acceptance runs appears in monitoring as a content-free event within 5 minutes. Tests show that no conversation content reaches telemetry.
- **SC-010**: In every automated voice test with a delegation, the agent that handles it sees every transcribed spoken exchange up to the delegation, including exchanges that were never delegated and corrections made in speech. Zero exchanges are missing.
- **SC-011**: With "Announce agent changes" off, at least 16 of the 18 acceptance runs have no announced handover, and entry agents and Routing speak to the user only to resolve something unclear. This is best effort.

## Assumptions

- **Approach.** This feature follows the assessment's approved concept (Option C). Agent changes use the agent framework's built-in handovers, limited to the published graph. Planning found no critical blocker. A check that fails at the moment a handover is applied is caught and reported to the requesting agent (FR-008).
- **Scenario set.** The nine scenarios A1–A9 are the agreed acceptance set. A8 represents the reported failure. No sanitized failing conversation was supplied, so A8 rebuilds it from the report: delegation stayed with the most recently active agent after the topic changed. A9 covers the related case the requester described: the voice conversation moved ahead of what the agents knew.
- **Delegation targeting.** In this feature the active agent decides where a request belongs (FR-023), using the whole shared history, quiet built-in handovers and the destinations it is offered. This is option C in the plan research. The research also documents the other options, including a dispatch step that replaces the voice clarity check (option A), as follow-up candidates. A follow-up spec decides on them using this feature's measurements:
  - the delay that handovers add (SC-005);
  - wrong destinations and repeated questions in the acceptance runs;
  - how often voice requests are still rejected as unclear.
- **Response speed.** Working agents, the voice clarity check and the greeting run with a very low reasoning effort for now, to keep voice answers quick. Operators can change it through the environment configuration.
- **Feature 006 is complete.** Its service-wide voice instructions and per-modality layers are in place, and this feature builds on them. Its quiet context note is replaced by the delegation updates of FR-013.
- **Modality.** Each conversation stays in one modality. Switching between voice and text inside one conversation, and memory across conversations, are out of scope.
- **Voice is volatile.** Voice audio is never recorded or stored, and voice history lives only for the session, as today.
- **Existing limits stay.** Text conversations keep their 30-day retention, which is reset on every update. The work budgets (20 s for text and 30 s for speech) and the step limit stay unchanged unless planning shows they must change. Any change to them must be documented.
- **Capacity.** Concurrency is unchanged: one active test per manager and at most 5 occupied slots. Larger model inputs raise latency and cost. SC-005 declares the latency target and records the delay; the owner accepts the measured cost. Condensing older parts (FR-016) adds one bounded model call, and only when a history reaches the limit.
- **Measuring impact.** Impact after release is measured with the acceptance set on int and with the content-free handover events (FR-018). No affected-session baseline exists.
- **Per-edge context fields.** The existing per-edge transfer context fields lose their effect. Earlier configurations keep them, but they no longer filter anything.

## Out of Scope

- Switching voice and text within one ongoing conversation, and memory across conversations.
- Sharing agent instructions, behavior layers, credentials or configuration in the history.
- Giving a receiving agent an earlier agent's tools, capabilities or handover permissions.
- Recording or storing voice audio, or keeping voice history beyond the session.
- A voice-only agent system, or replacing the shared agent setup with a managed delegation product.
- Feature 006 behavior beyond what this feature depends on: greeting, formatting, progress display and layers.
- Telephony, production capacity or cost certification, and legal-compliance claims.
- Exercising the MCP path of the feature 006 review scenarios. That needs an MCP server, which is a separate concern.
- A dispatch step that replaces the voice clarity check (option A), and other handover latency optimisations. A follow-up spec decides on them using this feature's measurements (Assumptions: Delegation targeting).
