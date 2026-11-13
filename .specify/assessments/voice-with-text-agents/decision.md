# Decision: Shared Conversation Continuity Across Voice and Text

- **Slug**: voice-with-text-agents
- **Decided**: 2026-11-13
- **Verdict**: go
- **Artifacts reviewed**: [intake.md](intake.md) | [research.md](research.md) | [problem.md](problem.md) | [concept.md](concept.md), plus the project constitution (v2.2.0)

## Scorecard

| Criterion | Rating | Justification |
|-----------|--------|---------------|
| Problem validity | adequate | The mechanism is verified in code and tests. On every agent change the working agent's history is reset to a filtered summary in both modalities, and voice keeps most spoken exchanges outside the agents' history. That is a structural continuity gap, not a hypothetical one. The original complaint ("hands over to the latest agent") is only partly precise: delegation goes to the conversation's retained active agent. No live failing conversation has been reproduced. [research: Prior Art, Verification V1; problem: Problem Statement] |
| Evidence strength | adequate | Strong for the mechanism: a code trace, 12 focused passing tests, a re-run of the filtered-handover test, and primary platform documentation stating that client delegation leaves context ownership to the application. Weak for demand: one qualitative report, with no prevalence, harm measurement or reproduction. Research rated overall confidence medium. [research: Evidence confidence, Users & Demand, Data & Constraints] |
| Value vs. inaction | adequate | Without change, context loss on agent change persists in both modalities, and voice remains asymmetric to text. The harm (repeated explanations, wrong specialization, inconsistent answers) is plausible but unquantified. Feature 006 adds speaking-model orientation only, not continuity. [problem: Cost of Inaction; concept: Option A] |
| Feasibility / appetite | adequate | Appetite is unlimited by requester decision. The recommended Option C is credible: the installed openai-agents 0.24.0 exposes `on_handoff` (can reject a transfer), `is_enabled`, per-destination handoffs, `input_filter` and history mapping. Integration with the runtime's admission transitions, epoch fencing, work budgets, step loop and voice-session replacement is unverified, but a fallback exists (application-applied transitions with shared history). [concept: Option C, Assumption 1] |
| Strategic fit | adequate | Aligns with the product's shared multimodal agent system (spec 002) and with feature 006's cross-modality consistency requirement (FR-012). The constitution imposes obligations rather than conflicts: a threat assessment for wider transcript and tool-result visibility (Principle II), server-side authorization of model-requested handovers (Principle II), documentation with Mermaid handover sequences (Principle III), and monitoring of handover failures (Principle VII). |
| Risk posture | adequate | The major risks are identified and paired with conditions on the specification: safeguards preserved under native handoffs, faithful voice history, visible behavior at the context limit, an explicit persistence decision for tool results, and information-policy acceptance of cross-community visibility. None is mitigated yet; each is a precondition for approval of the specification, not deferred hope. [concept: Rabbit holes, Conditions, Assumptions] |

No criterion is rated `unknown` or `weak`. Demand-side evidence is weak in isolation and is acknowledged above; the evidence rating rests on the verified mechanism and the requester's explicit product decision.

## Verdict & Rationale

**Go.** The problem is real at the mechanism level and verified in this codebase: agents lose conversation context on every handover, and voice additionally keeps spoken exchanges away from the agents. The requester, who owns this product decision, has set an explicit information-sharing contract (full history, including tool results and spoken exchanges, for every agent reached) and an unlimited appetite. Option C addresses all four problem goals directly and has a credible implementation path in the installed SDK. Problem validity and evidence strength are both `adequate`, and a concept option is recommended, so the go conditions are met.

The go is deliberately conditional. The specification must not trade away existing runtime safeguards for the simplicity of native handoffs, and it must define acceptance scenarios, because none exist yet and the reported failure has not been reproduced. If the specification cannot keep the safeguards with native handoffs, the information-sharing decision still stands and the handoff mechanism should fall back to application-applied transitions.

## If needs-clarification

Not applicable.

## If go - Handoff to `/speckit-specify`

