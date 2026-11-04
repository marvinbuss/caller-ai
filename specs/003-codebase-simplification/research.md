# Research: Codebase Simplification

Feature: `003-codebase-simplification`. Input: [spec.md](spec.md) (clarified 2026-11-03).

All Technical Context items are resolved. Facts below were measured on the working tree (backend 12,099 lines in 46 modules; tests 859 backend / 719 management / 368 tools; Terraform 1.17.0; openai 3.31.0; openai-agents 0.24.0; azure-identity 1.26.0).

## Baseline Measurements

| Measure | Value | Source |
| --- | --- | --- |
| Backend production source | 12,099 lines, 46 modules | line count of `code/backend/src/caller_runtime` |
| Modules unreachable from `main.py`/`composition.py` | 15 modules, 3,226 lines | AST import-reachability scan |
| Collected tests | backend 859, management 719 (81 browser cases), tools 368 | `pytest --collect-only` |
| CI native test step | about 195-222 s | run 41672927903 |
| CI documentation render step | 142-305 s | run 41672927903 |
| CI image audit step | about 167 s | run 41672927903 |

### Local suite baseline (Windows developer machine, 2026-11-03, before any change)

| Project | Result | Wall time | Slowest items |
| --- | --- | --- | --- |
| backend | 855 passed, 4 failed | 64.8 s (68.7 s with startup) | SDK lifecycle and startup tests 5-7 s each |
| management | 715 passed, 4 failed | 537.7 s | `test_owned_manager_relay_enforces_stable_actor_and_five_occupied_slots` 171 s; Reflex build in browser setup 84.5 s and 38.6 s; two-minute-class browser cases 7-10 s each |
| tools | 368 passed | 90.9 s | `test_failure_gate_detects_and_restores_real_health_assertion` 41.9 s; two multimodal alert observation cases 12 s each |

The four failures in `tests/integration/test_foundation_recovery.py` appear identically in backend and management runs and are environmental: each spawns `uv run --locked`, and the local uv cache cannot build the project offline (`registry packages may only be read from the cache`). They are recorded as baseline failures, not caused by this feature, and are not changed.

### Code volume baseline (production Python)

| Scope | Files | Lines | Files over 600 lines |
| --- | --- | --- | --- |
| `code/backend/src/caller_runtime` | 46 | 13,186 | 6 |
| `code/management/management_app` | 38 | 11,079 | 4 |
| `code/tools/scripts` (before `check_modules.py`) | 17 | about 5,620 | 2 |

Largest files: `state/configuration_editor.py` 1,301; `services/conversation.py` 1,273; `verify_multimodal.py` 1,255; `services/test_admission.py` 1,138; `api/test_sessions.py` 940; `components/live_audio.py` 877; `security.py` (management) 867; `adapters/test_session_client.py` 863; `adapters/capacity_repository.py` 699; `adapters/session_repository.py` 681. Modules without a purpose docstring: 0 (the check found none). The earlier 12,099-line figure counted the backend differently; use the table above for the before/after comparison.

### Measurements after the backend conversation endpoints (User Story 1)

| Measure | Before | After | Note |
| --- | --- | --- | --- |
| Backend production source | 46 files, 13,186 lines | 29 files, 8,993 lines | includes the new `api/connection.py`, `api/text.py`, `domain/text_history.py`, `adapters/text_history_repository.py`, `services/text_conversation.py` |
| Unreachable production modules (`check_modules.py`) | 15 modules, 3,226 lines | 0 | report mode, zero findings and zero missing purpose docstrings |
| Backend routes | health, readiness, test sessions, calls media, provider callbacks, drain, dev transport | `/healthz`, `/readyz`, `/v1/conversations/text`, `/v1/conversations/voice` | enforced by `tests/contract/test_route_inventory.py`; the served OpenAPI document is disabled |
| Backend tests | 859 collected | 510 collected (506 pass, the same 4 environmental `test_foundation_recovery.py` failures as the baseline) | call-era cases were deleted; endpoint, history, route and connection cases were added |
| Backend suite wall time | 64.8 s | about 37 s | Windows developer machine |

