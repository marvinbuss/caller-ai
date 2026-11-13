# Concept: Speech and text behavior per modality for the same agents

- **Slug**: agent-interaction-enhancements
- **Created**: 2026-11-12
- **Recommended option**: Option B, the full modality-aware behavior, delivered in two slices

## Options

### Option A: Better defaults and fixed mechanics, no new settings

- **Sketch**:
  - **Greetings.** Voice sessions open with Welcome greeting the caller using the documented greet-first sequence, which works on Foundry per the int probe. Text sessions open with a Welcome greeting before the user writes.
  - **Spoken answers.** A spoken answer longer than one speech update is spoken in parts instead of failing.
  - **Text answers.** The text chat shows formatted answers (lists, tables).
  - **Defaults.** Welcome, Routing and Goodbye get new best-practice prompts, overwriting today's one-liners. GPT-Live's voice instructions follow the published structure (backchannel, interruption and delegation policy).
  - **Acknowledgements.** A short quiet update is sent as soon as work is delegated, which reduces GPT-Live's automatic acknowledgement on a best-effort basis.
  - **What stays the same.** The runtime's fixed "One moment…" and failure lines remain the only progress feedback. Editors still have one prompt per agent and no new settings.
- **Appetite**: small. A few days, mostly in the runtime and the Test page, with no change to stored configuration.
- **Trade-offs**:
  - **Wins**: meets the greeting goals and metrics (voice and text greeting within 5 s, 90% of voice sessions), makes length failures in voice impossible, and gives better out-of-the-box behavior. There is no configuration format change, so none of the risk seen in PR #42 and #43.
  - **Sacrifices**: the goals for editor control and for progress in both modalities. Text still gets no status during work, and voice keeps one generic line. Answers adapt to the modality only through the fixed defaults, so custom agents written by editors stay modality-blind.
  - **Risks**: a model-written text greeting may miss 5 s; an int Welcome turn took 4.4 to 10.5 s.
- **Rabbit holes**:
  - **Text greeting latency** (see Option B).
  - **Session-start check.** The runtime's strict check on session start must accept the echoed greeting without loosening its other checks.

### Option B: Modality-aware behavior as requested, in two slices

- **Sketch**:
  - **Slice 1 = Option A.** Greetings in both modalities, spoken answers in parts, formatted text, best-practice defaults, best-effort acknowledgement handling.
  - **Slice 2: editor control and agent-written progress.**
    - **Layered behavior settings.** Settings gains modality-specific layers: one set for voice and one for text. Each set has platform rules, a global prompt, and an optional per-agent addition that is appended and never replaces. Editors can change all of them, and best-practice defaults ship with the product.
    - **Agent-written progress.** Agents report progress themselves: still working, waiting for input or confirmation, completed, failed, cancellation confirmed. Users see it as inline status lines in text chat and next to the captions in the voice Test page, and voice callers hear every state.
    - **Fixed lines as fallback.** The runtime's fixed lines are used only when an agent fails completely.
    - **Acknowledgement setting.** It becomes an on/off switch for the quiet update on delegation, default on. The delay setting is dropped because Foundry rejects `acknowledgement_delay_ms`.
- **Appetite**: medium. Slice 1 takes days; slice 2 takes a few weeks. Slice 2 touches the stored configuration format, management, runtime and Test page together.
- **Trade-offs**:
  - **Wins**: covers every goal in problem.md, including editor control and progress in both modalities, and follows the requester's decisions (intake Q2–Q8, research G5–G8). Slicing delivers the measurable greeting and length results early and keeps the riskier format change separate.
  - **Sacrifices**: more moving parts and a configuration format change. Agent-written outcomes are trusted, not checked, so a false "Completed" can be spoken (accepted, G6). Speaking every state, including "Still working", can feel padded; the guidance warns against it.
  - **Risks**:
    - Editable platform rules let an edit silently break voice delegation or the brevity rule.
    - Whether the quiet update suppresses GPT-Live's acknowledgement is unverified, so it stays best effort (P1).
