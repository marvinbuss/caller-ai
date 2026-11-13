# Idea Research: Modality-aware multi-agent behavior across speech (GPT-Live) and text

- **Slug**: agent-interaction-enhancements
- **Created**: 2026-11-12
- **Evidence confidence (overall)**: medium

The codebase, the published platform guidance and the Foundry behavior (probed on int) are well evidenced. User demand rests on one stakeholder.

## Users & Demand

- **Who has asked.** The only stated demand comes from the project owner. The reported triggers are inconsistent behavior between speech and text, and silent starts where the caller has to speak first. No ticket, interview or usage data was supplied. — [source: `.specify/assessments/agent-interaction-enhancements/intake.md`, Origin & Context] (confidence: medium, cited, stated want)
- **Customers.** The app is not in production, so there are no external users or usage metrics to measure demand against. — [source: intake.md, Follow-up Resolutions Q11] (confidence: high, cited)
- **Greeting is an existing product requirement.** Spec 001 already requires Welcome to speak first:
  - "Welcome speaks before any other agent" (`specs/001-call-center-agents/spec.md` L49);
  - "Welcome MUST introduce every normally accepted call before delegating to Routing" (FR-004, L236);
  - "95% of admitted callers hear Welcome within three seconds of connection" (L382).

  So the greeting part of the idea restates a requirement that was never re-met on the GPT-Live path. — [source: specs/001 spec.md] (confidence: high, cited)
- **Observed greeting behavior is mixed.** During int bring-up, GPT-Live did greet on its own in one voice test: "Live sends the Welcome greeting as 100-ms deltas faster than real time". That greeting comes from the Welcome prompt, not from an explicit greet-first sequence, and nothing guarantees it. The owner's "silent starts" are plausible but were not captured as evidence. — [source: `specs/004-int-environment-setup/research.md` L318] (confidence: medium, cited plus stated)
- **Inconsistency between modalities is built into today's design.** Speech and text run the same agent `prompt`, with no modality guidance at all. — [source: Data & Constraints below] (confidence: high, cited)

## Prior Art

### Internal

- **Speech instructions.** The GPT-Live `instructions` are today the active agent's single `prompt` plus a fixed delegation suffix: "Delegate reasoning, business actions and agent changes to the application-owned client…". There is no speech-specific layer. — `code/backend/src/caller_runtime/composition.py` L460-L466
- **Agent prompts.** Each agent has one `prompt` field, shared by speech and text. There is no per-modality field. — `code/management/management_app/models/configuration.py`; `code/contracts/configuration.schema.json` (agent schema, `additionalProperties: false`)
- **Default agent prompts are one line each.** — `code/management/management_app/services/bootstrap.py` L34-L38
  - Welcome: "Introduce the service and continue to Routing."
  - Routing: "Select an enabled business community entry from approved routing metadata."
  - Goodbye: "Give a concise farewell and end the call."

  They are not based on the GPT-Live prompt template; for example, they have no backchannel, interruption or delegation policy.
- **A fixed spoken holding line already exists.** If a speech delegation is still running after 5 s, the runtime appends "One moment, I'm checking that." as commentary. On a bounded failure it appends "I could not finish that, please try again." These are runtime-written and fixed text, a precursor of the proposed progress updates. — `code/backend/src/caller_runtime/services/conversation.py` L48-L51, L726-L744, L799
- **Speech results are delivered as one commentary append.** The full agent reply goes into a single `session.commentary.append` with the delegation ID. `quiet` (thinking) is never used in production code. Text replies stream as `text.delta` and then `text.completed`. — `conversation.py` L611-L690; `gpt_live.py` L632-L646
- **Text never greets first.** The text path runs a "readiness" request ("Initialize the published Welcome conversation without invoking actions.") that proves the backend works and keeps nothing it generates. — `code/backend/src/caller_runtime/adapters/foundry_text.py` L45; `specs/004-int-environment-setup/research.md` L307-L313
- **Tools today.** Welcome, Routing and Goodbye get only policy tools (`enter_routing` and others). Ordinary agents also get capability tools (MCP or direct). There is no progress or status tool. — `conversation.py` L440-L475; `services/conversation_agents.py` L27-L48
- **Test page rendering.**
  - Voice captions arrive as `transcript.delta` and are rendered in the browser (`components/live_audio_js.py` L222, L291).
  - Text messages go into a `role="log"` container (`pages/test_console.py` L176-L192).
  - Nothing renders Markdown (no `rx.markdown` anywhere in `management_app`), so tables or bullets requested for text would show as raw characters today.