### Management after the console moved to the public endpoints (User Story 2)

Full management run: 731 passed, 1 failed, 11 min 44 s. The failure was `test_owned_manager_relay_enforces_stable_actor_and_five_occupied_slots`, which passed on its own twice (143 s and 220 s); it builds a real frontend and a runtime subprocess and is timing-sensitive under full-suite load, so it is recorded as load-sensitive, not a behavior change. No file under `code/management/` references a test-only backend endpoint (`internal/test-sessions`, `caller.test.v1`, `dev/transport`, callbacks, drain); the one remaining `/v1/unreviewed-test-tickets` string is a negative route case.

### Terraform decisions (User Story 4)

- Both stacks lose the `environment` variable; an optional free-form `label` is used only in tags. The bootstrap output no longer carries a platform state key; each stack's backend file carries its own.
- `call-sessions` stays: it holds the admission authority item (`browser-test-authority-v1`) that the retained admission service reads and writes. `text-conversations` is new and runtime-owned.
- `call-audit` and its TTL variable stay for now: the backend no longer writes it, but the container is protected by `prevent_destroy` and removing it is a destructive change that needs its own reviewed plan against a real environment. Recorded as a follow-up, not silently dropped.
- `retirement.tf` stays: it only outputs review-only admission definitions and creates nothing; removing it belongs with the drain tooling follow-up.

These are the recorded "before" values for SC-004, SC-007 and SC-009. Targets stay advisory (clarification Q2): a missed target needs a recorded reason, not a failed delivery.

### Final code volume (T058)

| Scope | Files | Lines | Files over 600 lines |
| --- | --- | --- | --- |
| `code/backend/src/caller_runtime` | 32 (before 46) | 9,064 (before 13,186) | 4 (before 6) |
| `code/management/management_app` | 46 (before 38) | 11,668 (before 11,079) | 4 (before 4) |
| `code/tools/scripts` | 16 (before 17) | 5,114 (before about 5,620) | 2 (before 2) |

`check_modules.py` reports zero unreachable modules (one allow-listed operator adapter) and zero
modules without a purpose docstring. Management grew because it gained the shared connection
builder, text relay, relay frame accounting and the editor/reflex/live-audio splits, while its
test-only backend path was removed; the line count there is not a reduction target met. Reasons
for each module still above 600 lines:

| Module | Lines | Reason |
| --- | --- | --- |
| `services/conversation.py` | 962 | One executor owns tool dispatch, delegation and ordered close; the policy, speech segments and cleanup already moved out, and the remaining parts change together |
| `services/admission.py` | 927 | The single writer of the authority document: every mutation shares the CAS, retry and fencing rules |
| `api/voice.py` | 900 | One endpoint and its WebSocket state machine (authenticate, frames, credits, close) |
| `components/live_audio_js.py` | 859 | Browser JavaScript held as strings; no Python logic to split |
| `state/configuration_editor.py` | 836 | One Reflex state class (events and computed vars); a state class cannot be split across modules without changing its registered name |
| `security.py` (management) | 678 | Token verification, sessions and permit signing share one policy object |
| `adapters/gpt_live.py` | 668 | Protocol validator, adapter and session are one wire contract |
| `adapters/test_session_client.py` | 606 | The voice bridge has no further natural seam |
| `verify_multimodal.py` | 1,255 | Qualification driver, not touched by this feature |
| `terraform_delivery.py` | 617 | One delivery state machine |

The editor frontend export failed once after the `configuration_editor` split because the state
module was imported inside the forked registration context; importing the editor pages and states
before the fork (as the old composition import did) restored it. Keep that ordering.

### Final suite timings (T078)

Measured on the same Windows developer machine on 2026-11-04 with nothing else running.

