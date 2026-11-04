# Implementation Plan: Codebase Simplification

**Branch**: `003-codebase-simplification` | **Date**: 2026-11-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-codebase-simplification/spec.md`

## Summary

A cleanup-focused feature whose only new capability is server-side, per-user text conversation history stored in Azure Cosmos DB (text only, expiring 30 days after the last turn). It shrinks the backend to two public conversation endpoints (text and voice) sharing one connection contract, points the management test console at those same endpoints, deletes unreachable call-integration code, replaces the hand-written GPT-Live protocol with the official OpenAI SDK, makes Terraform one definition with environment data only in backend and variable files, shortens the test suite without weakening coverage, adds optional Spec Kit hooks for GitHub, and updates documentation and diagrams to match. Breaking changes are acceptable (nothing is deployed); real-environment verification is deferred to a follow-up. Details and measured baselines are in [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.12 (backend, management console, tooling); Terraform 1.17.0 (azurerm 5.11.0, azuread 3.10.0)

**Primary Dependencies**: FastAPI, OpenAI SDK 3.31.0 (`live` client delegation), azure-identity 1.26.0, azure-cosmos 4.17.1, OpenTelemetry, Reflex 0.10.0 and `websockets` 16.1.1 (management), `uv` locks per project; `openai-agents` retained without its `[realtime]` extra

**Storage**: Azure Cosmos DB (existing containers plus one new container for text conversation history, partitioned by owner key, item-level expiry of 30 days)

**Testing**: pytest per project (859 backend, 719 management including 81 browser cases, 368 tools), browser tests for the console, native CI workflows

**Target Platform**: Azure-hosted Linux containers; local development on Windows with PowerShell

**Project Type**: Multi-service web application (backend service, management console, tooling, Terraform)

**Performance Goals**: No runtime performance change. Cleanup targets are advisory: about 25 percent less backend source, no module over 600 lines without a recorded exception, about 50 percent shorter default test run, a quick tier near 3 minutes

**Constraints**: GPT-Live client delegation only (no Azure Voice Live, no Responses delegation, no public-OpenAI or legacy-realtime fallback); mirror-only locked dependencies; no real cloud activity; no GitHub Actions changes; hooks require user confirmation; stored text history is owner-only, bounded, expires 30 days after the last turn, stays out of logs and every other store, and voice transcripts stay volatile

**Scale/Scope**: About 12,100 backend lines in 46 modules (15 unreachable, 3,226 lines); three Python projects; one Terraform module with two roots

All items resolved; no unresolved clarifications remain.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Assessment | Status |
| --- | --- | --- |
| I. Azure-First Platform | Uses official Microsoft Azure libraries and Azure-hosted GPT-Live; no non-Azure dependency added | Pass |
| II. Security-First Engineering | Backend keeps independent authentication; gateway boundary retained; TLS and redirect controls verified or retained when swapping to the SDK; no secrets in hook or example files; stored text history needs a recorded threat assessment, owner-only access, encryption, managed-identity access and log exclusion | Pass, verification and threat-assessment tasks tracked |
| III. Documentation as a Deliverable | Module docstrings, decision comments, updated contracts and Mermaid diagrams, superseded 002 docs marked, contributor hook docs, all in the same change | Pass |
| IV. Bounded Microservices | The backend owns the history container and management never reads it. Service boundaries unchanged. Contract break is justified by no deployments or consumers; the migration plan is: console updated in the same change, retired paths verified not-found, 002 contract docs marked superseded | Pass with recorded plan |
| V. Python Backend and Reflex Frontends | No language or framework change; lint, type check and tests stay required | Pass |
| VI. Scalable Real-Time Call Handling | Removing call integration retires call-specific routing, drain and handover. Remaining voice sessions keep bounded admission, bounded queues and frames, timeouts, confirmed closure before capacity release, and documented shutdown and reconnect recovery. Capacity evidence for call handling is deferred with the call design | Pass, documentation must state remaining behavior |
| VII. Structured Logging and Monitoring | Both endpoints emit the shared telemetry names; alert and runbook content for removed call and handover signals is removed with the code, and monitoring of active sessions and dependency failures remains; no conversation content enters logs, metrics or traces; the history store has a readiness check, a content-free telemetry event and documented alert thresholds | Pass |
| VIII. Terraform Infrastructure as Code | Stays Terraform; version pins, validation, plan review and protected apply unchanged; no state or plan artifacts committed; no apply in this feature; the new history container is added through the normal plan review | Pass |
| IX. Automated Tests | Every retained behavior keeps a test; removed tests must name the removed behavior or the covering test; no test skipped or weakened to speed the run; the text-content privacy test is rewritten (not removed) to allow content only in the history container; regression tests prove retired paths are gone | Pass |

**Post-design re-check**: Design artifacts introduce no new violation. The items needing explicit follow-through are Principle II (SDK transport controls and the threat assessment for stored text history) and Principle VI (documenting remaining session behavior); both are inputs to tasks, not complexity exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/003-codebase-simplification/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── conversation-endpoints.md
│   ├── management-connection.md
│   ├── environment-files.md
│   └── github-hooks.md
├── checklists/
│   └── requirements.md
└── tasks.md              # created later by /speckit-tasks
```

