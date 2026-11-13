# Implementation Plan: Modality-Aware Agent Interaction

**Branch**: `006-modality-aware-agents` | **Date**: 2026-11-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/006-modality-aware-agents/spec.md`

## Summary

The same agents should behave well in both voice and text. The work ships in two independently deployable slices; decisions are in [research.md](research.md).

**Slice 1** (User Stories 1–4, no stored-configuration change):

- **Voice greeting.** When a voice session is ready, the Welcome agent writes the greeting text in one bounded model call (low effort, ≤256 tokens, no tools), and the speaking model says it through `session.commentary.append`. There is no fallback, so a late greeting counts against SC-001 (R1).
- **Text greeting.** Text conversations open with a new open request whose first reply is the Welcome greeting. A fixed fallback is sent after 4 s.
- **Speaking-model guidance.** GPT-Live gets shipped *voice conversation instructions* plus a quiet context note (active agent and reachable communities) instead of the active agent's prompt.
- **Quiet handover.** Every delegation gets a quiet thinking update (best effort, because Foundry rejects `acknowledgement_delay_ms`).
- **Long spoken answers.** These are split into ordered parts of at most 500 bytes. Remaining parts are dropped when the caller interrupts.
- **Formatted text.** The Test page shows formatted text from a server-built safe node tree, never HTML.
- **Default prompts.** Welcome, Routing and Goodbye get best-practice defaults through a one-time operator command and a "Restore default" action.

**Slice 2** (User Stories 5–7, configuration schema `2.2.0`):

- **Editable layers.** Platform rules and a global prompt per modality, editable voice conversation instructions, and per-agent voice and text additions are published through the existing Settings and Community and Agent drafts.
- **Progress reporting.** A `report_progress` tool gives agent-written progress, shown as status lines and sent to be spoken. The existing one-tool-per-step executor loop already continues after a tool call (R8).
- **Quiet-update switch.** The quiet handover update can be switched on or off.

## Technical Context

**Language/Version**: Python 3.12 (`.python-version` 3.12.15) for both applications. The browser code of the Test page is JavaScript generated through the Reflex component (`live_audio_js.py`), as allowed by Constitution V.

**Primary Dependencies**:

- Existing: FastAPI, the OpenAI SDK Live client and the OpenAI Agents SDK in the runtime, Reflex 0.10 in management, Azure Identity, and the Azure Cosmos DB client.
- New direct dependency in management: `markdown-it-py`, already present transitively in `code/management/uv.lock`. It is recorded in `docs/library-decisions.md`.

**Storage**:

- Existing Cosmos DB configuration revisions (management) and text history (runtime).
- Slice 2 adds optional fields to the configuration document (schema `2.2.0`). There is no new container and no new Azure resource.

**Testing**:

- pytest (unit, contract, integration) in `code/backend` and `code/management`;
- Playwright browser tests in `code/management/tests/browser`;
- ruff, mypy;
- `code/tools/scripts/check_whitespace.py` and `verify_docs.py`.

**Target Platform**: Azure Container Apps (`ca-caller-<env>-runtime`, `ca-caller-<env>-management`); Azure AI Foundry `gpt-live-1` (client delegation) and the existing text deployment; desktop Edge and Chrome for the Test page.

**Project Type**: Web application: runtime service plus Reflex management frontend with a FastAPI management API.

**Performance Goals**:

- First greeting audio starts within 5 s of ready in at least 90% of voice sessions (SC-001).
- Text greeting within 5 s of the open request in 100% of conversations: the model greeting has 4.0 s, then the fallback is sent (SC-002).
- Agent-reported progress within 5 s in at least 5 of 6 reviewed runs (SC-004).

**Constraints**:

| Area | Constraint |
|------|------------|
| GPT-Live appends | At most 500 UTF-8 bytes each and non-empty |
| Instructions | GPT-Live instructions at most 16 KiB; voice conversation instructions at most 12 KiB |
| Layers and additions | Each layer at most 8 KiB; each addition at most 4 KiB; composed instructions per agent at most 36 KiB |
| Foundry | `acknowledgement_delay_ms` is unsupported (int probe) |
| Formatted text | No HTML may reach the DOM |
| Slice boundary | Slice 1 changes no stored configuration |
| Compatibility | Revisions from earlier releases (2.0.0, 2.1.0) must load, verify twice, project and re-publish (lessons from PR #42 and #43) |
| Infrastructure | No new Azure resource or identity |

**Scale/Scope**: Today's limits are unchanged: one active test per manager, at most 5 occupied slots, up to 50 communities and 200 agents. The greeting adds one bounded model call per new text conversation and per new voice session.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Azure-First | Pass | Foundry GPT-Live and the existing text deployment only. Model capability was verified on int before design: greet-first works and the acknowledgement delay is rejected (assessment research, R1, R3). |
| II. Security-First | Pass | Model output is untrusted. Formatted text is a server-built allowlisted tree rendered with `createElement` and `textContent`, with https-only links and images (R6). Editor-only fields are role-checked server-side. Logs and prompts carry no secrets. Threats are listed below. |
| III. Documentation | Pass | The documentation deliverables are listed below. They include Mermaid sequences for the voice and text openings and the progress flow. |
| IV. Bounded Microservices | Pass | Management stays the owner of configuration and defaults; the runtime reads the projection. The text endpoint and configuration contract changes are additive, versioned and documented, with a rollback plan ([configuration-v2.2.md](contracts/configuration-v2.2.md)). |
| V. Python and Reflex | Pass | All logic is in Python. Markdown parsing happens in the Python relay. Browser code stays generated by the Reflex component. |
| VI. Real-Time Call Handling | Pass | New targets are defined: first greeting audio within 5 s at 90% (voice) and greeting within 5 s at 100% (text), measured on int (SC-001). Queues and timeouts are bounded: the voice greeting request is cancelled at 5 s, a 4 s text greeting deadline, 2 s part pacing, a 1,500-byte dropped note, and a progress rate limit. Admission limits and concurrency are unchanged (spec Assumptions: Capacity). Failure behavior is covered by automated tests (slow text model leads to the fallback; a slow or failing voice greeting is cancelled and logged as `deadline`). No load test of the greeting target exists yet, so this feature adds one: load and failure tests at the declared concurrency of 5 occupied slots, with test doubles (T087), plus the int measurement of real model latency (T091). Both are recorded in `docs/capacity.md`. |
| VII. Structured Logging | Pass | New closed-set, content-free operations: `test.greeting`, `test.speech_parts`, `test.progress` ([observability.md](contracts/observability.md)). |
| VIII. Terraform | Pass | No infrastructure change; image tags are pinned through the existing tfvars flow. |
| IX. Automated Tests | Pass | Every functional requirement maps to automated tests ([quickstart.md](quickstart.md)). The manual review applies only to qualitative consistency (SC-005), as the requester decided. |

### Threats and controls (Principle II)

| Threat | Control |
|--------|---------|
| Script or HTML injection through model answers | Markdown parsed with `html=False` into an allowlisted node tree. The browser builds DOM nodes with `textContent` and never parses HTML. The tree has size and depth limits with a plain-text fallback (FR-011). |
| Phishing or tracking through links and images | Only `https:` is kept. Links open in a new tab with `noopener noreferrer` and only on click. Images use `no-referrer` and lazy loading. Everything else shows as plain text. |
| Prompt content leaking into logs | Telemetry uses closed sets with no message, greeting, note or progress text (FR-035). |
| Editable platform rules used to break behavior | Editor role only, a confirmation dialog, a Test page notice when rules are empty, publication history attribution (FR-029, FR-030, FR-036). |
| Oversized prompts exhausting model budgets | Per-field and composed limits, checked by management on publish and by the runtime on load (FR-033). |
| Progress updates claiming false outcomes | An accepted risk (FR-022). Platform rules instruct otherwise, and the "failing call" scenario checks it. |
| Stale configuration after rollback | A documented rollback order, and runtime refusal of unknown fields ([configuration-v2.2.md](contracts/configuration-v2.2.md)). |

## Project Structure

### Documentation (this feature)

```text
specs/006-modality-aware-agents/
├── plan.md              # This file
├── research.md          # Phase 0 decisions R1–R12
├── data-model.md        # Phase 1 data and session state
├── quickstart.md        # Phase 1 validation guide
├── contracts/
│   ├── configuration-v2.2.md
│   ├── text-endpoint.md
│   ├── voice-live.md
│   ├── test-console.md
│   └── observability.md
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
code/backend/src/caller_runtime/
├── adapters/
│   ├── gpt_live.py              # S1: event IDs and ack matching for parts (session.input unchanged)
│   └── foundry_text.py          # S1: greeting request (low effort, ≤256 tokens, no tools)
├── services/
│   ├── modality_defaults.py     # S1 new: runtime copies of shipped voice instructions and platform rules (checked against management)
│   ├── greeting.py              # S1 new: greeting texts, fallback, greeting state
│   ├── speech_parts.py          # S1 new: splitter, pacing, drop-and-note
│   ├── conversation.py          # S1: quiet handover, parts, greeting history; S2: report_progress, fallback-only lines
│   ├── conversation_policy.py   # S1: context note after each segment start
│   ├── conversation_agents.py   # S2: layer composition per mode, report_progress tool
│   └── text_conversation.py     # S1: open request, greeting turn stored
├── api/
│   ├── text.py                  # S1: open request; S2: progress event
│   └── voice.py                 # S1: ready → greeting timing; S2: progress control event
├── domain/configuration.py      # S2: accept 2.2.0 and the resolved projection fields
├── composition.py               # S1: instructions from voice conversation instructions, not agent prompt
└── observability.py             # S1/S2: test.greeting, test.speech_parts, test.progress