| Project | Before | After | Note |
| --- | --- | --- | --- |
| backend | 855 passed, 4 failed, 64.8 s | 508 passed, about 65 to 75 s (quick tier 439 passed, 36.4 s); the same 4 environmental failures | Fewer tests did not make the run faster: the SDK lifecycle, owned-TLS and port cases dominate it, and this run was slower than the baseline run on the same machine. The duplicate SDK startup case was removed (T071) and three race tests were added (T096), so the count went from 510 to 508. With `pytest-xdist` the same tests took about 40 s, but the lock for it could not be produced here (see `docs/library-decisions.md`) |
| management | 715 passed, 4 failed, 537.7 s | 733 passed, 480.4 s (8 min; unit and contract tiers 554 passed, about 100 s) | More cases (backend connection builder, text relay, relay frames) in less time. The editor browser module already shares one host per module, and each host builds its own frontend; a session-wide shared build gave no further measurable saving (506 s against 503 s, see T072). The time is the 81 real browser cases, each driving Chromium, so the advisory target is not met. One run showed a single failure in `test_console_synthetic_speech_pcm_mute_interrupt_and_confirmed_stop[chromium-controls-768]`; it passed alone, in a 24-case group and in the next full run, so it is recorded as a timing flake |
| tools | 368 passed, 90.9 s | 325 passed, 71 to 88 s | Call-era tooling cases were removed. Three real child-process runs (the failure gate at 37 s and two service-alert contract cases at 11 to 13 s) account for about two thirds of the time (T074) |

CI step durations of two green pull-request runs against the baseline run 41672927903: run
41803375451 (commit `66caffc`, before CI caching) and run 41827283123 (commit `f20c377`, with
dependency caching, third attempt):

| Step | Baseline | `66caffc` Ubuntu / Windows | `f20c377` Ubuntu / Windows |
| --- | --- | --- | --- |
| Native tests (`ci.py test`) | about 195-222 s | 221 s / 266 s | 218 s / 303 s |
| Documentation render | 142-305 s | 142 s / 343 s | 151 s / 410 s |
| Image audit | about 167 s | 133 s / not run | 144 s / not run |

CI shows no reduction. The native test step is at the top of the baseline range on Ubuntu and above
it on Windows, and the Windows documentation render is above the baseline range in both runs. The
image audit is faster than the baseline. The dependency caching did not shorten the measured steps
(they are test and render time, not installs), so the advisory targets for test time stay unmet and
the reasons are above.

## Decision 1: Two public conversation endpoints, one connection contract

- **Decision**: Replace the internal test-session WebSocket and all call-era routes with exactly two conversation endpoints: `POST /v1/conversations/text` (one text turn) and `WS /v1/conversations/voice` (voice session). Both use one connection contract: the signed manager start permit as the credential (verified independently by the backend, so verified user context, revision binding and admission deadlines are unchanged), admission and limits, a stable error vocabulary, and an optional `settings` object that MUST be absent or empty and is rejected otherwise (clarification Q4). `/healthz` and `/readyz` remain operational probes, not conversation endpoints.
- **Rationale**: The existing test route already authenticates, admits and handles text plus speech on one socket, so the working behavior is kept and the surface is renamed and narrowed. A text turn is naturally request/response; the response is streamed as server-sent events so the incremental `text.delta` and `text.completed` display is kept; voice needs a duplex socket. Each text request takes one admission slot, runs, and releases the slot after verified cleanup, so a text test is one in-flight turn per manager. The backend stores each text conversation's history in Cosmos DB (Decision 9), so a request carries only a conversation identifier. This changes the feature 002 rule that conversation content is never persisted, by owner decision on 2026-11-03; voice stays volatile. Production clients (management console now, future client app later) and the test console then share exactly one path (FR-001 to FR-004, FR-007, FR-008).
- **Alternatives rejected**: Keep one WebSocket for both modalities (violates the "one endpoint for text, one for voice" shape and forces text callers onto WebSocket); keep `/v1/internal/test-sessions` as an alias (violates "no different endpoints for the same purpose"); add per-environment routes (no need).
- **Removal set**: `/v1/calls/{id}/media`, `/v1/provider/callbacks`, `/v1/operations/drain/*`, `/v1/internal/test-sessions`, and `/v1/dev/transport` (local-only transport simulation; superseded by the voice endpoint, which can be driven locally). Metrics and observability route lists are updated to the new route set.
- **Subprotocol**: `caller.test.v1` is retired with the path; the voice endpoint uses `caller.voice.v1`. The name is generic because the endpoint is no longer test-only.