- **Rabbit holes**:
  - **Configuration format change.** New layers and settings change the stored and projected configuration. Adding defaulted fields has caused two int incidents (PR #42 and #43), so this needs a careful version step and tests against revisions saved by earlier releases.
  - **Progress during an agent turn.** The executor currently ends an agent turn at the first tool call and allows one model turn per step (`tool_use_behavior = "stop_on_first_tool"`, `max_turns=1` in `services/conversation.py`). A progress report made by the agent would end its turn unless that flow changes; this is the largest unknown in slice 2.
  - **Text greeting within 5 s.** A Welcome turn on the reasoning deployment took 4.4 to 10.5 s, so a model-written greeting needs a tighter bound or a fallback greeting.
  - **Spoken answers in parts.** GPT-Live may paraphrase or merge several updates, which needs listening tests.
  - **Editing every layer.** Allowing all layers to be edited invites scope creep: versioning, reset to default, and validation of platform rules.

### Option C: Do nothing now

- **Sketch**: Keep today's behavior until there is a customer or production need: one prompt per agent, a model-dependent voice greeting, no text greeting, single-update spoken answers, and the fixed holding and failure lines.
- **Appetite**: none.
- **Trade-offs**:
  - **Wins**: no effort and no configuration format risk.
  - **Sacrifices**: every goal. The spec 001 greeting requirement stays unmet, length failures in voice stay possible, and every demo shows the gaps. Prompts that editors write meanwhile will need revisiting.
- **Rabbit holes**: none. The cost is deferred, not avoided.

## Recommendation

**Option B, delivered in two slices.**

- **Slice 1 (Option A's content)** directly hits the measurable success metrics at small cost, with no configuration format change:
  - voice greeting within 5 s in 90% of sessions;
  - text greeting within 5 s in 100% of sessions;
  - zero length failures in voice;
  - automated tests for the mechanics.
- **Slice 2** covers the remaining goals the requester explicitly chose: editor control, progress in both modalities, and answers shaped per modality for custom agents.

Option A alone would leave half the goals unmet. Option C fails every goal.

Slice 2 should begin with the progress-during-a-turn question. If the executor's one-tool-per-turn flow cannot carry agent-reported progress without a large redesign, slice 2 should fall back to runtime-reported progress for "Still working" and keep agent-written text for outcomes. That fallback should be decided in the spec, not here.

## Out of Scope (for the recommended option)

**Inherited from problem.md:**

- Changing the voice provider or the delegation mode.
- Exact greeting wording or pre-recorded clips.
- Guaranteed silence or application-controlled playback.
- Runtime checks of agent-reported outcomes.
- Keeping or migrating the existing Welcome, Routing and Goodbye instructions.
- A new management role.
- A before-change baseline.
- A phone or telephony channel.

**Newly excluded:**

- **Acknowledgement delay setting.** Foundry rejects `acknowledgement_delay_ms`, so no delay value is offered.
- **Versioned prompt history.** No history beyond what published configuration revisions already keep.
- **Automated cross-modality evaluation** by a grader model against real models; consistency is checked manually (P5).
- **Markdown outside the Test page text chat.**
- **Other management pages** beyond Settings, Community and Agent (agent addition) and Test.

## Assumptions to Validate

1. **Greeting reliability.** The greet-first sequence greets reliably on Foundry. One int probe showed first speech about 0.7 s after start; 90% within 5 s still needs repeated sessions.
2. **Quiet update.** A short quiet update sent right after delegation noticeably reduces GPT-Live's automatic acknowledgement on Foundry without the delay window. This is unverified, and the requirement is best effort.
3. **Text greeting speed.** A Welcome text greeting can be produced within 5 s of the session being ready. Low reasoning effort, a small output cap or a fallback greeting may be needed.
4. **Spoken answers in parts.** GPT-Live speaks several consecutive updates for one delegation as one coherent answer.
5. **Progress mid-turn.** Agents can report progress mid-turn without ending their turn, given the current one-tool-per-turn executor.
6. **Agent honesty.** With the platform rules in place, agents report "Completed" or "Failed" only after a real capability result. This is accepted risk; it is validated by the manual "failing call" scenario.
7. **Safe Markdown.** The Test page can render Markdown safely, with no raw HTML or script injection from model output.
8. **Format change safety.** A configuration format version step for the new layers can be introduced without breaking revisions published by earlier releases (lesson from PR #42 and #43).
9. **Platform-rule editing.** Editors rarely change the platform rules, as the requester expects (Q2). Where they do, voice still works or the change is clearly rejected.
