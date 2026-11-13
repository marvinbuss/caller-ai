# Concept: Shared Conversation Continuity Across Voice and Text

- **Slug**: voice-with-text-agents
- **Created**: 2026-11-13
- **Revised**: 2026-11-13 after requester decisions. This revision supersedes the earlier provisional recommendation of Option B.
- **Recommended option**: Option C - Full shared conversation history through native handoffs
- **Inputs used**: [problem definition](problem.md), [research](research.md), [intake](intake.md), requester decisions on 2026-11-13
- **Investment appetite**: Unlimited, as requested. Available budget must not determine the recommendation. No small/medium/large timebox or delivery estimate is assigned.

## Framing

The outcome is the same configured multi-agent system reaching the expected permitted agent and preserving relevant conversation context in separate voice and text conversations. Switching modalities within one conversation remains out of scope. [Source: problem, Confirmed Scope & Requester Clarifications and Success Metrics.]

The problem definition asked for an explicit decision on which conversation information may cross agent and community boundaries. The requester has now made that decision (below). This concept stays at the concept level; how handoffs, history and safeguards are engineered belongs to `/speckit-specify` onward.

## Requester Decisions (2026-11-13)

1. **Share the full conversation history.** Every agent reached in a conversation receives the whole conversation so far, including across community transfers.
2. **Include tool activity.** Earlier agents' tool calls and capability results are part of the shared history.
3. **Include spoken exchanges.** What the caller and the voice model said without delegating is part of the shared history.
4. **Use native handoffs.** The Agents SDK's native handoff feature should carry control and history between agents to simplify the effort.
5. **Unlimited appetite.** Address the problem in the best possible technical way, not the cheapest.

Rationale given: sharing the full history "makes a ton of sense and should be done to ensure a smooth conversation." These decisions are the explicit information-sharing contract the problem definition required (problem Goal 4, Q4). Formal acceptance by any separate information-policy owner remains open (problem Q8).

## Options

### Option A - Existing modality work only; no separate continuity capability

- **Sketch**: Rely on the already-defined feature 006 improvements, which tell the speaking model the active agent and reachable communities, without changing what working agents know after a handover.
- **Appetite**: Uncapped; conceptually the narrowest.
- **Trade-offs**: Avoids a second behavioral contract, but knowing the active agent is not knowing the conversation. It leaves the agent-change history reset and the missing spoken exchanges untouched, and contradicts the requester's decision to share the full history.
- **Rabbit holes**: Treating unfinished feature 006 work as delivered; declaring success from mechanical handover tests.

### Option B - Conversation-aware agents within existing information boundaries

- **Sketch**: Improve how agents interpret the current request and permitted progress, but keep today's transfer filtering: a destination receives only a permitted summary and transfer reason.
- **Appetite**: Uncapped.
- **Trade-offs**: Preserves the current isolation, but that isolation turned out to be an application policy rather than a product requirement. Summaries lose conversational meaning that follow-ups and returns to earlier tasks depend on. The requester has rejected this boundary.
- **Rabbit holes**: Encoding ever-richer summaries that approximate the full history anyway.

### Option C - Full shared conversation history through native handoffs

- **Sketch**: A conversation is one continuous record that every participating agent sees. When the active agent hands over, the receiving agent continues as if it had been present from the start: it sees every user and assistant message, including exchanges spoken in voice without delegation, and earlier agents' tool calls and results. Handover itself is a standard native handoff from the active agent to a permitted destination. In voice, an agent change should not reset what the conversation knows; the caller experiences one continuous conversation. Text and voice share the same conversational record and handoff behavior, so the same configured agents behave equivalently in both modalities.
- **What stays restricted**: The published agent graph still decides which destinations each agent may hand over to. Receiving the history does not grant the receiving agent the previous agents' tools, capabilities or handover permissions. Agent instructions, credentials and configuration are not part of the conversation history.
- **Appetite**: Uncapped; no timebox or delivery estimate.
- **Trade-offs**:
  - *Wins*: Directly addresses all four problem goals. Agents can resolve follow-ups and returns to earlier tasks. Voice and text converge on one record. Native handoffs follow the SDK's default behavior (the receiving agent sees the prior history) instead of maintaining custom transfer filtering.
  - *Sacrifices*: Communities stop being information boundaries; they remain routing and permission boundaries. Capability results become visible to agents in other communities. The existing filtered-handover test and the security documentation that describes communities as context boundaries must change deliberately.
  - *Risks*: Longer contexts raise latency and model cost and approach the current 256 KiB conversation limit sooner, which fails rather than trims today. More content means more stale, irrelevant or untrusted material, including injected content in tool results, reaches more agents.