- **Earlier specs did not cover the rest.** Specs 002 and 005 contain no requirement on modality-specific prompts, acknowledgements, progress updates or structured text output. Spec 002 only fixed client delegation and the commentary/thinking contract. — `specs/002-multimodal-agent-management/contracts/conversation.md` L238-L248; spec 005 FR-008/FR-009 cover the graph only

### External

- **Greet before the caller speaks.** Put the greeting instructions in a `developer` message in `session.input`. After `session.started`, send the same instructions once through `session.commentary.append` with `delegation_id: null`, keeping input audio running. The model may paraphrase or stay silent; exact wording needs a pre-rendered clip. — [source: developers.openai.com, live-conversations]
- **Suppressing automatic acknowledgements.**
  - How: set `delegation.acknowledgement_delay_ms` at start (example: 800), then send an empty `session.thinking.append` right after `session.delegation.created`.
  - Side effect: any thinking or commentary append cancels all pending automatic acknowledgements in the session.
  - Limit: it does not guarantee silence.

  — [source: developers.openai.com, live-delegation]
- **Recommended progress states.** "Still working", "Completed", "Failed" and "Cancellation confirmed", sent as commentary only when the state is verified. Quiet progress goes through thinking. — [source: live-delegation, "Keep updates accurate and useful"]
- **Backend prompt guidance.** Start from the existing text-agent prompt and add a "Voice conversation context" section. Keep large structured payloads and Markdown meant for display in the backend, and give GPT-Live only the relevant facts. — [source: live-delegation, "Start with your existing backend prompt"]
- **Live prompt structure.**
  - Use the headings *Backchannel policy*, *Interruption policy* and *Delegation policy*.
  - For `gpt-live-1`, request delegation with "Generate <|SpawnThinking|>" rather than "delegate to the backend".
  - A spoken "I'm checking that" does not confirm that work started.
  - Two recipes show the choice for tool calls: "Concise: brief receipt, then quiet" and "Chatty: natural progress".
  - Language: "Use the language configured by your application until the caller chooses another."

  — [source: developers.openai.com, live-prompting]
- **Foundry's own delegation page.** It covers client delegation, commentary and thinking with the 500-token limit, and Responses delegation. It does **not** mention `acknowledgement_delay_ms`, greeting or quiet suppression. — [source: learn.microsoft.com, gpt-live-delegation, page updated 2026-09-11]
- **OpenAI guidance is not binding here.** Spec 002 already decided that OpenAI's pages are "general guidance only; they do not override the Foundry-specific service contract." — `specs/002-multimodal-agent-management/research.md` L28-L29

## Market & Context

- **Platform direction.** Both OpenAI's and Foundry's documentation separate the voice-facing prompt (personality, backchannel, interruption, delegation policy) from the backend agent prompt (task procedure and tools). The idea's layered, modality-specific prompts follow the same split. — [source: live-prompting; live-delegation; Foundry gpt-live-delegation]
- **What happens today without the idea.**
  - Both modalities use the same one-line prompts.
  - The fixed holding and failure lines cover only two states.
  - Text conversations wait for the user.
  - Voice greetings depend on the model choosing to speak.

  — [source: conversation.py L48-L51; composition.py L460-L466; foundry_text.py L45]
- **Cost of doing nothing.** No production users are affected yet (app not in production). The spec 001 greeting requirement (FR-004, 95% within 3 s) remains unmet and unmeasured on the GPT-Live path. — [source: intake.md; specs/001 spec.md L236, L382] (confidence: medium)

## Data & Constraints

- **Speech replies are capped at 500 UTF-8 bytes.** `context_event` rejects content over 500 bytes, and the runtime sends the whole speech reply in one append with no splitting. A reply written for text (tables, bullets, long answers) can therefore fail in speech with `live_context_unavailable`, after which the caller hears the fixed failure line. This is direct evidence that speech needs its own output guidance. — `gpt_live.py` L116-L130; `conversation.py` L680-L682, L799 (confidence: high, cited)
- **Empty appends are refused.** `context_event` rejects empty content, so the documented empty quiet append is impossible without a change. The documented alternative, a short factual thinking append such as "Lookup in progress.", is non-empty and would pass. — `gpt_live.py` L124-L125; live-delegation (confidence: high, cited)
- **The session-start validator is strict.**
  - On `session.started`, it compares every echoed session field with `start_event()` and requires `input` to be `[]`.
  - Adding a greeting `developer` message to `session.input`, or `acknowledgement_delay_ms` to `delegation`, needs coordinated changes to the start event, the validator and the contract tests (`tests/contract/test_gpt_live.py`).
  - It is unknown whether Foundry echoes these fields back.

  — `gpt_live.py` L90-L101, L155-L180; `specs/004-int-environment-setup/research.md` L297-L304 (confidence: high, cited)