- **Problem**: In voice and text conversations with the same configured multi-agent system, each handover resets the receiving agent to a filtered summary, and voice keeps most spoken exchanges outside the agents' history; agents therefore lose conversational context and cannot reliably continue follow-ups, topic changes or returns to earlier tasks.
- **Chosen approach**: Option C - full shared conversation history through native handoffs. Every agent reached in a conversation, including after community transfers, sees the full conversation: user and assistant messages, spoken exchanges that were not delegated, and earlier agents' tool calls and results. Agent changes use the Agents SDK's native handoffs, constrained to the published agent graph.
- **Non-negotiable conditions**:
  1. Every existing runtime safeguard remains enforced for handoffs: published graph and protected roles, admission transitions, agent-epoch fencing, work budgets, step limits and speech-session handling. If native handoffs cannot carry these, use application-applied transitions while keeping full history sharing.
  2. Seeing history grants no new authority: a receiving agent gets neither the previous agents' tools and capabilities nor broader handover permissions. Agent instructions, credentials and configuration are never part of the history.
  3. Voice history reflects what was actually exchanged, including interruptions, not only generated text.
  4. Reaching the context limit has a defined, visible behavior; no silent loss of relevant or security context.
  5. Whether text conversations persist tool results across turns is decided explicitly, and the security and text-history documentation is updated to match.
  6. Constitution obligations: threat assessment for wider visibility of transcripts and tool results (II), documentation with Mermaid handover sequences (III), monitoring of handover failures (VII), automated tests (IX).
- **In scope**: Shared conversation history across all agents in a conversation; inclusion of spoken exchanges and tool activity; native handoffs within the published graph; equivalent behavior in separate voice and text conversations; reconciliation with feature 006 (speaking-model instructions and context note, per-agent voice-session replacement); updating the deliberate filtered-handover test and the security documentation that describes communities as context boundaries.
- **Out of scope**: Switching modality within one conversation; cross-conversation memory; sharing agent instructions, credentials or configuration; granting previous agents' tools or wider handover permissions; recording or persisting voice audio; voice-only agent systems or managed delegation as an assumed drop-in; unrelated feature 006 behavior; telephony, production capacity/cost certification and compliance claims.
- **Success metrics** (from problem.md; targets apply to an agreed scenario set the specification must define):
  - Context-appropriate handover: every agreed scenario reaches the expected permitted agent, in voice and in text.
  - Relevant-context continuity: every agreed scenario preserves the relevant earlier facts and progress after agent changes, in each modality.
  - Cross-modality equivalence: every paired voice/text scenario reaches a consistent substantive outcome; wording may differ.
  - Authorization boundaries: zero handovers or actions outside the published permissions.
  - Latency and cost targets: not yet agreed; must be set during specification.
- **Carried-forward open questions**:
  - [NEEDS CLARIFICATION: Which representative scenarios, with expected destinations and the context each must preserve, form the acceptance set? Include topic changes, context-dependent follow-ups, returns to earlier tasks, same-community handovers and cross-community transfers. (problem Q1, Q2)]
  - [NEEDS CLARIFICATION: Can the originally reported failure be reproduced as one of these scenarios? (problem Q1)]
  - [NEEDS CLARIFICATION: Does whoever owns information policy accept removing community context isolation and sharing capability results across communities, beyond the requester's decision? (problem Q8)]
  - [NEEDS CLARIFICATION: Do text conversations persist tool calls and results across turns, and with what retention? (concept, Assumption 7)]
  - [NEEDS CLARIFICATION: What is the behavior when a shared history reaches the context limit? (concept, Assumption 4)]
  - [NEEDS CLARIFICATION: How do this feature and the remaining feature 006 tasks (T042, T046, T047: voice instructions, context note, per-agent voice-session replacement) divide responsibility? (problem Q5)]
  - [NEEDS CLARIFICATION: What added latency and cost are acceptable for larger contexts? (problem Q6)]
  - [NEEDS CLARIFICATION: How will affected sessions and outcome improvement be measured after release? (problem Q7)]
