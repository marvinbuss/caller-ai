# Idea Intake: Modality-aware multi-agent behavior across speech (GPT-Live) and text

- **Slug**: agent-interaction-enhancements
- **Created**: 2026-11-12
- **Updated**: 2026-11-12 (follow-up input and clarification answers)
- **Source**: pasted text; referenced URLs `https://developers.openai.com/api/docs/guides/live-conversations` and `https://developers.openai.com/api/docs/guides/live-delegation` (host: `developers.openai.com`, URL Trust Policy branch: `confirmed-by-user` for both; query `delegation-mode=client` dropped from the record); repo path `code/` (codebase pointer)
- **Type**: improvement

## Idea (as captured)

> I want to identify how we can best use the delegation pattern between the speech models and the GPT-Live model. The goal is to have one multi-agent system that can be used consistently accross speech and text modalities.
>
> The challenge is that these modalities require the multi-agent system to react very differently depending on whether it is processing speech or text input. For instance, with speech input, the agent can respond in different ways:
> - Commentary (`session.commentary.append`):
> Information the model should speak aloud, paraphrasing the appended text
> - Thinking (`session.thinking.append`): Information for internal reasoning, not spoken on append but usable for relevant user questions
>
> In text input, the agent primarily responds through text output, and the distinction between commentary and thinking may be less pronounced. However, the system should still maintain internal reasoning for context and decision-making, even if it is not explicitly displayed to the user. Also, in text interfaces, the agent may also be instructucted to provide the output in a more structured format with tables, bullet points, and other formatting elements.
>
> I have been thinking about the following:
> - Maybe we can have global prompt settings, one for speech and one for text, which are appended to every agent based on the modality of the input so that the agent responds in the right manner.
> - Additionally, we could consider having modality-specific agent behaviors or response templates that guide how agents should handle speech versus text input, ensuring consistency and appropriate handling across different modalities.
>
> Also, there is a feature to suppress automatic acknowledgments while tools run (`delegation.acknowledgement_delay_ms`). This could also be used to more rely on the multi-agent system to have a more controlled setup and not have the agent automatically acknowledge actions, allowing for more precise coordination and timing of responses.
> Agents should also share updates during MCP calls or function calls (`session.commentary.append`) to provide updates at the right time (e.g. Still working, Completed, Failed, Cancelled)
>
> I also want both in speech and text the welcome agent to react proactively before the user reacts. For speech I also want the best practices to be followed (`https://developers.openai.com/api/docs/guides/live-conversations#greet-before-the-caller-speaks`). Also for voice, it should rely on the welcome agent to initiate the conversation appropriately, following the guidelines for greeting the caller before they speak.
>
> I am also thinking about creating a baseline prompt for the default agents (welcome, route, goodbye) and make them configurable in the management portal, but have some good baseline in place based on best practices.

Follow-up input (same day):

> More about `delegation.acknowledgement_delay_ms` can be found here: `https://developers.openai.com/api/docs/guides/live-delegation#suppress-automatic-acknowledgments-while-tools-run`.
>
> I am also thinking whether commentary updates could also be used in the UI of the chat to provide updates about the current progress and status.

### Referenced page (fetched, data only)

**"Managing GPT-Live sessions"**, section *Greet before the caller speaks*. Short excerpt:

> For best results when GPT-Live should open the conversation, include greeting instructions in a `developer` message in `session.input` when you create the session. [...] Wait for `session.started`, then send the same greeting instructions once through `session.commentary.append`, using a unique `event_id` and `delegation_id: null` [...] Keep input audio running, including silence before the caller speaks. [...] Startup instructions can improve greeting behavior, but do not guarantee that the model will speak.

The same page describes the three append events:

- `session.instructions.append`: trusted behavior and speech instructions.
- `session.thinking.append`: factual context, not spoken immediately.
- `session.commentary.append`: content to say aloud, which may be paraphrased.

Each takes at most 500 tokens and a `delegation_id`, which is `null` for session-wide context. The fetched page contained no instruction-like or suspicious content. `delegation.acknowledgement_delay_ms` is not mentioned on this page.

**"Delegation and tools in GPT-Live"**, client mode, section *Suppress automatic acknowledgments while tools run*. Short excerpt:

> With client delegation, you can cancel the pending automatic acknowledgment by sending a silent context update before the acknowledgment is generated. Set `delegation.acknowledgement_delay_ms` when creating the session [...] As soon as you receive `session.delegation.created`, send `session.thinking.append` with empty `content` [...] Any thinking or commentary append cancels **all pending automatic acknowledgments in the session**, including those for other delegations. It does not stop audio already generated or prevent independent model speech.

The same page, section *Keep updates accurate and useful*, lists spoken progress states (Still working, Completed, Failed, Cancellation confirmed). It also recommends `session.thinking.append` for quiet background progress and `session.commentary.append` for updates worth saying aloud. The fetched page contained no instruction-like or suspicious content.