## Decision 2: Delete unreachable call-era code, relocate shared types first

- **Decision**: Delete the 15 unreachable modules (media, provider webhooks, operations, capacity and its repository, rollout and its repository, drain, handover, owner routing, routing, audit, transfer context) with their tests, docs and tool scripts (`drain_rollout.py`, `kubernetes_rollout.py`) once nothing live imports them. Because `CredentialPort` (in `adapters/azure_realtime.py`) and `ContainerPort` and `SessionConflict` (in `adapters/session_repository.py`) are imported by live modules (`composition.py`, `capabilities.py`, `capability_client.py`, `configuration_client.py`, `test_admission_repository.py`), those shared types are first moved to small neutral modules (`ports.py`) and imports updated, then the call-only remainder is removed.
- **Rationale**: The ordering prevents a half-deleted import graph. A reachability scan (re-run as an automated check) proves no live module depends on deleted code (FR-002, FR-010, SC-001, SC-011). Call-only metrics and events (handover counters, drain route handling, owner-routing events) are removed with the code and their tests.
- **Alternatives rejected**: Keeping dead modules "for later" (explicitly out of scope; call integration returns as a new design); deleting by file name pattern without a reachability proof (risks live breakage).

## Decision 3: Replace the hand-written GPT-Live protocol with the OpenAI SDK

- **Decision**: Use `AsyncOpenAI(...).live.connect()` (present in openai 3.31.0, typed events and client-delegation support) with a bearer token provider from `azure.identity.get_bearer_token_provider`, replacing the hand-written protocol and session classes in `adapters/gpt_live.py`. The SDK accepts `websocket_connection_options` (`max_size`, `max_queue`, `write_limit`, `compression`, `subprotocols`), so the retained 256 KiB frame bound and queue bounds remain expressible. Timeouts and the closure-confirmation repair stay as thin local logic around the SDK session, because the SDK offers no equivalent. Drop the unused `openai-agents[realtime]` extra (used only by two tests) and delete `adapters/azure_realtime.py` after relocating `CredentialPort`.
- **Rationale**: Satisfies FR-012 and FR-013 with less code and fewer protocol defects. Feature 002 provider constraints are preserved verbatim: client delegation only, no Azure Voice Live, no Responses delegation, no public-OpenAI or legacy-realtime fallback.
- **Verification step (in tasks)**: confirm the SDK path enforces TLS verification and redirect refusal equal to the current adapter; if any control is not available, keep that control locally and record the reason in the Library Decision Log (FR-012).
- **Alternatives rejected**: Keeping the hand-written websockets protocol (the main avoidable complexity); the `openai-agents` realtime runner (different delegation model, forbidden by feature 002 constraints).
- **Probe result (T044) and as built (T045)**: a local `websockets` server on 127.0.0.1 confirmed that `live.connect()` requests `/openai/v1/live/sessions` with `Authorization: Bearer <token>`, that `max_size` closes the connection on a frame above 256 KiB, and that a cross-origin redirect is refused with `SecurityError` (the SDK allows same-origin redirects only; the token never leaves the origin). TLS verification uses the `ssl` context the adapter passes in `websocket_connection_options`; it cannot be exercised without a certificate, so it is covered by the unit test that checks `check_hostname`. The SDK offers no schema validation, closure receipts or token-expiry check, so `GptLiveProtocol` stays as a validator over raw frames (`recv_bytes`) and the adapter reads the Azure token itself instead of `get_bearer_token_provider` to prove it outlives the session boundary. `azure_realtime.py` was already gone; the `openai-agents[realtime]` extra is dropped and `openai>=3.31,<4` and `websockets>=15,<17` are declared directly (the SDK `realtime` extra pins `websockets<16`, which would downgrade the locked 16.1.1). The resolution ran through the approved mirror; only the changed `caller-runtime` and `openai-agents` entries were merged into `uv.lock` so every source URL stays on pypi.org.

## Decision 4: Management console stays a gateway but uses the public contract

