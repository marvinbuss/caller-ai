# Problem Definition: Conversation-Aware Handover Across Voice and Text

- **Slug**: voice-with-text-agents
- **Created**: 2026-11-13
- **Inputs used**: [intake.md](intake.md), [research.md](research.md), requester clarifications on 2026-11-13

## Problem Statement

The requester reports that voice conversations can be handed to an agent that no longer matches the current topic or conversation progress, interrupting continuity for people using the same configured multi-agent service. The problem is to achieve context-appropriate, permitted handovers and preserve relevant permitted context in both voice and text, while clarifying information-sharing boundaries rather than assuming every agent may see the entire conversation. [Sources: intake, Idea (as captured); research, Users & Demand and Prior Art; requester clarifications below.]

The live failure has not yet been reproduced or quantified. Existing handover support and passing mechanical tests do not establish contextual correctness in a real conversation. [Source: research, Evidence Against the Idea and Verification & Evidence Quality.]

## Confirmed Scope & Requester Clarifications

- **Modality scope:** Equivalent behavior using the same agent configuration in separate voice and text conversations. Continuing one conversation while switching modalities is not included.
- **Information boundaries:** Evaluate both preserving existing boundaries and reconsidering what conversation information may cross them. Neither position is selected here.
- **Success bar:** "Every agreed evaluation scenario reaches the expected permitted agent and preserves relevant permitted context in both voice and text."
- **Incident evidence:** No concrete failing example is available yet.

These are requester clarifications from this assessment, not findings about deployed behavior.

## Affected Users & Stakeholders

- **Conversation participants:** People speaking or writing to the configured multi-agent service need the appropriate agent to handle their current request without losing relevant permitted context. The complaint concerns voice; consistent text behavior is part of the requested outcome. The number of affected participants and severity of impact remain unknown. [Sources: intake, Restated; research, Users & Demand.]
- **People testing agent configurations:** The documented environment provides browser voice/text tests. Correct transitions alone are insufficient to establish that a configuration handles evolving conversation intent consistently. This identifies a relevant testing context, not a separately verified complaint from testers. [Sources: research, Users & Demand and Verification & Evidence Quality.]
- **Requester:** Confirms the desired cross-modality outcome and asks that both information-boundary positions be examined. Funding, release authority and acceptance ownership have not been established. [Source: requester clarifications; ownership remains open.]
- **Accountable service and information-policy owners:** Any reconsideration of context visibility intersects existing confidentiality, consent, retention and authorization obligations. The research identifies those responsibilities, but does not name their owners for this assessment. [Source: research, Data & Constraints.]

## Goals

1. **Context-appropriate handover:** In the agreed scenarios, the current request and relevant conversation progress lead to the expected permitted agent, including when a topic changes or an earlier task is resumed. The scenario set must establish which situations actually apply. [Sources: intake, Idea (as captured) and First-Glance Unknowns; requester success bar.]
2. **Continuity of relevant information:** Preserve the permitted facts and progress needed to continue those scenarios across agent changes, rather than treating a technically successful transfer as sufficient. This does not imply access to every earlier exchange. [Sources: requester success bar; research, Prior Art and Evidence Against the Idea.]
3. **Equivalent outcomes across modalities:** The same agent configuration supports the same underlying requests and permitted contextual facts in separate voice and text conversations. Substance and appropriate destination should remain consistent; spoken and written presentation need not be identical. [Sources: requester modality scope and success bar; research, Users & Demand.]
4. **An explicit continuity/confidentiality contract:** Establish what continuity must mean under the existing information boundaries, and what it would mean under an explicitly reconsidered boundary contract, without conflating information visibility with authority to act. Both positions remain for comparison during shaping. [Sources: requester boundary clarification; research, Data & Constraints.]

## Non-Goals

- Switching between voice and text within the same ongoing conversation. The confirmed scope is equivalent behavior in separate conversations. [Source: requester modality scope.]
- Replacing the shared agent configuration with an unrelated voice-only agent system. The requested outcome is for the same multi-agent system to work across modalities. [Sources: intake, Idea (as captured); requester modality scope.]
- Automatically exposing complete conversation history to every agent, or treating greater information visibility as permission for otherwise forbidden handovers or actions. Reconsidering visibility requires an explicit contract; this assessment does not approve broader access. [Sources: requester boundary clarification; research, Data & Constraints.]
- Unrelated greeting, answer-formatting or console redesign work, or duplicating existing modality work without identifying a remaining continuity gap. Overlap with feature 006 must be accounted for. [Source: research, Existing assessment and feature work.]
- Telephone integration or production-scale capacity, billing or compliance certification. The evidence concerns the current browser conversation context and does not qualify those broader outcomes. [Sources: research, Users & Demand and Data & Constraints.]
- Selecting where conversation awareness resides or how handover is implemented. Those are solution questions, not this problem definition.

