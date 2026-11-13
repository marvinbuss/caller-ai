# Idea Intake: Context-Aware Agent Handover Across Voice and Text

- **Slug**: voice-with-text-agents
- **Created**: 2026-11-13
- **Source**: pasted text
- **Type**: fix

## Idea (as captured)

> The issue is that in speech mode, the agent setup does not have inherent knowledge of the ongoing conversation. This creates challenges because the agent handover is not taking place today as it should, leading to potential inconsistencies and gaps in the conversation flow. If the voice model delegates, it hands over to the latest agent that has been active, which is fully out of band in comparison to the actual conversation context. We need a solution that ensures either the text models are aware of the entire progress and on the side hand over to the right agent during the conversation or the voice model itself has sufficient context to make the correct handover decisions and ensures the right agent is being called. We need to identify a good strategy on this matter to ensure proper agent handover and consistent conversation flow in both voice and text and the same multi-agent system works across different modalities.

## Restated

The proposal is to make agent handovers reflect the ongoing conversation context so that the same multi-agent system maintains consistent conversation flow across voice and text. It asks for a strategy in which the text models, the voice model, or both have sufficient awareness of conversation progress to delegate to the appropriate agent.

## Origin & Context

- **Raised by**: The intake requester; [NEEDS CLARIFICATION: original reporter and affected team or stakeholders].
- **Reported on**: 2026-11-13, through this intake; [NEEDS CLARIFICATION: when the behavior was first observed].
- **Trigger**: A reported speech-mode delegation behavior that selects the most recently active agent rather than an agent appropriate to the current conversation context, creating potential inconsistencies and gaps.
- **Desired scope**: Consistent agent handover and conversation continuity across voice and text within the same multi-agent system.

## First-Glance Unknowns

- [NEEDS CLARIFICATION: Which concrete conversation examples demonstrate an incorrect handover, and which agent should have received each delegation?]
- [NEEDS CLARIFICATION: What conversation history, agent state, and handover information are currently available to the voice model and the text agents?]
- [NEEDS CLARIFICATION: How is the most recently active agent determined, and under what conditions does voice delegation select it?]
- [NEEDS CLARIFICATION: What defines the appropriate agent at each point in a conversation, including when a user changes topics or returns to an earlier task?]
- [NEEDS CLARIFICATION: Must continuity cover switching between voice and text within a single conversation, and what information must remain consistent across that switch?]
- [NEEDS CLARIFICATION: Which models, agent frameworks, and modality-specific constraints must a future handover strategy accommodate?]
- [NEEDS CLARIFICATION: What observable outcomes would establish that handovers are correct and conversation flow is consistent?]