- **Decision**: Browser to management WebSocket to backend (clarification Q1). `TestSessionClient` becomes a thin client of the two public endpoints, built through one connection-request builder that composes token, headers and the empty `settings` extension point. The backend continues to authenticate independently; the browser never reaches the backend directly. The management `/v1/test-tickets` and `/v1/test-sessions` APIs are renamed to reflect that they are the console's own gateway, not a separate backend contract.
- **Rationale**: Keeps the existing security boundary and removes the test-only backend path (FR-005, FR-006, FR-009). The settings extension point is where per-connection settings attach later without a second path.
- **Alternatives rejected**: Direct browser to backend (changes the credential boundary, rejected in clarification Q1); a separate test client library (a second path).

## Decision 5: One Terraform definition, environment data only in files

- **Decision**: Keep one root per stack (`bootstrap`, `environments`) and one module. Environment differences come only from the backend-config file and the variable file. Verified against the pinned Terraform 1.17.0 in a throwaway local-backend directory: both HCL (`.tfbackend`, `.tfvars`) and JSON (`.tfbackend.json`, `.tfvars.json`) are accepted natively. The existing JSON examples therefore stay valid; the feature changes what they contain and how they are used.
  - Remove the `environment` name validation regex, the hard-coded `"${var.environment}/platform.tfstate"` key in bootstrap, the `environment` output, and the `environment` tag requirement as a logic input; replace with an optional free-form label variable used only for naming and tags if a file supplies it.
  - Add synthetic `dev`, `test`, `prod` examples that differ only in data.
  - `terraform_delivery.py` stops deriving `f"{environment}/{stack}.tfstate"` and `f"{environment}-terraform-{stack}-{operation}"`; it reads the key and the protected deployment environment name from the backend/variable files and workflow inputs.
- **Rationale**: FR-016 to FR-019. Environment names stop being branching logic and no list of stage names exists anywhere (not in Terraform, workflows or a data file); adding a fourth environment means adding two files. Each stack's backend file (`bootstrap` and `platform`) carries its own state key, and bootstrap holds no key for the platform stack.
- **Alternatives rejected**: Per-environment directories (duplication); workspaces (hidden state coupling and not the stated "backend file plus tfvars" model); changing file formats (no benefit, formats already native).
- **Principle VIII**: Changes still run through plan review and protected apply; no real apply happens in this feature (real-environment testing deferred).

## Decision 6: Faster tests without weaker evidence

- **Decision**: In order of expected saving: (a) delete tests that only cover removed call-era code (largest, cheapest); (b) merge duplicate cases and cut the 81 management browser cases that restate the same behavior; (c) share expensive setup (one compiled Reflex build and one browser session per module/session instead of per test); (d) split a quick tier (unit plus contract) from the full tier (adds browser and integration) with pytest markers; (e) adopt parallel execution only if the parallel plugin is present in the approved mirror and lock, otherwise skip with a recorded reason. Opt-in live runs stay out of the default path.
- **Rationale**: FR-020 to FR-022 and Principle IX (tests for content privacy, admission and one active test per manager, limits and cleanup are never deleted or weakened; the text-content privacy case is rewritten, not removed, to allow content only in the history container; only cases whose subject is a removed feature go; the default suites keep network access blocked except localhost, which satisfies FR-027 and SC-012): every retained behavior keeps a test; a removed test must name the removed behavior or the test that still covers it.
- **Alternatives rejected**: Skipping or marking tests expected-fail to hit a time target; loosening assertions.

## Decision 7: GitHub hooks through Spec Kit extensions, not Actions

- **Decision**: Add a committed `.specify/extensions.yml` registering optional, prompted hooks (clarification Q3; Spec Kit-only, user confirmation required). Use the bundled `git` extension for the feature branch before specify and for commits after specify/plan/tasks/implement, and the bundled `github` extension for tasks-to-issues after tasks. Push and pull-request creation are not covered by a bundled extension, so a small repository-local extension adds one command that runs `gh` after explicit confirmation. Every hook is `optional: true` with a prompt; when `git`, `gh` or authentication is missing the hook reports the reason and skips without failing the workflow.
- **Rationale**: FR-023 to FR-025. Matches how Spec Kit loads hooks (`.specify/extensions.yml`), keeps remote actions explicit, and adds no CI trigger. `gh` 2.103.3 is installed and authenticated in this environment, but the hook must not assume it.
- **Alternatives rejected**: Git client hooks or GitHub Actions (out of scope per Q3); automatic pushes without confirmation (violates the confirmation requirement).