## Success Metrics

The first three metrics operationalize the requester's success bar. They are evaluation criteria, not measured current results or a statistical reliability promise for all future conversations. The agreed scenario set, expected destinations and relevant permitted context must be defined before measurement.

| Signal | Measurement and target | Baseline |
|---|---|---|
| Context-appropriate handover | Proportion of agreed scenarios reaching the expected permitted agent: **every scenario passes in voice and in text**. A technically allowed but context-inappropriate destination is not a pass. | Unknown; no concrete failing example or live correctness rate is available. |
| Relevant-context continuity | Proportion of agreed scenarios in which the destination retains the relevant permitted facts and progress identified for that scenario: **every scenario passes in each modality**. | Unknown; research establishes partial context visibility and intentional filtering, not a measured continuity pass rate. |
| Cross-modality equivalence | Proportion of paired voice/text scenarios with consistent intended destination and substantive outcome: **every agreed pair passes**, allowing modality-appropriate wording and presentation. | Unknown; existing mechanical tests do not establish this outcome. |
| Information and authorization boundaries | **Zero violations** of whichever information-sharing contract is explicitly approved, and no otherwise forbidden handover or action, in the agreed boundary scenarios. This preserves existing safeguards; a revised visibility contract must be approved before judging against it. | Existing filtering and authorization tests pass; live end-to-end exposure outcomes remain unmeasured. |

[Sources: requester success bar and modality scope; research, Prior Art, Data & Constraints, and Verification & Evidence Quality.]

Latency and cost targets have not been agreed. No numerical target, expected improvement or production guarantee is inferred from the research. [Source: research, Data & Constraints; open question Q6.]

## Cost of Inaction

Without further change, the researched baseline remains: handovers exist and follow authorization rules, but the available conversation information differs between voice and text, and some loss of context at a boundary is intentional. The reported mismatch between conversation progress and the active agent would remain unresolved unless other ongoing work addresses it. [Source: research, Prior Art and Evidence Against the Idea.]

Repeated explanations, inappropriate specialization and inconsistent answers are plausible consequences, not measured outcomes. No affected-session rate, abandonment rate, financial loss or support burden is available, so the cost of inaction cannot honestly be quantified yet. [Source: research, Market & Context and Users & Demand.]

Doing nothing on this assessment is also not necessarily a frozen product baseline: feature 006 already overlaps part of the need. Its planned active-agent awareness does not by itself establish complete conversational continuity, and its remaining effect must be distinguished from the residual problem. [Source: research, Existing assessment and feature work.]

## Open Questions

- **Q1 - Reproduction:** [NEEDS CLARIFICATION: What synthetic or sanitized conversation demonstrates the problem, with expected and actual destinations, the permitted agent relationships, and the affected deployed build/configuration? Does it occur on initial routing, a follow-up, a topic change, a return to earlier work, or after a handover?]
- **Q2 - Evaluation coverage:** [NEEDS CLARIFICATION: Which representative scenarios and expected permitted destinations make up the agreed evaluation set, including any same-community and cross-community cases?]
- **Q3 - Relevant progress:** [NEEDS CLARIFICATION: Which facts and progress must remain available for those scenarios, including non-delegated exchanges, actual spoken answers, interrupted or unheard content, pending/completed work and user confirmations?]
- **Q4 - Boundary comparison:** [NEEDS CLARIFICATION: Under existing boundaries, what continuity is required and what information must stay private; under reconsidered boundaries, which additional conversation information could be permitted to cross, and which restrictions remain mandatory?]
- **Q5 - Existing work:** [NEEDS CLARIFICATION: Which continuity outcomes remain uncovered by feature 006, and does its earlier decision to exclude working-agent instructions from the speaking model still apply?]
- **Q6 - Acceptable overhead:** [NEEDS CLARIFICATION: What additional response latency and cost, if any, are acceptable while meeting the confirmed handover and continuity success bar?]
- **Q7 - Impact:** [NEEDS CLARIFICATION: How many users or sessions are affected, and what observed repeated effort, incorrect outcome or other harm establishes priority?]
- **Q8 - Ownership:** [NEEDS CLARIFICATION: Who owns acceptance of the scenario set and outcomes, prioritization/release decisions, and approval of any revised information-sharing contract?]