- **Rabbit holes** (where scope can blow up):
  - **Native handoffs versus application authority.** Today every agent change passes admission checks, agent-epoch fencing, work budgets and step limits, and in voice retires and replaces the speaking session. Native handoffs must keep these guarantees rather than bypass them. The installed SDK (openai-agents 0.24.0) provides `on_handoff` (can reject before the transfer takes effect), `is_enabled`, per-destination handoffs, `input_filter` and history mapping, so this appears feasible but is unverified for this runtime.
  - **Cross-community transfers.** Today these pass through the Routing agent with a pending destination; expressing that path with native handoffs needs care.
  - **Executor loop.** The current executor runs one model turn per step and applies transitions itself; native handoffs change that control flow.
  - **Voice transcript fidelity.** Spoken history must reflect what was actually said and heard, including interruptions and unspoken answer parts, not just what was generated.
  - **Context growth.** Long conversations may outgrow the limit. Compaction exists in the SDK only as an opt-in beta; any bounding must not silently drop security-relevant context.
  - **Text persistence.** Stored text history today holds only user and assistant text and never tool payloads. Continuing a text conversation across turns with tool results in its history would extend what is persisted, which changes the documented retention and threat assessment.
  - **Speaking-session continuity.** Today each agent change replaces the voice session, which starts without prior conversation. Feature 006 makes voice instructions service-wide, which may remove the need for per-agent replacement; the two efforts must be reconciled.

## Boundary Comparison

| Question | Option B: preserve current boundaries | Option C: full shared history (recommended) |
|---|---|---|
| What the receiving agent sees | A filtered summary and transfer reason | The entire conversation: messages, spoken exchanges, tool calls and results |
| Communities | Routing, permission and information boundaries | Routing and permission boundaries only |
| Handover and tool permissions | Unchanged | Unchanged; seeing history grants no new authority |
| Handover mechanism | Application-implemented transfer with filtering | Native SDK handoffs, constrained by the published graph and runtime safeguards |
| Main limitation | Agents cannot use earlier facts they were not given | Larger, less-curated contexts and wider visibility of tool results |

## Recommendation

**Recommend Option C**, on the requester's explicit decision and on technical merit:

- It removes the root asymmetry the research found: agent changes reset history in both modalities, and voice keeps most spoken exchanges outside the agents' history.
- A single conversational record shared by every agent is the most direct way to meet the success bar: the expected permitted agent, the relevant context preserved, and equivalent outcomes in voice and text.
- Native handoffs match the SDK's default history behavior, so the product no longer maintains custom transfer filtering that worked against continuity.

**Conditions** that the specification must meet for this recommendation to hold:

1. Every existing runtime safeguard (published graph, admission transitions, epoch fencing, work budgets, step limits, speech-session handling) remains enforced for native handoffs. Simplification must not come from dropping a safeguard.
2. Voice history reflects what was actually exchanged, not only what was generated.
3. Context growth has a defined, visible behavior instead of silent loss.
4. Any change to what text history persists is explicitly decided and documented.

No option has yet been shown to meet the success bar. A representative scenario set and a reproduction of the reported failure remain open (problem Q1, Q2).

### Outcome Boundaries for the Recommended Concept

- Every agreed evaluation scenario reaches the expected permitted agent and preserves its relevant context in both modalities.
- Paired voice/text scenarios reach consistent substantive outcomes; wording and presentation may differ.
- No handover or action outside the published permissions occurs because an agent has more context.
- Unlimited investment does not mean unlimited context, retention or resource use; latency still needs an explicit user-experience target.

## Out of Scope (for the recommended option)

- Switching voice/text within the same ongoing conversation, and cross-conversation memory.
- Sharing agent instructions, credentials or configuration as part of the history.
- Granting a receiving agent the previous agents' tools, capabilities or handover permissions.
- Recording or persisting voice audio; voice conversations remain volatile unless separately decided.
- Replacing the shared agent setup with a voice-only system, or switching to a managed delegation product as an assumed drop-in.
- Feature 006 behavior beyond what continuity depends on (greetings, formatting, progress UI).
- Telephony, production capacity/cost certification and legal-compliance claims.
- Technical architecture, data models, API changes, tasks or a delivery schedule.

## Assumptions to Validate

1. **Native handoffs can carry the runtime's safeguards.** Admission transitions, epoch fencing, work budgets, step limits and speech-session handling can be enforced through native handoff hooks in openai-agents 0.24.0. If not, a hybrid (native history semantics, application-applied transitions) is the fallback; full history sharing does not depend on native handoffs.
2. **Information-policy acceptance.** Removing community context isolation and sharing capability results across communities is acceptable to whoever owns information policy (problem Q8). The requester's decision is recorded; separate approval may still be required.
3. **Spoken exchanges can be represented faithfully.** Transcripts are reliable enough to stand as the voice part of the shared history.
4. **Context limits suffice or can be bounded visibly** for the agreed scenarios without silently dropping relevant or security context.
5. **Shared history fixes the reported failure.** A residual gap exists that full history closes and that feature 006 alone does not (problem Q1, Q5).
6. **Latency and cost of larger contexts are acceptable** for the user experience (problem Q6).
7. **Persistence decision.** Whether text conversations persist tool results across turns is decided explicitly, with the security and retention documentation updated accordingly.
8. **Impact remains to be established.** Unlimited appetite does not replace measuring the outcome against the agreed scenarios (problem Q7).