## Decision 8: Readability and documentation

- **Decision**: Every production module gets a one-line purpose docstring and a comment for each non-obvious decision (what is intended and why, not what the line does). An automated check (added to the tools tests) fails when a production module lacks a purpose docstring (SC-005). Modules over 600 lines are split where a natural seam exists; the limit is advisory (Q2) and exceptions are recorded. Architecture, contract, setup and runbook docs and their Mermaid diagrams are updated; feature 002 contract docs for retired endpoints are marked superseded rather than deleted (Q5); zero-obsolete-reference applies to active code and current docs only, and regression tests may name retired endpoints to prove they are gone.
- **Rationale**: FR-010, FR-011, FR-014 and FR-026, plus Principle III (diagrams stay accurate). SC-005 is met by the automated purpose-statement check alone; there is no manual review sample.

## Decision 9: Text conversation history in Cosmos DB

- **Decision**: Store text conversation history in one new Cosmos container (`text-conversations`) owned by the backend, one item per conversation. Partition key `/ownerKey`, the existing actor key hash of tenant and manager from the start permit. The item id is a server-assigned opaque conversation identifier returned in the first stream event. Each item carries the turn list (text only), `createdAt`, `updatedAt` and the ETag. Each write sets an item `ttl` of 2,592,000 seconds (30 days after the last turn); the container default expiry is set to `-1` so item expiry is enabled and the retention value lives in backend settings. A completed turn appends the user and assistant messages with an ETag-conditioned replace, a failed turn writes nothing, an agent change resets the stored context as today, and the total stays within the existing 256 KiB context limit. Access uses the backend workload managed identity with data-plane RBAC (local key auth stays off) and Cosmos encryption at rest. Unknown, expired and foreign identifiers return the same `invalid_request` with reason `conversation_not_found`. If the store is unavailable the request fails closed with `upstream_unavailable` reason `history_unavailable`; a cached background check (a sentinel read about every 10 seconds) makes `/readyz` report not ready with the fixed reason `history_store_unavailable` when the result is bad or older than 30 seconds, and a content-free `history.store` event records outcome and latency. The factory that supplies containers to composition takes a container name so the admission authority and history containers share one mechanism.
- **Rationale**: HTTP text requests can land on different backend instances, so history cannot live in one instance's memory without sticky routing, which this cleanup removes. Owner-only partitioning keeps the data per user. Voice keeps its current volatile handling to limit the privacy change.
- **Constitution**: Principle II needs a threat assessment and log exclusion (docs and tests); Principle IV is met because only the backend reads the container; Principle VIII applies through the normal plan review; Principle IX applies through rewritten privacy tests.
- **Alternatives rejected**: caller-supplied history (the owner chose server-side storage); in-memory history with sticky routing (brings back routing complexity); text over a WebSocket session (rejected by the owner); storing voice transcripts (larger privacy change, out of scope); an explicit delete operation (a third endpoint operation, deferred; expiry only for now).

## Library Decision Log (initial entries)

| Area | Current | Decision | Reason |
| --- | --- | --- | --- |
| GPT-Live transport | hand-written websockets protocol | OpenAI SDK `live.connect()` | official, typed, bounded via connection options |
| Azure credentials | azure-identity already used | keep; use `get_bearer_token_provider` | official Microsoft library |
| Cosmos access | azure-cosmos | keep | official Microsoft library |
| Agent runtime extra | `openai-agents[realtime]` | drop the extra | unused outside two tests |
| Management upstream socket | `websockets` | keep unless the shared connection builder makes it redundant | needed for the gateway relay; no official equivalent |
| Telemetry | OpenTelemetry | keep | standard library for the purpose |

## Open Items