code/management/management_app/
├── services/
│   ├── default_prompts.py       # S1 new: Welcome/Routing/Goodbye, voice instructions; S2: layer defaults
│   ├── apply_default_prompts.py # S1 new: one-time operator command
│   ├── formatted_text.py        # S1 new: Markdown → allowlisted tree
│   ├── bootstrap.py             # S1: uses default_prompts
│   └── configuration_validation.py  # S2: limits, confirmation flag
├── adapters/
│   ├── text_relay.py            # S1: open request on ready, text.formatted; S2: progress
│   └── configuration_repository.py  # S2: 2.2.0, resolved projection, absent stays absent
├── models/configuration.py      # S2: new optional fields, 2.2.0
├── components/live_audio_js.py  # S1: formatted rows; S2: status rows (text and voice)
├── pages/
│   ├── settings.py              # S2: Voice and Text sections, confirmation dialog
│   ├── agents.py                # S1: Restore default (pre-defined); S2: additions
│   └── test_console.py          # S1/S2: notice for empty platform rules, status row style
└── state/settings.py            # S2: drafts for new fields

code/contracts/configuration.schema.json   # S2: 2.2.0
docs/                                       # updated, see below
```

**Structure Decision**: The existing two-application layout (`code/backend`, `code/management`) is kept. No new service or package is added. Slice tags (S1/S2) above map files to the two independently deployable slices.

### Documentation deliverables (Principle III)

| Document | Change |
|----------|--------|
| `docs/conversation-endpoints.md` | Open request, `progress` event, voice greeting and context note, spoken parts; Mermaid sequences for the voice and text openings |
| `docs/configuration-guide.md` | Voice conversation instructions, layers, additions, quiet handover, limits, restore defaults |
| `docs/runbooks/configuration.md` | One-time `apply_default_prompts` step, schema 2.2.0 rollback order |
| `docs/runbooks/monitoring.md` | `test.greeting`, `test.speech_parts`, `test.progress`, SC-001 query |
| `docs/architecture.md` | Speaking model vs working agents; layer composition (Mermaid) |
| `docs/capacity.md` | Declared greeting targets, load and failure test results (T087), the int measurement (T091), and the extra model call per text conversation and voice session |
| `docs/library-decisions.md` | `markdown-it-py` direct dependency |
| `code/contracts/README.md` | Schema 2.2.0 |

## Complexity Tracking

No constitution violations. Principle VI is met by the load and failure tests added in T087, together with the int measurement (T091).

## Post-Design Constitution Re-check

All nine principles still pass after Phase 1:

- **Contracts.** They are additive. The schema change has a compatibility and rollback plan (IV). The greeting and progress events are content-free in telemetry (VII).
- **Rendering.** Formatted text never reaches the DOM as HTML (II).
- **Code location.** All new code sits in the existing Python packages (V).
- **Unknowns.** No `NEEDS CLARIFICATION` remains.

**Open risk.** Text greeting latency on the reasoning deployment. It is mitigated by the 4 s fallback (R5). With the fallback, every text conversation gets a greeting within 5 s; the remaining risk is how often the fallback replaces the model's greeting. The `test.greeting` reason `fallback` tracks this.