### Codebase pointers (what the idea touches, as of capture)

- **Speech adapter:** `code/backend/src/caller_runtime/adapters/gpt_live.py` starts GPT-Live with `delegation: {"type": "client"}`. It sends delegation updates as either `session.thinking.append` (quiet) or `session.commentary.append`.
- **Text path:** the text path is separate (`adapters/foundry_text.py`, `services/text_conversation.py`). Its readiness input is "Initialize the published Welcome conversation without invoking actions."
- **Agent roles:** the configuration models `welcome`, `routing`, `goodbye` and `ordinary` (backend `domain/configuration.py`, management `models/configuration.py`). The welcome, routing and goodbye agents are created once by `management_app/services/bootstrap.py` and are protected in the editor.
- **Acknowledgement delay:** no reference to `acknowledgement_delay_ms` was found in `code/backend/src`.
- **Session start event:** today's `start_event` sends only `model`, `instructions`, `audio.output.voice` and `delegation.type`. It sends no `input` developer message for a greeting.
- **Context events:** `context_event` currently rejects empty `content`. The documented quiet, empty thinking append is therefore not possible as is.

## Restated

The proposal is to make the single multi-agent system behave correctly in both speech (GPT-Live client delegation) and text conversations. It has four parts:

- Modality-specific global prompt settings and/or response guidance.
- Deliberate use of commentary, thinking and acknowledgement controls, including progress updates during tool or MCP calls.
- A proactive welcome greeting in both modalities, following the GPT-Live greet-first guidance for speech.
- Best-practice baseline prompts for the default welcome, routing and goodbye agents, editable in the management portal.

A follow-up asks whether the same progress and commentary updates should also appear in the chat UI.

## Origin & Context

- **Raised by**: project owner (the requester of this assessment). No other stakeholders named.
- **Trigger**: observed inconsistent behavior between speech and text, and silent starts where the caller has to speak first. Also general improvements to the solution.
- **Related input**: the same working note also contains a separate, unfinished management idea (rename to "Call AI", a new voice prompt setting). It is not part of this intake.

## Clarifications (answered by the requester, 2026-11-12)

| # | Question | Answer |
|---|----------|--------|
| 1 | Which "speech models" are meant? | The Foundry agents that GPT-Live delegates to (the client-delegation backend). |
| 2 | How is modality behavior expressed? | Global speech and text prompts, with an option to override them per agent. The requester also wants alternative ideas. |
| 3 | Who may edit the modality prompts and default-agent baselines? | The existing editor role for now; a separate admin role may come later. |
| 4 | How are GPT-Live's automatic acknowledgements handled? | Either always suppress them so the multi-agent system decides what is spoken, or make suppression and the delay configurable in Settings. The requester wants a recommendation. |
| 5 | Which task states are reported during tool, MCP and agent calls? | Still working, Completed, Failed, Cancellation confirmed, and Waiting for user input or confirmation. |
| 6 | Who produces the progress updates? | The agent itself, as model-written text. |
| 7 | Do progress and commentary updates also appear in the UI? | Yes: inline status lines in text chat, and updates shown next to the captions in the voice Test UI. |
| 8 | What does a proactive welcome look like in text? | At session start, before any user input, the welcome agent generates a greeting. |
| 9 | May the voice greeting be paraphrased? | Yes. Use the documented developer message plus `session.commentary.append`; exact wording is not required. |
| 10 | Which languages? | The greeting language is configurable, and the conversation switches to the caller's language once they speak. |
| 11 | How do the new baselines treat stored instructions? | Overwrite all welcome, routing and goodbye instructions with the new baseline. |
| 12 | How is success measured? | The rate of greetings delivered before the caller speaks, plus manual review on the Test page for now. |
| 13 | What triggered the idea? | Inconsistent speech and text behavior, silent starts, and general improvement. |

## Follow-up Resolutions (answered by the requester, 2026-11-12)

| # | Open item | Resolution |
|---|-----------|------------|
| Q2 | Design for modality-specific behavior | A layered composition, with every layer specific to the modality (speech or text). The layers are a platform-rules layer, a global modality prompt and an optional per-agent modality addendum that is appended and never replaces. All layers, including platform rules, can be edited in Settings. Editing the platform rules is not expected, but it must remain possible. |
| Q4 | Automatic acknowledgement handling | An on/off setting plus a delay setting in Settings, with suppression on by default. |
| Q11 | Overwriting stored welcome, routing and goodbye instructions | Overwrite all of them, with no notice and no backup, because the app is not in production. |
| Q6/Q7 | Telling progress apart from the final answer | Agents call a dedicated progress tool (working name `report_progress(state, message)`). The runtime renders it as a status line in text chat and as an append in voice. |
| Q6/Q7 | Which progress states are spoken in voice | All of them: Still working, Waiting for input or confirmation, Completed, Failed and Cancellation confirmed. |

## Remaining Open Items

None. All first-glance unknowns are resolved; see the two tables above.