None blocking. Items to verify during implementation are stated as verification steps inside the decisions above (SDK TLS and redirect controls; parallel plugin availability).

## Implemented and deferred

Implemented and verified against controlled doubles: the two backend conversation endpoints with
one connection contract (`api/connection.py`), server-side text history with expiry, readiness
reason and telemetry, deletion of the call-era routes and modules (`check_modules.py --strict`
reports zero findings), the management console on the public endpoints with a single request
builder, per-turn permits and a retention notice, one Terraform definition per stack with
stage-neutral delivery tooling and examples, the `quick` test tier, the optional Spec Kit hooks,
GPT-Live on the OpenAI SDK, the file splits, the duplicate-helper sweep (strict JSON parsing, the
authority encoder and the management problem response), the wording pass (a multi-line docstring
and why-comments in every module of the backend, management app and tools scripts), the library
decision log and the refreshed documents (68 documents and 46 Mermaid diagrams verified by
`verify_docs.py`).

Test speed-up decisions (T071 to T076):

- **T071, duplicate coverage**: the paired backend conversation test files were reviewed per
  behavior. Only the SDK startup case was a true duplicate: `tests/unit/test_sdk_startup.py` was
  removed and `tests/integration/test_sdk_lifecycle.py` covers both of its cases (it gained the
  environment-variable and restart assertions). The conversation unit and integration pairs test
  different layers and stay.
- **T072, shared browser build**: tried and reverted. A session-wide compiled frontend on fixed
  ports ran the full management suite in 506 s on Windows, against 503 s for the per-host build
  that was restored, so it gave no measurable saving. On the Ubuntu CI run (run 41817409529) it
  also made all 24 speech console cases fail: the first stalled after the interrupt step and the
  rest found the manager slot busy. The cause could not be reproduced without a Linux
  environment. A later attempt needs a Linux reproduction first and a measured saving to justify it.
- **T073, merged browser cases**: all 81 browser cases were reviewed and none merged. They cover
  distinct behaviors, and the width parametrization is the required responsive evidence.
- **T074, tools fixtures**: profiling showed the time is three real child-process runs, not
  repeated setup, so no fixture change was made.
- **T075, `pytest-xdist`**: not adopted. It is on the approved mirror, but its lock needs public
  artifact URLs that could not be produced here; see `docs/library-decisions.md`.
- **T076, CI**: job names stay as they are (they are the required check names) and dependency
  caching was added. The saving is not measured until a CI run exists for this branch, and
  caching the Mermaid renderer or Chromium needs a pinned `actions/cache` reference.
- **Intermittent browser failures**: one Windows CI run (run 41807919799) failed
  `test_responsive_keyboard_editing_and_protected_roles[chromium-1440]` with a page error
  `Cannot read properties of null (reading 'name')`, and one local full run failed a speech console
  case that passed on every rerun. Six repeated local runs of the 1440 case passed, and that case
  passed in every other CI run, so no cause was found and no code was changed. If the page error
  returns, capture the page's console output and the Reflex event that was in flight.
- **Intermittent CI failures on `f20c377`** (run 41827283123): the same commit failed twice on
  Windows and passed on the third attempt, with Ubuntu green throughout. The first attempt failed
  the 24 `test_console_synthetic_speech_pcm_mute_interrupt_and_confirmed_stop` cases: the first
  ended in `cleanup pending` instead of `ended`, and the manager slot stayed held, so the other 23
  found it `busy`. The second attempt failed
  `tests/integration/test_conversation.py::test_owned_socket_rejects_expired_session_even_for_heartbeat`
  with close code 4502 instead of 4401. Neither reproduced locally (the speech cases passed pinned
  to two cores; the backend test passed 70 times pinned to one core, 30 of them with busy-loop processes on it), and the code involved is
  unchanged from `main`. Two leads, not confirmed: the closure receipt is recorded in
  `CleanupObservations` before `settle_attempt` stores it on the slot, so a failed settle makes
  `observe()` report a mismatch and hold the slot; and `api/voice.py` re-raises the first finished
  task from an unordered set, so two tasks failing in one tick can pick either error.
  Both leads were hardened without a reproduction, each with a test that fails before the change
  and passes after it: `GptLiveSession` now keeps the provider's close receipt when the first
  settlement fails and `close()` retries the settlement up to three times within its deadline
  (`test_live_close_retries_a_failed_closure_settlement_until_cleanup_verifies`), and the voice
  endpoint re-raises finished tasks in a fixed order, the client's own refusal first, then
  authority loss, then the rest (`tests/unit/test_voice_task_order.py`). Whether these were the
  actual causes of the two CI failures is not confirmed; if either failure returns, the leads were
  wrong and the CI log is the next evidence.
