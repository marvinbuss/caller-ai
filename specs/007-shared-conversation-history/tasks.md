---

description: "Task list for feature 007: Shared Conversation History Across Agents"
---

# Tasks: Shared Conversation History Across Agents

**Input**: Design documents from `specs/007-shared-conversation-history/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), contracts ([shared-history](contracts/shared-history.md), [handover](contracts/handover.md), [voice-live](contracts/voice-live.md), [text-endpoint](contracts/text-endpoint.md), [configuration-v2.3](contracts/configuration-v2.3.md), [test-console](contracts/test-console.md), [observability](contracts/observability.md)), [quickstart.md](quickstart.md)

**Tests**: Required. Constitution IX requires automated tests for every change, and SC-004, SC-006, SC-007 and SC-010 name mechanics they must cover. Within each phase, tests come first and must fail before the implementation.

**Paths**: Runtime is `code/backend/src/caller_runtime/` (tests in `code/backend/tests/`). Management is `code/management/management_app/` (tests in `code/management/tests/`). Tools are `code/tools/`.

**Test commands**: see [quickstart.md](quickstart.md). After management tests, always run `git checkout -- code/management/reflex.lock`.

**Deployment order**: management before runtime. The relay must accept `agent.changed` without a generation change (T030) before the runtime stops raising the generation on agent changes (T028, T029).

**Delivery**: the feature merges as one change, as feature 006 did, with its documentation tasks inside it (Constitution III).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: User story the task belongs to (US1–US6)

---

## Phase 1: Setup (Probes)

**Purpose**: Answer the model-behavior questions P1–P3 of [research.md](research.md#r15-probes-on-int-before-implementation) before implementation, because they set defaults.

- [X] T001 Create `code/tools/scripts/probes/common.py`. It provides:
  - the int endpoints and deployment names, read from the same `CALLER_*` variables as the runtime;
  - an Azure credential (`DefaultAzureCredential`, no keys);
  - a `--preview` flag that prints the planned calls without any network call;
  - one JSON result line per probe run (`probe`, `run`, `outcome`, `value`).

  Probes use only fixed synthetic texts defined in the module.
- [X] T002 [P] Create `code/tools/scripts/probes/live_instructions_append.py` (P1):
  1. Start a `gpt-live-1` session with client delegation and the shipped voice instructions.
  2. Send a long synthetic `session.commentary.append` (`delegation_id: null`) so the model speaks.
  3. During output, send `session.instructions.append` with a synthetic delegation update.
  4. Record whether `session.output_transcript.delta` stops within 1 s and whether `session.instructions.appended` arrives.

  Run 10 times and print the counts.
- [X] T003 [P] Create `code/tools/scripts/probes/reasoning_minimal.py` (P2). It sends 10 Responses calls to `gpt-6.1-sol` with `reasoning.effort="minimal"`, one function tool and `store=false`, then 10 with `low`, and records acceptance and first-token latency.
- [X] T004 [P] Create `code/tools/scripts/probes/replay_foreign_items.py` (P3). It sends a Responses call with `store=false` whose input replays a `function_call` and `function_call_output` of a tool that is not in the request's tools, plus an SDK-style handoff call and output, and records whether the call is accepted.
- [X] T005 [P] Write tests in `code/tools/tests/test_probes.py`:
  - `--preview` makes no network call;
  - each probe prints only closed JSON result lines;
  - all probe texts are the module's synthetic constants.
- [X] T006 With owner approval, run P1–P3 on int and record the results under R15 in `specs/007-shared-conversation-history/research.md`:
  - P1 sets the default of `CALLER_VOICE_DELEGATION_UPDATES`: `false` if any update cut the speech;
  - P2 sets the default of `CALLER_REASONING_EFFORT`: `minimal` if accepted, otherwise `low`;
  - P3 decides native rendering or the text fallback of [shared-history.md](contracts/shared-history.md).

**Checkpoint**: Defaults are decided.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Runtime settings, telemetry closed sets, the shared history, configuration 2.3.0 in the runtime, handover rules, the SDK handoff mechanism and the continuous voice session. Every story builds on these.

**⚠️ CRITICAL**: Complete before the user story phases.

### Runtime settings

- [X] T007 [P] Write failing tests in `code/backend/tests/unit/test_settings.py` and `code/backend/tests/unit/test_live_settings.py`:
  - `CALLER_TRANSCRIPTION_WAIT_SECONDS`: float, "0–2", default `0.5`;
  - `CALLER_REASONING_EFFORT`: one of `minimal`, `low`, `medium`, default `low` (T006);
  - `CALLER_VOICE_DELEGATION_UPDATES`: `true` or `false`, default `false` (T006);
  - an invalid value stops startup with `settings_invalid`.
- [X] T008 Add the three settings to `code/backend/src/caller_runtime/settings.py` (`LiveSettings`), `code/backend/src/caller_runtime/live.py` (`runtime_options`) and `RuntimeOptions` in `code/backend/src/caller_runtime/composition.py`.
- [X] T009 Add an optional `application.tuning` object (`transcription_wait_seconds`, `reasoning_effort`, `voice_delegation_updates`) to `code/infra/terraform/app_environment/variables.tf`, with the same validation as T007. Pass the set values as `CALLER_TRANSCRIPTION_WAIT_SECONDS`, `CALLER_REASONING_EFFORT` and `CALLER_VOICE_DELEGATION_UPDATES` in `code/infra/terraform/app_environment/container_apps.tf`, and extend `code/tools/tests/test_application_settings_contract.py` to cover them.

### Telemetry closed sets

- [X] T010 [P] Write failing tests in `code/backend/tests/unit/test_observability.py` for [observability.md](contracts/observability.md):
  - operations `test.handover` and `test.history`;
  - reasons `applied`, `refused_graph`, `refused_stale`, `refused_budget`, `refused_step_limit`, `condensed` and `limit`;
  - fields `source_role`, `destination_role`, `path`, `first_role`, `handled_role`, `handed_on`, `untranscribed` and `history_kib`, with their closed values (roles `welcome`, `routing`, `goodbye`, `ordinary`; paths `peer`, `direct`, `routing`, `lifecycle`; ranges `<16`, `16-64`, `64-128`, `128-192`, `>=192`);
  - measurements `handover_seconds` and `transcription_wait_seconds` under `test.latency`;
  - any free-text value is refused.
- [X] T011 Add those closed sets and fields to `code/backend/src/caller_runtime/observability.py`, without any content field (FR-018).

### Shared history

- [X] T012 [P] Write failing tests in `code/backend/tests/unit/test_shared_history.py` for [shared-history.md](contracts/shared-history.md) and [data-model.md](data-model.md#historyentry-runtime-new):
  - every entry kind renders to the item in the contract's rendering table, with the exact prefixes (`[Caller, spoken] `, `[Voice assistant, spoken] `, `[Result given to the voice assistant] `, `[Not delivered] `, `Status (<state>): `, `[Condensed earlier conversation, untrusted]`);
  - entries are ordered by `at_ms` in voice and by insertion in text;
  - one entry's content is "At most 64 KiB for one entry; longer tool results are cut with "[cut]"";
  - a history holds "At most 2,048 entries";
  - reasoning items, instructions, layers and tool definitions are never recorded or rendered;
  - `rendered_bytes` is the UTF-8 size of the rendered input.
- [X] T013 Create `code/backend/src/caller_runtime/domain/shared_history.py` with `HistoryEntry`, `Handover` and `SharedHistory` (fields, enums and limits as in [data-model.md](data-model.md)), `record_*` methods per entry kind, and `render()` producing Responses input items. Render native `function_call` items, or the text fallback if T006 decided so.

### Configuration 2.3.0 in the runtime

- [X] T014 [P] Write failing tests in `code/backend/tests/unit/test_configuration_cache.py`:
  - schema `2.3.0` loads;
  - `agents[].description` is optional and "≤ 1 KiB", and is refused above it with `configuration_text_too_large`;
  - `conversationSettings.announceAgentChanges` is an optional bool with default `false`;
  - revisions 2.0.0, 2.1.0 and 2.2.0 still load;
  - `allowedContextFields` is still accepted.
- [X] T015 Add `description` to `_Agent`, `announceAgentChanges` to `_ConversationSettings` and `"2.3.0"` to `schemaVersion` in `code/backend/src/caller_runtime/domain/configuration.py`.

### Handover rules in the platform rules

- [X] T016 [P] Extend `code/management/tests/unit/test_default_prompts.py`: `VOICE_PLATFORM_RULES` and `TEXT_PLATFORM_RULES` end with the "Handover rules:" block copied verbatim from [handover.md](contracts/handover.md#shipped-handover-rules). The byte-equality test in `code/backend/tests/contract/test_modality_defaults.py` still passes.
- [X] T017 Append the handover rules block to both platform rules in `code/management/management_app/services/default_prompts.py` and in `code/backend/src/caller_runtime/services/modality_defaults.py`.

### SDK handoffs

- [X] T018 [P] Write failing tests in `code/backend/tests/unit/test_handover_rules.py` for [handover.md](contracts/handover.md#destinations-offered-fr-008-fr-028):
  - destinations per role exactly as the table (Routing: entry agents of enabled routable communities and Goodbye; ordinary: colleagues along relationships, entry agents of connected communities, Routing only when a routable community is neither its own nor connected, Goodbye; Welcome: none);
  - sorted by destination name and named `handover_<n>`;
  - descriptions cut to 1,000 characters;
  - the Routing handoff's `community_id` enum lists exactly the routable communities that are neither its own nor connected;
  - during a transfer, only the pending destination is enabled for Routing.
- [X] T019 Create `code/backend/src/caller_runtime/services/handover_rules.py` with `destinations(snapshot, agent_id) -> list[Destination]` (`path`, `agent_id`, `community_id`, `description`, optional `community_choices`), following T018.
- [X] T020 [P] Write failing tests in `code/backend/tests/unit/test_conversation.py`:
  - an SDK handoff runs `on_handoff`, which calls `ConversationPolicy.handover(...)` with today's checks;
  - after an applied handover, the next step runs `RunResult.last_agent` with the full input list (no reset, no `transfer_context`);
  - when `on_handoff` raises, the executor records a `handover` entry with `outcome = refused` and runs the requesting agent again with the handoff output `refused: <reason>`;
  - a chain Routing → entry agent → colleague stays within the 8-step limit and the work budget.

  Replace the tests that assert the removed tools and the context filter (for example `test_text_executor_applies_directed_tool_handover_with_filtered_context`) with SDK handoff equivalents.
- [X] T021 Replace `enter_routing`, `route`, `route_to_peer`, `transfer_community` and `goodbye` with one `handover(destination, path, community_id=None, work)` in `code/backend/src/caller_runtime/services/conversation_policy.py`, following the steps in [handover.md](contracts/handover.md#applying-a-handover). The pending destination is set by a `routing` handoff with `community_id` and cleared when Routing hands over to it. Remove `transfer_context`. `restore_position` stays.
- [X] T022 Build each agent's `handoffs` in `code/backend/src/caller_runtime/services/conversation_agents.py` from `handover_rules.destinations`:
  - one SDK `Handoff` per destination (`destination_handoff`), named `handover_<n>` with the destination's description;
  - `is_enabled` for Routing's pending destination;
  - an input schema with the `community_id` enum for the Routing handoff;
  - `on_invoke_handoff` bound to `policy.handover`.

  Remove the tools `discover_peers`, `handover_agent`, `transfer_community`, `route_community`, `enter_routing` and `goodbye` from `code/backend/src/caller_runtime/services/conversation.py`.
- [X] T023 Rework the step loop in `code/backend/src/caller_runtime/services/conversation.py`:
  - build every model step from `SharedHistory.render()` plus the current work item's items (`to_input_list()` without reasoning items);
  - continue with `RunResult.last_agent` after a handoff;
  - catch handoff refusals as in T020;
  - record answers, tool calls and results, progress and handovers as entries;
  - remove `_history`, `_transcripts` and the reset on an `agent_epoch` change.

### Welcome hands over to Routing

- [X] T024 [P] Write failing tests in `code/backend/tests/unit/test_conversation.py` (FR-030):
  - the application moves Welcome → Routing (`lifecycle`) after the greeting is delivered (voice: `session.commentary.appended`; text: greeting or fallback stored), skipped or missed;
  - a request that arrives first waits for the move;
  - Welcome never runs a request.
- [X] T025 Apply the Welcome → Routing move after the greeting in `code/backend/src/caller_runtime/api/voice.py` and `code/backend/src/caller_runtime/services/conversation.py` (`leave_welcome`), with the `lifecycle` path of `policy.handover`.

### One continuous voice session

- [X] T026 [P] Write failing tests in `code/backend/tests/unit/test_admission.py`:
  - `transition` in speech no longer raises `live_segment_not_closed` and no longer raises `generation`;
  - `agentEpoch` still rises by 1;
  - `retire_generation` is unchanged.
- [X] T027 [P] Write failing tests in `code/management/tests/unit/test_test_console.py` for the relay (`relay_frames.py`) and the session client (`test_session_client.py`):
  - `agent.changed` with a `generation` equal to the current one is accepted and resets no audio state;
  - a higher `generation` still resets as today;
  - `generation.changed` with a higher generation is still accepted, because a runtime from before this feature still sends it during the rollout (management deploys first). The new runtime no longer sends it (T042).
- [X] T028 Change `transition` in `code/backend/src/caller_runtime/services/admission.py` as T026 requires.
- [X] T029 Remove `_retire_speech` and `_continue_speech` from `ConversationPolicy` and the `retire`, `continue_with` and `starting` paths of `SpeechSegments` in `code/backend/src/caller_runtime/services/conversation_policy.py`. Keep one `LiveSegment` per voice test in `code/backend/src/caller_runtime/composition.py`, and emit `agent.changed` at the current generation after an applied handover. Update the speech segment tests in `code/backend/tests/unit/test_conversation.py` (for example `test_speech_handover_retires_actual_socket_before_any_continuation`) to assert that the socket is kept.
- [X] T030 Accept same-generation `agent.changed` in `code/management/management_app/adapters/relay_frames.py`, `code/management/management_app/adapters/test_session_client.py` and `code/management/management_app/components/live_audio_js.py`, as T027 requires. This ships before T028 and T029.

### Reasoning effort

- [X] T031 Apply `CALLER_REASONING_EFFORT` to every working agent in both modes (`code/backend/src/caller_runtime/services/conversation_agents.py`), the clarity check (`_clear_task` in `code/backend/src/caller_runtime/services/conversation.py`) and the greeting request (`code/backend/src/caller_runtime/adapters/foundry_text.py`). Assert the passed effort in `code/backend/tests/unit/test_conversation.py` and `code/backend/tests/unit/test_greeting.py`.

**Checkpoint**: Shared history, SDK handoffs and the continuous voice session are in place. The user stories can start.

---

## Phase 3: User Story 1 - Text agents continue the conversation after a handover (Priority: P1) 🎯 MVP

**Goal**: In text, every agent sees the whole conversation after any handover, and a later request continues with all of it, including tool activity (FR-001, FR-002, FR-007, FR-015, FR-025).

**Independent Test**: Run A1, A3 and A6 in text. Each receiving agent uses earlier facts and tool results without asking again, also in a later request.

### Tests for User Story 1

- [X] T032 [P] [US1] Write failing tests in `code/backend/tests/unit/test_conversation.py` with scripted model doubles:
  - A1: after a peer handover, the second Billing agent's input contains the order number message;
  - A3: after a transfer through Routing, the Billing agent's input contains the earlier Billing exchange;
  - A6: the earlier agent's tool call and result are in the receiving agent's input, without a new call.
- [X] T033 [P] [US1] Write failing tests in `code/backend/tests/unit/test_text_history_repository.py` for TextConversation v2:
  - `historyVersion: 2` and `entries` replace `turns`; entries "at most 2,048"; document "≤ 512 KiB";
  - `pendingDestination` is stored;
  - a document without `historyVersion` loads as `closed`.
- [X] T034 [P] [US1] Write failing tests in `code/backend/tests/contract/test_text_endpoint.py`:
  - a later request resumes with the tool and handover entries of earlier requests (US1 AC4);
  - a pre-upgrade document starts a new conversation from Welcome, and the stream's first `conversation` event carries the new `conversationId` (FR-025);
  - a stored conversation whose agent was removed restarts from Welcome with all entries kept and moves straight to Routing, without a new greeting;
  - the greeting is stored as an `agent_answer` entry.

### Implementation for User Story 1

- [X] T035 [US1] Change `TextConversation` in `code/backend/src/caller_runtime/domain/text_history.py` to version 2: `historyVersion: Literal[2]`, `entries` (stored HistoryEntry, at most 2,048) and `pendingDestination`; `turns` is removed.
- [X] T036 [US1] Return a `closed` result for documents without `historyVersion` in `code/backend/src/caller_runtime/adapters/text_history_repository.py`. The old document is left to its TTL.
- [X] T037 [US1] In `code/backend/src/caller_runtime/services/text_conversation.py`:
  - resume loads all entries into the executor's `SharedHistory` and restores `pendingDestination`;
  - save writes version 2;
  - `closed` starts a new conversation with a new id.
- [X] T038 [US1] Send the new `conversationId` in the stream's first event (`conversation`) after a `closed` result, followed by the greeting, in `code/backend/src/caller_runtime/services/text_conversation.py` (`api/text.py` streams it unchanged).
- [X] T039 [US1] Document version 2 entries, what is stored (including tool results), the 30-day retention and the pre-upgrade closure in `docs/runbooks/text-history.md`.

**Checkpoint**: Text conversations keep their whole history across handovers and requests.

---

## Phase 4: User Story 2 - Voice agents know the whole spoken conversation (Priority: P1)

**Goal**: In voice, the working agents see every spoken exchange, delegation results stay apart from what was said, a request is understood from the conversation, and the voice assistant never forgets because its session is never replaced (FR-003, FR-004, FR-012, FR-013, FR-024).

**Independent Test**: Run A4 and A5 by voice. The working agent uses facts said only to the voice assistant, and an interrupted answer is recorded as what was actually said.

### Tests for User Story 2

- [X] T040 [P] [US2] Write failing tests in `code/backend/tests/integration/test_voice_delegation_context.py`:
  - caller and assistant transcript deltas are recorded as `caller_speech` and `assistant_speech`, merged per turn and response;
  - a fact said without any delegation is in the agent's input (A4);
  - "and the second one?" passes the history-aware clarity check;
  - the transcription wait ends early when a caller delta with `end_ms >= offset_ms` arrives;
  - after `CALLER_TRANSCRIPTION_WAIT_SECONDS` (default 0.5) the work starts, the pending speech is `partial`, and the work item is flagged `untranscribed`;
  - the voice greeting is recorded as a `delegation_result` entry followed by its `assistant_speech` (FR-002).
- [X] T041 [P] [US2] Write failing tests in `code/backend/tests/integration/test_voice_interruption_record.py` (SC-007):
  - the agent's reply is a `delegation_result` entry, apart from the transcript;
  - an interrupted response's `assistant_speech` ends at its last delta;
  - parts never sent are `not_delivered`.
- [X] T042 [P] [US2] Write failing tests in `code/backend/tests/integration/test_voice_continuous_session.py`:
  - one `session.start` for a test with two agent changes, and `generation` unchanged throughout;
  - no greeting after an agent change;
  - a result of replaced work is dropped by `agentEpoch`;
  - the T095, T097 and T098 incidents of feature 006 pass against the new flow: capture in flight during an agent change, a frame during a change, and `ping` and `audio.played` during a change;
  - in a test with tool calls and handovers, the events sent to GPT-Live carry no tool call, tool result or handover content (FR-013).
- [X] T043 [P] [US2] Write failing tests in `code/backend/tests/contract/test_gpt_live.py`:
  - `session.instructions.append` with `delegation_id: null` and content of at most 500 UTF-8 bytes;
  - `session.instructions.appended` is matched by `client_event_id`;
  - the start event's `session.input` stays `[]`.

### Implementation for User Story 2

- [X] T044 [US2] Add the `session.instructions.append` event and its acknowledgement to `code/backend/src/caller_runtime/adapters/gpt_live.py`, following T043.
- [X] T045 [US2] In `code/backend/src/caller_runtime/api/voice.py` and `code/backend/src/caller_runtime/services/conversation.py` (`accept_transcript`, `_await_transcript`):
  - record transcript deltas into the session's `SharedHistory` (merged per turn and response, `at_ms` = first `start_ms`);
  - add the transcription wait (`asyncio`, ends early on catch-up, bounded by `CALLER_TRANSCRIPTION_WAIT_SECONDS`) after the quiet delegation update;
  - mark `partial` and pass `untranscribed` to the work item.
- [X] T046 [US2] In `code/backend/src/caller_runtime/services/conversation.py`:
  - record each reply sent through `session.commentary.append` as `delegation_result`, and dropped parts as `not_delivered`;
  - give `_clear_task` the rendered history before the fragments, with the prompt "clear in the context of this conversation"; it still never rewrites the task or selects an action.
- [X] T047 [US2] Replace the Test page's Interrupt and Mute with two browser-only switches, following [voice-live.md](contracts/voice-live.md#microphone-and-loudspeaker-test-page-controls):
  - in `code/management/management_app/pages/test_console.py` and `code/management/management_app/components/live_audio_js.py`, "Mute input" stops or resumes microphone capture, and "Mute output" replaces "Interrupt output" and silences or restores playback by setting the playback volume to zero (audio keeps playing silently, so `audio.played` and pacing are unchanged). Neither switch sends a message of its own, and progress rows keep updating;
  - remove the `input.mute` and `output.interrupt` messages from `live_audio_js.py`, `code/management/management_app/adapters/relay_frames.py`, `code/management/management_app/adapters/test_session_client.py` and `code/backend/src/caller_runtime/api/voice.py`, plus `ConversationPolicy.interrupt` in `code/backend/src/caller_runtime/services/conversation_policy.py` and `set_muted` in `code/backend/src/caller_runtime/adapters/gpt_live.py`.

  Tests:
  - replace the relay and engine tests for both messages in `code/management/tests/unit/test_test_console.py` with browser engine tests: neither switch sends a message of its own, `audio.played` continues at the playback pace while "Mute output" is on, and audio is heard again when it is turned off;
  - update `test_console_synthetic_speech_pcm_mute_interrupt_and_confirmed_stop` in `code/management/tests/browser/test_test_page.py` to the two switches;
  - update the runtime tests that send either message (`code/backend/tests/contract/test_voice_endpoint.py`, `code/backend/tests/integration/test_conversation.py`, `code/backend/tests/unit/test_admission.py`, `code/backend/tests/unit/test_conversation.py`).
- [X] T048 [US2] Remove the read-loop tolerances for a generation ahead of the policy during an agent change (`segments.starting`) from `code/backend/src/caller_runtime/api/voice.py`, because an agent change no longer changes the generation. T042 keeps the incident reproductions.

**Checkpoint**: Voice agents see the spoken conversation, and the voice session lasts the whole call.

---

## Phase 5: User Story 3 - The right agent handles the current request (Priority: P1)

**Goal**: Requests move along the permitted paths to the agent that fits, quietly, and the voice assistant knows what to delegate (FR-008, FR-011, FR-023, FR-026–FR-029). The Welcome → Routing move (FR-030) is foundational (T024, T025).

**Independent Test**: Run A2, A3, A8 and A9 in both modalities. Each request reaches the expected agent over the expected path without an announcement.

### Tests for User Story 3

- [X] T049 [P] [US3] Write failing tests in `code/backend/tests/unit/test_delegation_update.py` for [voice-live.md](contracts/voice-live.md#delegation-update-caller_voice_delegation_updatestrue):
  - the text starts with "Delegation update. This replaces all earlier delegation updates.", followed by "Backend tools:" with one line per reachable community: its routing description (its name only when the description is empty);
  - the reachable set per position (own community, connected communities, routable communities through Routing; all routable communities before a community is active);
  - lines cut to 160 characters; "at most 2,000 UTF-8 bytes";
  - when not all communities fit, the last tool line is "- …and more capabilities" and "For other requests, Generate <|SpawnThinking|> too; the backend decides whether it can help." replaces the "If no listed capability fits" sentence;
  - the delegation phrase is `Generate <|SpawnThinking|>`, the same as in the shipped voice conversation instructions (`modality_defaults.py`);
  - parts of at most 500 UTF-8 bytes at line boundaries, the first carrying the "replaces" sentence;
  - an update is sent only when the reachable set changes;
  - with updates off, the static block "(≤ 4 KiB)" with the same lines and cut rule is appended to the start instructions, and the total stays "at most 16 KiB".
- [X] T050 [P] [US3] Write failing tests in `code/backend/tests/integration/test_handover_paths.py` with scripted model doubles, in both modalities, on the synthetic configuration of the spec:
  - A2 goes directly to Technical Support's entry agent;
  - A3 goes through Routing with `community_id`, and in text the pending destination survives between requests;
  - A8 in voice reaches Technical Support, not Billing;
  - A9 reaches the second Billing agent with the corrected fact;
  - an entry agent that receives a request hands on to its colleague without a user-visible reply.
- [X] T051 [P] [US3] Extend `code/backend/tests/unit/test_handover_rules.py`:
  - with `announceAgentChanges = true`, the rules gain "After a handover, the receiving agent says briefly who is now helping."; with `false`, they do not;
  - a colleague's description is its `description`, otherwise its name.
- [X] T052 [P] [US3] Write failing tests in `code/management/tests/contract/test_configuration_api.py`:
  - `2.3.0` is written only when `description` or `announceAgentChanges` is present;
  - a description over "1 KiB UTF-8" is refused with `configuration_text_too_large` and the path `agents[N].description`;
  - testers receive neither field.
- [X] T053 [P] [US3] Write failing browser tests:
  - `code/management/tests/browser/test_configuration_editor.py`: the description text area with a byte counter; no "Request summary" or "Transfer reason" checkboxes; connections labelled "Direct handover to the entry agent";
  - `code/management/tests/browser/test_settings_page.py`: "Announce agent changes" is off by default, publishes and restores; the existing switch is labelled "Quiet update on delegation".

### Implementation for User Story 3

- [X] T054 [US3] Create `code/backend/src/caller_runtime/services/delegation_update.py` with `delegation_update(snapshot, slot) -> list[str]` and `static_delegation_block(snapshot) -> str`, following T049.
- [X] T055 [US3] Wire the delegation updates:
  - send the first update right after `session.started` and before the greeting text is appended (`code/backend/src/caller_runtime/api/voice.py`);
  - send an update after an applied handover when the reachable set changed (`code/backend/src/caller_runtime/services/conversation.py`);
  - when `CALLER_VOICE_DELEGATION_UPDATES` is false, append `static_delegation_block` in `live_instructions` (`code/backend/src/caller_runtime/composition.py`);
  - remove the quiet context note (`context_note`, `send_context`).

  Update the context-note tests in `code/backend/tests/contract/test_voice_endpoint.py`.
- [X] T056 [US3] Use the agent `description` and add the announce line in `code/backend/src/caller_runtime/services/handover_rules.py` and in the composed instructions (`compose_instructions` in `code/backend/src/caller_runtime/domain/configuration.py`). Remove the Routing JSON directory (`routing_directory`, T094 of feature 006) from `code/backend/src/caller_runtime/services/conversation_policy.py` and `conversation.py`.
- [X] T057 [US3] Add `description` and `announceAgentChanges` to `code/management/management_app/models/configuration.py` and `code/contracts/configuration.schema.json` (2.3.0). Add the limit and the version rule to `code/management/management_app/services/configuration_validation.py` and the publication and repository code. Remove both fields from tester projections in `code/management/management_app/api/configuration.py`.
- [X] T058 [US3] Add the "Description (used to choose handover destinations)" text area with a byte counter to `code/management/management_app/pages/agents.py` and its draft state in `code/management/management_app/state/editor_draft.py`.
- [X] T059 [US3] Remove the "Request summary" and "Transfer reason" checkboxes and label connections "Direct handover to the entry agent" in `code/management/management_app/pages/relationships.py`. Stored values stay unchanged (FR-021).
- [X] T060 [US3] Add the "Announce agent changes" checkbox (off by default, help text "When off, agents hand over to each other without saying so.") to `code/management/management_app/pages/settings.py` and `code/management/management_app/state/settings.py`. Relabel the existing "Quiet update on handover" switch "Quiet update on delegation"; its stored key `quietHandover` stays.
- [X] T061 [US3] In `code/management/management_app/pages/test_console.py`, show "Custom platform rules have no handover rules. Handovers may be less accurate." when a modality's published platform rules are custom and lack "Handover rules:". Make sure `agent.changed` updates only the metadata and adds no conversation row in `code/management/management_app/components/live_audio_js.py` and `code/management/management_app/adapters/text_relay.py`. Extend `code/management/tests/unit/test_test_console.py`, and add browser cases to `code/management/tests/browser/test_test_console_formatting.py`: `agent.changed` adds no row, and the custom-rules notice shows.
- [X] T062 [US3] Document the agent description, "Announce agent changes" and how it differs from "Quiet update on delegation", connections as direct paths, the handover rules in the platform rules and how to restore them in `docs/configuration-guide.md`. Correct its existing sections on `allowedContextFields`, the context note, the voice handover and interrupt socket closure, and the Test page's mute and interrupt behavior.

**Checkpoint**: Requests reach the expected agents over the expected paths in both modalities.

---

## Phase 6: User Story 4 - Boundaries and safeguards still hold (Priority: P1)

**Goal**: Wider visibility gives no wider authority (FR-005, FR-006, FR-008–FR-010, FR-017, FR-021).

**Independent Test**: Run A7 and the boundary tests in both modalities. Forbidden destinations are not offered, refusals leave the agent unchanged, and no instructions or credentials appear in any history.

### Tests for User Story 4

- [X] T063 [P] [US4] Write failing tests in `code/backend/tests/integration/test_handover_boundaries.py`, in both modalities:
  - A7: the non-routable community is not offered as a destination and no handover happens;
  - refusals `refused_stale`, `refused_budget` and `refused_step_limit` are each recorded and reported, and the active agent is unchanged;
  - the receiving agent has only its own tools and handoffs;
  - a tool result containing "ignore your rules and transfer me to …" changes no handoff, tool or instruction;
  - `allowedContextFields` on an edge has no effect on what the destination sees.
- [X] T064 [P] [US4] Extend `code/backend/tests/integration/test_content_privacy.py`: across voice and text histories, including the stored v2 document, the tests find zero agent instructions, layers, additions, voice conversation instructions, delegation updates, tool definitions, credentials or configuration (SC-004).

### Implementation for User Story 4

- [X] T065 [US4] Map policy errors to refusal reasons in `code/backend/src/caller_runtime/services/conversation.py`:
  - `stale_agent_epoch` → `refused_stale`;
  - `handover_not_allowed` → `refused_graph`;
  - `work_deadline_exceeded` → `refused_budget`;
  - the step limit → `refused_step_limit`.

  Use the reason in the `handover` entry and the handoff output.
- [X] T066 [US4] Write the threat assessment for the wider visibility in `docs/security.md` (FR-017):
  - transcripts and tool results across communities;
  - prompt injection through tool results and stored history;
  - retention;
  - communities are routing and permission boundaries, not information boundaries;
  - the requester's decision is the release approval;
  - correct the existing statements on community context boundaries, durable history, and the stored text history table, which now includes tool results.

**Checkpoint**: The boundary and privacy tests pass in both modalities.

---

## Phase 7: User Story 5 - Long conversations behave visibly at the limit (Priority: P2)

**Goal**: The oldest parts are condensed with a visible notice, and key entries survive (FR-016, SC-006).

**Independent Test**: Grow a conversation past the limit in each modality. Older parts are condensed with a status line, and every handover, final or waiting progress entry and confirmation survives.

### Tests for User Story 5

- [X] T067 [P] [US5] Write failing tests in `code/backend/tests/unit/test_condensing.py`:
  - the trigger is a rendered history "above 192 KiB", or a new entry that would exceed 2,048 entries;
  - the oldest entries are condensed until the history is "below 128 KiB" and below 2,048 entries;
  - kept verbatim: every `handover` entry, every `progress` entry, and the user or caller entry directly after an `agent_answer` or `delegation_result` ending with a question mark, or after a `waiting_for_input` progress entry;
  - one `condensed` entry results;
  - the call has no tools and `max_output_tokens` 2,048;
  - if it fails, the history is still above 256 KiB, or the kept entries alone exceed 2,048, the conversation ends with "This conversation became too long. Please start a new one." and `conversation_context_limit`.
- [X] T068 [P] [US5] Write failing tests in `code/backend/tests/contract/test_text_endpoint.py`:
  - `history.condensed` precedes the answer;
  - the limit ends with `error` and `conversation_context_limit`;
  - a document over 512 KiB triggers condensing before save.
- [X] T069 [P] [US5] Write failing tests in `code/management/tests/unit/test_test_console.py` and `code/management/tests/browser/test_test_console_formatting.py`: `history.condensed` adds the status row "Older parts of this conversation were condensed." (`data-kind="status"`) in both modalities, and it is never spoken.

### Implementation for User Story 5

- [X] T070 [US5] Create `code/backend/src/caller_runtime/services/condensing.py`, following T067, with the reasoning effort of T031.
- [X] T071 [US5] Run condensing before each model step in `code/backend/src/caller_runtime/services/conversation.py`. Emit `history.condensed` as a voice control event (`code/backend/src/caller_runtime/api/voice.py`) and a text stream event (`code/backend/src/caller_runtime/api/text.py`).
- [X] T072 [US5] Accept and show `history.condensed` in `code/management/management_app/adapters/text_relay.py`, `code/management/management_app/adapters/test_session_client.py` and `code/management/management_app/components/live_audio_js.py`.

**Checkpoint**: Long conversations condense visibly instead of failing.

---

## Phase 8: User Story 6 - Operators see handover health (Priority: P3)

**Goal**: Content-free handover, delegation and history events, queries and an alert (FR-018, FR-019, SC-009).

**Independent Test**: Every handover in the tests produces the expected event. The alert's query matches the documented threshold.

### Tests for User Story 6

- [X] T073 [P] [US6] Write failing tests in `code/backend/tests/unit/test_conversation.py` and `code/backend/tests/integration/test_voice_delegation_context.py`:
  - one `test.handover` per applied or refused handover, with roles and `path`;
  - `test.delegation` with `first_role`, `handled_role`, `handed_on` and `untranscribed`;
  - `test.history` with the `history_kib` range at each applied handover, and `condensed`/`limit`;
  - `test.latency` `handover_seconds` with `path`, and `transcription_wait_seconds`;
  - each new event carries the session's correlation and trace identifiers;
  - no event carries content.
- [X] T074 [P] [US6] Add a test in `code/tools/tests/test_check_terraform_structure.py`: `code/infra/terraform/modules/monitoring/main.tf` defines a `handover-failures` entry in `local.query_alerts` with the query of [observability.md](contracts/observability.md#alert-handover-failures-fr-019).

### Implementation for User Story 6

- [X] T075 [US6] Emit the events of T073 in `code/backend/src/caller_runtime/services/conversation.py` and `code/backend/src/caller_runtime/api/voice.py`.
- [X] T076 [US6] Add the `handover-failures` query alert to `local.query_alerts` in `code/infra/terraform/modules/monitoring/main.tf` (threshold 0 on `Count`, the module's 15-minute window and 5-minute evaluation, existing owner, runbook and action group).
- [X] T077 [US6] Document `test.handover`, `test.history`, the delegation fields, the handover outcome, SC-005, SC-009 and P4 queries of [observability.md](contracts/observability.md), and a `#handover-failures` section with response steps in `docs/runbooks/monitoring.md`.