### Source Code (repository root)

```text
code/
├── backend/
│   ├── src/caller_runtime/
│   │   ├── main.py                 # routes reduced to health + two conversation endpoints
│   │   ├── composition.py          # wiring after dead-code removal
│   │   ├── domain/ports.py         # extended: CredentialPort, ContainerPort, SessionConflict relocated here
│   │   ├── api/                    # text and voice endpoints replace test_sessions.py and call-era routes
│   │   ├── adapters/gpt_live.py    # rewritten on the OpenAI SDK live client
│   │   ├── adapters/foundry_text.py
│   │   ├── adapters/text_history_repository.py  # new: Cosmos store for text conversation history
│   │   ├── domain/text_history.py               # new: history model, owner key, size bound
│   │   ├── services/               # conversation.py; test admission renamed to connection admission
│   │   └── (removed) media, provider_webhooks, operations, capacity*, rollout*, drain, handover, owner_routing, routing, audit*, transfer_context, azure_realtime
│   └── tests/
├── management/
│   ├── management_app/
│   │   ├── adapters/test_session_client.py   # thin client on one connection-request builder
│   │   ├── api/test_sessions.py              # console gateway routes, renamed
│   │   ├── components/live_audio.py
│   │   └── models/test_session.py
│   └── tests/                      # duplicates merged; shared compiled-app fixture; quick and full tiers
├── tools/
│   ├── scripts/                    # terraform_delivery.py, verify_terraform.py simplified; drain_rollout.py and kubernetes_rollout.py removed; docstring and reachability checks added
│   └── tests/
└── infra/terraform/
    ├── bootstrap/                  # no environment-name logic or hard-coded state key
    ├── environments/               # same
    ├── modules/platform/           # retirement.tf reviewed; removed if only call-related; services.tf gains the text-conversations container
    └── config/                     # dev, test, prod synthetic example pairs

.github/workflows/                  # terraform-* reusable workflows updated for file-driven inputs (no new triggers)
.specify/
├── extensions.yml                  # new: optional prompted hooks
└── extensions/caller-github/       # new: local push and pull-request command
docs/                               # architecture, contracts, setup, runbook, contributor hook page, superseded markers
```

**Structure Decision**: Keep the existing three-project layout (backend, management, tools) and the single Terraform tree. Changes are deletions, renames and rewrites inside existing directories, plus an extended `domain/ports.py` and the new hook files under `.specify/`.

## Delivery Order

1. Baseline records and automated checks (reachability, docstring) first, so every later step is measurable.
2. Relocate shared types to `domain/ports.py`, then delete unreachable call-era code, tests, docs and scripts.
3. Introduce the two conversation endpoints with the shared contract and the Cosmos-backed text history; retire the test-session route and subprotocol.
4. Rewrite the GPT-Live adapter on the OpenAI SDK; drop the unused extra.
5. Re-point the management console through the single connection-request builder.
6. Terraform consolidation, helper and workflow updates, three examples.
7. Test reduction and tiers, measured against baselines.
8. Hooks and contributor documentation.
9. Documentation, diagram and superseded-marker updates; final full validation.

## Complexity Tracking

No constitution violations require justification. The Principle IV compatibility plan and Principle VI documentation obligation are recorded in the Constitution Check above.

## Final names (as built)

- Backend: `POST /v1/conversations/text`, `WS /v1/conversations/voice` (subprotocol `caller.voice.v1`), `/healthz`, `/readyz`; `api/connection.py`, `api/text.py`, `api/voice.py`; `ConversationRuntime` and `RuntimeOptions` in `composition.py` (`create_app(runtime=...)`).
- Containers: `call-sessions` (admission authority item `browser-test-authority-v1`) and new `text-conversations` (partition `/ownerKey`, item `ttl` 2592000).
- Management: `/v1/console/tickets`, `/v1/console/sessions`, subprotocol `caller.console.v1`; `adapters/backend_connection.py`, `text_relay.py`, `relay_frames.py`, `backend_errors.py`.
- Terraform: optional `label` variable instead of `environment`; state key in each backend file.