- **Third Windows failure and test isolation** (run 41847352921, a docs-only commit): the text
  case `test_console_published_text_uses_owned_runtime_without_microphone` timed out waiting for
  the reply (5 s default), and every later session-starting case then found the manager slot
  `busy`. Two test changes follow from that (T098, T099): browser expectations wait 15 s when
  `CI` is set (5 s locally), and each speech and text console case runs under its own manager
  identity, so one stuck session fails only its own case. The runtime-wide `occupied == 0`
  assertion in the text case is unchanged, so a stuck session can still trip it. Repeated local
  runs of the text and speech cases (17 runs of 5 tests) failed twice on the first speech case: once
  with `stopping` still shown after 5 s, once with capture `Off` where `Muted` was expected because
  the session ended about half a second after it started. The early end is the console's
  fail-closed mute guard: `mute()` in `live_audio_js.py` stops the session as `unavailable` when
  any audio is still queued on the socket, because queued frames cannot be recalled. It is not
  the clock watch that was suspected first. The guard is deliberate and was left as it is; the
  speech case now presses Mute only in a task that sees an empty send queue (T101).
- **Fourth Windows failure and the stop fix** (run 41862400575): with the isolation from T099 only
  one case failed (737 passed, 1 failed), `[chromium-hidden-768]`: the console stayed `stopping`
  for 10 s and then showed `cleanup pending`. `stop()` skipped the stop command when more than
  8 KB of audio was queued on the socket, so the server was never asked and never confirmed.
  `stop()` now sends the command at once (`test_browser_stop_reaches_the_server_even_when_audio_is_queued`
  fails before the change). That this was the cause of the CI failure is not confirmed. A failed
  speech case now also prints the runtime's last lifecycle events from the host log (T100), so the
  next occurrence shows the server side.
- **Fifth Windows failure** (run 41877194314, commit `f260566`, with the stop fix): the text case
  `[chromium-overview]` never showed `Synthetic shared reply` within 15 s, and `[community]` then
  failed its runtime-wide `occupied == 0` check because the overview session stayed occupied
  (737 passed, 2 failed). Ubuntu and the image job were green. The text case now prints the same
  failure context as the speech case (console status and error plus the runtime's last work,
  latency, relay and cleanup events), so the next occurrence shows whether the turn failed or only
  stalled. No cause is known.
- **Relay contract test flake**: `test_owned_manager_relay_enforces_stable_actor_and_five_occupied_slots`
  failed in two of five full management runs on an otherwise idle workstation and passed alone
  (twice, 2 min 25 s) and in the next full run (733 passed in 8 min). It was already recorded as
  load-sensitive; no cause was found.

- **SC-008 decision (T097)**: the owner chose to record that the required-check time reduction is
  not met rather than cut browser cases. Local suites dropped from 693 s to about 645 s in total
  (management 537.7 s to 503.4 s, tools 90.9 s to about 75 s, backend 64.8 s to about 67 s), but
  CI shows none (native tests 218 s on Ubuntu and 303 s on Windows against 195-222 s). What was
  tried: a shared browser build (no gain), `pytest-xdist` (its lock needs public artifact URLs),
  merging browser cases (none merge) and fixture sharing in tools (the time is child processes).
  The remaining levers cut evidence: the responsive width matrix and the speech exit-event cases
  are required coverage (FR-021), so they stay.

Still open, each needing its own change:

- `call-audit` container and `retirement.tf` stay until a reviewed destructive plan exists.
- A shared editor frontend build (T072) is not worth retrying unless it first shows a measured saving.