**Checkpoint**: Handover health is visible and alerting.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T078 [P] Write load and failure tests in `code/backend/tests/load/test_handover_capacity.py`: 5 concurrent voice sessions at the slot limit with agent changes, refused handovers, condensing and slow transcripts. There are no extra admission refusals, every slot is released, one Live attempt per session settles at the end, and every answer after a handover starts within the work budget (SC-005 target).
- [X] T079 [P] Update `docs/conversation-endpoints.md`: the shared history, SDK handovers and paths, the continuous voice session and delegation updates, the transcription wait, and Mermaid sequence diagrams for a same-community handover, a direct community handover, a transfer through Routing, and a voice delegation with its delegation update (FR-020). Correct the existing protocol tables and sequences: remove `input.mute`, `output.interrupt`, `generation.changed` and the context note, and add `history.condensed`.
- [X] T080 [P] Update `docs/architecture.md`: the voice assistant and the working agents with one continuous session, and history ownership (Mermaid). Replace the context note in the diagram and text, and the socket retirement on a voice interrupt or handover.
- [X] T081 [P] Update `docs/runbooks/scaling.md` (one Live attempt per call; the microphone and loudspeaker switches act only in the browser; recovery), `docs/runbooks/configuration.md` (2.3.0 rollback order: runtime first, then management), `code/contracts/README.md` (schema 2.3.0), `docs/runbooks/communities.md` (SDK handoffs instead of `route_community`, direct handovers to the entry agents of connected communities, and the whole history instead of the "Transfer Content" fields), and `docs/setup.md` and `docs/testing.md` ("Mute input" and "Mute output" instead of Interrupt).
- [X] T082 [P] Document `CALLER_TRANSCRIPTION_WAIT_SECONDS`, `CALLER_REASONING_EFFORT` and `CALLER_VOICE_DELEGATION_UPDATES`, with defaults and ranges, in `docs/environments.md`.
- [X] T083 Add a section to `docs/capacity.md` for the handover delay per path and modality (SC-005), input size per turn, cost per conversation, the T078 results and the option A criteria (R1), marked pending until T087.
- [X] T084 Run the full checks (`ci.py check` and `verify_docs.py`, per [quickstart.md](quickstart.md)) and fix any failure.
- [X] T085 With owner approval, deploy to int:
  1. publish the images;
  2. deploy management first, then the runtime (relay compatibility, T030);
  3. pin the tags in `code/infra/environments/int/int.tfvars.json`;
  4. run `terraform-deploy`.

  Done 2026-11-24:
  - Images: `sha-b2bb1f7aade5205151cd77a7f203f61333e67679` (#52 merge), both tags pinned in one PR (#53). The owner chose this over the management-first order.
  - Run 45508163173 applied, and every environment check passed, including the functional journey.
  - Fixes on the way:
    - The tenant policy `StorageAccount_PublicNetwork_Modify` had disabled public access on the state account; the owner tagged it `SecurityControl=Ignore` and re-enabled access.
    - The check's speech step now acts as an open microphone and speaker for 6 s before it stops (#54–#56).
    - One slot held by an earlier check was released with `caller_runtime.recovery`.
- [X] T086 After at least 20 voice delegations on int, run the [P4 query](contracts/observability.md#p4-transcription-wait). Record the untranscribed share and the actual wait under R15 in `specs/007-shared-conversation-history/research.md`, and raise `CALLER_TRANSCRIPTION_WAIT_SECONDS` (0–2) if more than 10 % end untranscribed.

  Done 2026-11-26: 56 % untranscribed with the 0.5 s wait (32 delegations); raised to 2 s for int in #62, which takes effect with the next `terraform-app-environment` apply. The documented queries now run in Log Analytics (`kind` is reserved, `percentileif` does not exist). Measured again with 2 s after the apply of #68: 40 % untranscribed (42 delegations), and no wait on int ever ended by catching up, so int now uses 0 s (owner decision; see R15).
- [X] T087 With the owner, run the acceptance set A1–A9 by voice and by text (18 runs) per [quickstart.md](quickstart.md#3-acceptance-set-on-int-a1a9-sc-001sc-003). Record destination, context use, pair consistency and quietness in `specs/007-shared-conversation-history/checklists/review.md`. Pass: SC-001, SC-002, SC-003, and SC-011 in at least 16 of 18 runs.

  Done 2026-11-26: 16 of 16 runs possible on int passed; A6 needs a reviewed tool on int and stays covered by the automated tests. Defects found on the way were fixed in #58–#65.
- [X] T088 From the T087 runs, check the SC-005 target per [observability.md](contracts/observability.md#sc-005-target-check) (no run ended with `work_deadline_exceeded`, and no handover was refused with `refused_budget`), and record the SC-005 and SC-009 measurements and the option A criteria in `docs/capacity.md`.
- [X] T089 With owner approval, verify alert delivery per [quickstart.md](quickstart.md#5-alert-delivery-fr-019) and restore the alert condition afterwards.

  Done 2026-11-26: the temporary condition fired after the owner's A1 text run and ran the action group; the email was not checked because the operations mailbox is not set up yet (accepted by the owner). The condition was restored and verified.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: none. T006 needs the owner and int access, and its results set defaults used by T007, T013 and T031.
- **Foundational (Phase 2)**: depends on Setup. Blocks all stories.
  - T030 (management relay) must be deployed before T028 and T029 reach int.
  - T021–T023 replace the application handover tools, so all stories use SDK handoffs.
  - T024 and T025 (Welcome → Routing after the greeting) are needed by every story, because Welcome has no handoffs.
- **US1 (Phase 3)**: depends on Phase 2 only.
- **US2 (Phase 4)**: depends on Phase 2 only.
- **US3 (Phase 5)**: depends on Phase 2. Its voice parts (T054, T055) use the instructions event of US2 (T044).
- **US4 (Phase 6)**: depends on Phase 2. Its tests run against US1–US3 behavior when those are present.
- **US5 (Phase 7)**: depends on US1 (text storage) for T068.
- **US6 (Phase 8)**: depends on Phase 2. The emission tests cover the paths of US3.
- **Polish (Phase 9)**: after the stories it documents. T078 must pass before the deployment (T085). T086–T089 run after T085.

### Story dependency graph

```mermaid
flowchart LR
    S[Setup probes] --> F[Foundational]
    F --> US1[US1 Text continuity]
    F --> US2[US2 Voice context]
    F --> US3[US3 Right agent]
    F --> US4[US4 Boundaries]
    F --> US6[US6 Handover health]
    US2 --> US3
    US1 --> US5[US5 Limits]
    US1 & US2 & US3 & US4 & US5 & US6 --> P[Polish and int]
```

### Within each story

1. Tests are written first and must fail.
2. Then domain and models, then services, then endpoints and relay, then the browser.
3. Files shared between stories (`conversation.py`, `conversation_policy.py`, `voice.py`, `live_audio_js.py`, `text_relay.py`, `test_session_client.py`) are edited one story at a time.

## Parallel Opportunities

| Phase | Parallel tasks |
|-------|----------------|
| Setup | T002 ∥ T003 ∥ T004 ∥ T005 after T001; then T006 |
| Foundational | T007 ∥ T010 ∥ T012 ∥ T014 ∥ T016 ∥ T018 ∥ T020 ∥ T024 ∥ T026 ∥ T027; then T008 → T009, T011, T013, T015, T017, T019 → T021 → T022 → T023 → T025, T028 → T029, T030; then T031 |
| US1 | T032 ∥ T033 ∥ T034; then T035 → T036 → T037 → T038; T039 |
| US2 | T040 ∥ T041 ∥ T042 ∥ T043; then T044 → T045 → T046 → T047 → T048 |
| US3 | T049 ∥ T050 ∥ T051 ∥ T052 ∥ T053; runtime track T054 → T055 → T056 ∥ management track T057 → T058 → T059 → T060 → T061; T062 |
| US4 | T063 ∥ T064; then T065; T066 |
| US5 | T067 ∥ T068 ∥ T069; then T070 → T071 ∥ T072 |
| US6 | T073 ∥ T074; then T075 ∥ T076; T077 |
| Polish | T078 ∥ T079 ∥ T080 ∥ T081 ∥ T082; then T083 → T084 → T085 → T086 → T087 → T088 → T089 |

### Parallel example: User Story 3

```text
Together: T049 test_delegation_update.py | T050 test_handover_paths.py | T052 test_configuration_api.py | T053 browser tests
Then: runtime track T054 → T055 → T056  ||  management track T057 → T058 → T059 → T060 → T061
```

## Implementation Strategy

### MVP (User Story 1)

1. Phases 1–2.
2. Phase 3 (US1 text continuity).
3. Validate: A1, A3 and A6 in text use earlier facts and tool results without asking again, also across requests.

### Increments

1. Add US2 (voice context and the continuous session), then US3 (paths, quiet handovers, delegation updates, editor fields), each validated on its own.
2. Add US4 (boundaries and privacy) before any int deployment.
3. Add US5 (condensing) and US6 (telemetry and alert).
4. Run the polish tasks, the load tests (T078) and the full checks (T084), then deploy (T085), measure (T086, T088) and run the acceptance set (T087) and the alert check (T089).

## Phase 10: Convergence

- [X] T099 Publish a reviewed synthetic tool for a Billing agent of the int acceptance configuration, run acceptance scenario A6 by voice and by text, and record both runs and the updated SC-001, SC-002 and SC-003 results in `specs/007-shared-conversation-history/checklists/review.md` per SC-001/SC-002/SC-003 (US1/AC2) (partial)

  Done 2026-11-26: waived by the owner. Running A6 on int needs a synthetic reviewed tool endpoint, which this feature does not add; A6 stays covered by `test_a1_a6_a_colleague_sees_earlier_facts_and_tool_results_without_a_new_call`. Recorded in `checklists/review.md`; SC-001 to SC-003 stay at 16 of 16 runs and 8 of 8 pairs on int.
- [X] T100 Review the runtime changes made for acceptance defects that no task requested (the automatic release of Live-held slots in `ConversationRuntime.release_live_slots` from #63 and #65, the `test.live.failure` and `test.model.failure` diagnostics from #58, #61, #65 and #70, and the ETag check in `TestAdmissionService._change` from #70), and record each as an accepted safeguard change in `specs/007-shared-conversation-history/research.md` or remove it per FR-010 (unrequested)

  Done 2026-11-26: all four changes reviewed and accepted (the owner merged them); recorded as R16 in `research.md` with what each changes and why it stays within FR-010 and Constitution VI and VII.