- **Configuration needs a contract change.**
  - The configuration contract is strict (`additionalProperties: false`, `schemaVersion` 2.1.0). Conversation settings currently hold only `voice`, `maxOutputTokens` and `idleTimeoutSeconds`.
  - New global modality prompts, per-agent addenda, platform-rule layers and acknowledgement settings each need a schema change, a management-to-runtime projection change, and a version bump.
  - The `routable` incident (PR #42, PR #43) shows that adding a defaulted field to stored revisions has already broken the published-revision hash checks twice.

  — `code/contracts/configuration.schema.json` L226-L268; `.specify/bugs/management-redesign-bugs/assessment.md` (confidence: high, cited)
- **Budgets.**
  - Live instructions: up to 16,384 tokens; the runtime caps them at 16 KiB UTF-8.
  - Live startup history: 128 messages and 8,192 tokens.
  - Each append: 500 tokens.
  - Speech readiness: must fit the 10-second startup budget. A Welcome greeting via the reasoning backend took 4.4 to 10.5 s, which is why readiness was cut down to a tool-free request.

  A model-generated text greeting at session start falls under this same budget. — live-conversations; `gpt_live.py` L77-L79; `specs/004-int-environment-setup/research.md` L307-L313 (confidence: high, cited)
- **Platform: Foundry only.** GPT-Live is reached only through Azure AI Foundry (`https://{foundry_host}/openai/v1`, model `gpt-live-1`). Responses delegation is out of scope. — `docs/library-decisions.md` L13; `specs/002-multimodal-agent-management/plan.md` L22-L23 (confidence: high, cited)
- **Two producers of speech today.** The runtime's fixed holding line fires 5 s into speech work, independently of the agent. Model-written progress from a `report_progress` tool would be a second producer of spoken updates. — `conversation.py` L741-L744 (confidence: high, cited)
- **Compliance.** No new personal-data category is introduced. Progress text written by the model reaches the caller and the transcript UI, so the existing redaction and no-content-logging rules still apply (`test.relay.failure` logs only error class and codes). — `specs/004-int-environment-setup/research.md` L356-L360 (confidence: medium, cited)

## Evidence Against the Idea

- **Foundry does not support acknowledgement suppression (confirmed by probe).** `delegation.acknowledgement_delay_ms` appears only on OpenAI's page, and Foundry's `gpt-live-1` on int rejects it with `unknown_parameter`. Spec 002 decided that OpenAI's pages do not override the Foundry contract. The on/off and delay settings from intake Q4 therefore have nothing to configure today. — [source: int probe 2026-11-12 (see Resolutions); learn.microsoft.com gpt-live-delegation; specs/002 research.md L28-L29] (confidence: high)
- **Suppression is coarse and gives no guarantee.** One append cancels every pending acknowledgement in the session, and the model "may still speak earlier". Platform guidance says application-controlled playback is the only way to guarantee silence. — [source: live-delegation] (confidence: high)
- **Model-written progress conflicts with documented guidance.**
  - Guidance: send a spoken update only when the task's *verified* state changes, and never invent progress.
  - The chosen design (Q6): the agent writes its own progress through a tool, so a "Completed" could be claimed before a capability confirms success.
  - Overlap: the runtime's fixed holding line would also need reconciling with agent-written updates.

  — [source: live-delegation "Keep updates accurate and useful"; live-prompting "Concise/Chatty" recipes; conversation.py L48-L51] (confidence: high)
- **Speaking every state could be noisy.** Q7 asks for all five states to be spoken, including "Still working". The documented "Chatty" recipe warns against repeated acknowledgements and padding the wait, and the "Concise" recipe goes quiet after one receipt. — [source: live-prompting] (confidence: medium, a product trade-off rather than a defect)
- **A text greeting costs time and money.** A model-generated greeting at session start adds a model call before the user does anything. Int measured 4.4 to 10.5 s for a Welcome turn on the reasoning deployment, against a 10-second startup budget. — [source: specs/004 research.md L307-L313] (confidence: high)
- **Editable platform rules can break the protocol.** Q2 makes the platform-rules layer editable in Settings. That layer would carry the rules that keep speech replies under the 500-byte append limit and make delegation work ("Generate <|SpawnThinking|>"). An edit there can silently break voice. — [source: gpt_live.py L127; live-prompting] (confidence: medium, ASSUMPTION about which rules the layer will hold)
- **Contract risk is real and recent.** Adding fields to stored configuration has already caused two int incidents (PR #42, PR #43). Several new layers and settings widen that surface again. — [source: `.specify/bugs/failed-management-redesign-startup/`, `.specify/bugs/management-redesign-bugs/`] (confidence: high)
- **Demand is thin.** There is one stakeholder and no production users. The success measure is manual review on the Test page plus a greeting rate, and by decision (G4) there is no baseline to compare against. — [source: intake.md; Resolutions G4] (confidence: high)
- **Agent-only progress is trusted, not checked.** By decision (G5 and G6), progress is written only by the agent and is not checked by the runtime. The fixed lines are kept only as a fallback when the agent fails completely. A false "Completed" or "Failed" spoken to the caller is an accepted risk. — [source: Resolutions G5, G6; live-delegation] (confidence: high)

## Gaps & Open Questions

- [NEEDS CLARIFICATION: Does Foundry-hosted `gpt-live-1` accept `delegation.acknowledgement_delay_ms`, and does `session.started` echo it back? Not documented by Foundry; needs a live probe on int.]
- [NEEDS CLARIFICATION: Does Foundry echo `session.input` (a greeting `developer` message) in `session.started`? Today's validator requires `input == []`.]
- [NEEDS CLARIFICATION: Does `session.commentary.append` with `delegation_id: null` right after `session.started` produce a greeting on Foundry, and how quickly (the spec 001 target is 95% within 3 s)?]
- [NEEDS CLARIFICATION: Baseline. How often do voice sessions on int start silently today? No measurement exists; the greeting-rate success measure needs a "before" value.]
- [NEEDS CLARIFICATION: Should the runtime's fixed holding line ("One moment, I'm checking that.") and failure line stay, or be replaced by agent-written progress?]
- [NEEDS CLARIFICATION: Can an agent-written "Completed" or "Failed" be tied to a verified capability outcome, given the platform guidance to report only verified state?]
- [NEEDS CLARIFICATION: Should text chat render Markdown (tables, bullets)? Today the Test page shows raw text.]
- [NEEDS CLARIFICATION: How should a speech reply over 500 bytes be handled: split into several appends, have the speech layer ask for brevity, or both?]

### Resolutions (answered by the requester, 2026-11-12)

| # | Gap | Resolution |
|---|-----|------------|
| G1 | Foundry support for `delegation.acknowledgement_delay_ms` | **Probed on int: not supported.** Foundry rejects the field at `session.start` with `invalid_request_error` / `unknown_parameter` ("Unknown parameter: 'session.delegation.acknowledgement_delay_ms'."), and the session does not start. See "Live probe on int" below. |
| G2 | Whether Foundry accepts the `session.input` greeting and echoes it back | **Probed on int: supported.** Foundry accepts a `developer` message in `session.input` and echoes it in `session.started`. With `session.commentary.append` (`delegation_id: null`) sent right after start, the assistant greeted within about 1 s. |
| G3 | Greeting latency target | Relax the target to the caller hearing Welcome within **5 s** for now. This replaces the 3 s in spec 001 for this work. |
| G4 | Baseline of silent starts | No baseline measurement; manual review on the Test page after the change is enough. |
| G5 | Fixed holding and failure lines | Progress is written by the agent only. The runtime's fixed lines remain only as a fallback when the agent fails completely. |
| G6 | Keeping Completed and Failed truthful | Trust the agent. Its prompt tells it to report only verified results; the runtime does not check. The accepted risk is that a false "Completed" is possible (see Evidence Against). |
| G7 | Markdown in text chat | Yes: the Test page text chat renders Markdown, including tables and bullets. |
| G8 | Speech replies over 500 bytes | Both: the speech layer asks for brevity, and the runtime splits long replies into several appends. |

The gaps above are answered by this table; none remain open.

### Live probe on int (2026-11-12)

- **Setup.** One read-only script ran inside `ca-caller-int-runtime--0000025` through `az containerapp exec`, using the runtime's managed identity against the configured Foundry endpoint with `gpt-live-1` and the configured voice.
- **What it did.** It opened two short sessions and printed only event types, timings, the echoed `delegation` and `input`, errors, and the assistant's output transcript. It sent no caller audio, only silence.
- **Side effects.** No slot, configuration or infrastructure was touched. The staged file was removed afterwards. About 8 s of voice usage in total.

**Session A** started with `delegation: {"type": "client", "acknowledgement_delay_ms": 800}`:

```text
A error 0.04 {"type": "invalid_request_error", "code": "unknown_parameter", "message": "Unknown parameter: 'session.delegation.acknowledgement_delay_ms'.", "param": "session.delegation.acknowledgement_delay_ms"}
```

**Session B** started with `delegation: {"type": "client"}` and a `developer` greeting message in `session.input`. The same greeting text was then sent through `session.commentary.append` after `session.started`, with silence streaming:

```text
B started 0.35 delegation= {'type': 'client'} input= [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Greet the caller now in English. ...
B first_out_transcript 1.03
B event session.output_audio.delta 1.1
B event session.commentary.appended 1.64
B transcript:  Hi there! I'm your support assistant. How can I help today?
B closed close_requested {'seconds': 8.0}
```

**Implications** (evidence only, no decision):

- **Acknowledgement suppression.** The documented way to suppress automatic acknowledgements is **not available on Foundry** today. The "on/off and delay" setting from intake Q4 cannot be implemented as described. The only documented lever left is to send a non-empty `session.thinking.append` quickly after `session.delegation.created`, without the delay window. Whether that alone prevents the acknowledgement is unverified.
- **Greeting.** The greet-first sequence works on Foundry. The first assistant speech came about 0.7 s after `session.started` (about 1.0 s after the start was sent), well inside the 5 s target from G3. Only one greeting was spoken in 9 s, with no duplicate from sending the instructions twice.
- **Validator change.** `session.started` echoes the non-empty `input`, so today's validator rule `input == []` must change to accept the echoed greeting message.
- **Which signal caused the greeting is unknown.** The greeting began (1.03 s) before `session.commentary.appended` arrived (1.64 s). This does not show whether the `session.input` message or the commentary append triggered it.

## Sources

- **Live probe:** int runtime container `ca-caller-int-runtime--0000025`, Foundry `gpt-live-1`, 2026-11-12 (approved by the requester; read-only; results under Resolutions).
- **Pasted idea and clarifications:** `.specify/assessments/agent-interaction-enhancements/intake.md` (repo, read-only)
- **Codebase files** (repo, read-only):
  - `code/backend/src/caller_runtime/adapters/gpt_live.py`
  - `code/backend/src/caller_runtime/adapters/foundry_text.py`
  - `code/backend/src/caller_runtime/services/conversation.py`
  - `code/backend/src/caller_runtime/services/conversation_agents.py`
  - `code/backend/src/caller_runtime/composition.py`
  - `code/management/management_app/services/bootstrap.py`
  - `code/management/management_app/models/configuration.py`
  - `code/management/management_app/pages/test_console.py`
  - `code/management/management_app/components/live_audio_js.py`
  - `code/contracts/configuration.schema.json`
  - `docs/library-decisions.md`
- **Earlier specs and bug records** (repo, read-only):
  - `specs/001-call-center-agents/spec.md`
  - `specs/002-multimodal-agent-management/{research.md,plan.md,contracts/conversation.md}`
  - `specs/004-int-environment-setup/research.md`
  - `specs/005-management-frontend-redesign/spec.md`
  - `.specify/bugs/management-redesign-bugs/assessment.md`
- **External pages** (all with policy `confirmed-by-user`):
  - https://developers.openai.com/api/docs/guides/live-conversations (host: developers.openai.com)
  - https://developers.openai.com/api/docs/guides/live-delegation (host: developers.openai.com; query `delegation-mode` dropped from the record)
  - https://developers.openai.com/api/docs/guides/live-prompting (host: developers.openai.com)
  - https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live-delegation (host: learn.microsoft.com)

No fetched page contained instruction-like or suspicious content.
