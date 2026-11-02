# Multimodal Agent Management Evidence

## Status and Scope

Feature `002-multimodal-agent-management` implements the Python/Reflex management graphs,
one configured application-owned Agents backend, Foundry GPT-Live client delegation, explicit
published-only browser text/speech tests and root documentation. This report is **local evidence,
not production/live acceptance**. The owner has authorized feature-only commit/push/PR
publication on the existing `feat/spec-002` branch for review, not merge, release, image
publication, deployment or actual qualification. The latest full local profile
passes 852 runtime, 719 management and 284 tools tests. The earlier responsive-shell setup
errors remain historical with their cause unconfirmed, as recorded below.
The dependency and external acceptance blockers remain. Feature-001 evidence and its
deferred tasks are not recertified or reopened.

Actual Foundry bindings/identity/quota/retention (T062), current stable physical browser/headset
qualification (T086), full-duration five-manager acceptance (T087), approved-sink delivery and
current native Windows/Linux CI must not be inferred from controlled ports or screenshots.
Those missing results block affected live acceptance. No operator authorization was supplied
for actual provider/device/store access, credential discovery, provisioning, deployment or spend.

The operator retained the approved-mirror-only dependency policy and requested a Git-ignored
local qualification-input template. That template has disabled execution gates, blank secret
inputs and no automatic application loading; it must never be published or logged. Filling
it supplies review inputs, not authority to execute tests. Exact existing resources, identity,
privacy/retention, time/cost and applicable device/sink reviews still require confirmation.

Source commit `49175e111da62d0834e483b0f7f2072cc372bf14` is published in
[draft PR #3](https://github.com/marvinbuss/caller-ai/pull/3). Exact-source
[CI run 41462727627](https://github.com/marvinbuss/caller-ai/actions/runs/41462727627)
completed **failed**. The staged/publication guards verified the exact reviewed feature set
and excluded all local inputs; no merge or deployment was performed.

## Native CI Source Checkpoint

Observed results apply to source commit `49175e111da62d0834e483b0f7f2072cc372bf14`:

- Windows job 125335617006 passed 852 runtime, 719 management and 284 tools tests, the
  assertion-failure gate and actual documentation rendering (53 documents, Chrome
  156.0.8078.48). Its dependency audit failed on minimatch 10.2.6 / GHSA-g3hv-75wg-m249.
- Ubuntu job 125335616653 passed 284 tools and 852 runtime tests, then management reported
  718 passed and one failure: `test_us1_community_graph_publication_navigation_and_presentation`
  at 768 pixels, line 651, expecting Unsaved after dragging but observing Saved. Later
  assertion/documentation/advisory steps did not run; they are not counted as passed.
- Linux image job 125335616483 passed the actual non-root/offline image smoke, including
  three browser widths. Its dependency audit failed on the same minimatch advisory; no
  passing image vulnerability-audit result is inferred from successful image smoke.

The exact 768-pixel journey passed once locally with an additional read-only drag-origin
viewport/hit-test probe (205.69 seconds). The cause remains unconfirmed. That temporary
probe was removed and the test file was verified identical to the published source; no
application change, assertion weakening, timeout increase or selective CI rerun was applied.
Keep T101/T114 and merge/release blocked. A later evidence-only commit does not imply a
new native-CI pass; this is a source-specific failed checkpoint, not acceptance.

The current frontend dependency advisory gate is **failed/blocked** by Moderate
`minimatch` 10.2.6 / GHSA-g3hv-75wg-m249. Its reported fixed release 11.0.1 is absent from
the approved mirror; the canonical registry was unreachable under normal TLS. No pin,
integrity, feed, TLS or advisory policy was bypassed. See [current security blocker](../security.md#feature-002-current-advisory-blocker).

## Latest Local Regression

The resumed feature-aware profile completed all three full test directories with actual JUnit
results: **852 runtime, 719 management and 284 tools tests**, each exit zero with no failures,
errors or skips. The original 900-second per-suite bound and full discovery are unchanged.
Its result remains `local_status=passed`, `live_status=pending`, `release_status=blocked`.

A single owned shell-readiness probe observed both services ready and the frontend serving
HTML within the original deadline. The three unchanged shell browser cases passed separately
(47.88 seconds), then passed again as part of the complete management suite. No smoke code,
timeout, assertion or fixture was changed; the prior 715-pass/3-setup-error result below is
preserved, and its original cause is not certified fixed by later passing observations.

T113 now has explicit local lifecycle binding: runtime `create_app(test_alert_sink=...)`
and reviewed `ManagementResources.test_alert_sink`, both defaulting to `None`. Two new
failing-then-passing lifecycle regressions verify one-item nonblocking enqueue, contained
queue-full errors, detached allowlisted metadata, shared correlation/trace context and no
post-shutdown callback. The existing qualification-tool contract now runs four local alert
checks per service, including these bindings. No default collector, endpoint, credential,
delivery worker or actual sink was created.

**103/114 tasks remain checked.** T113 stays incomplete until an existing approved sink
demonstrates actual notification delivery, accountable recipient acknowledgement and recovery.
The Moderate minimatch advisory and original/convergence provider, physical-device, endurance
and native-CI evidence gates also remain blocked. No controlled queue or full local pass is
upgraded to actual delivery, live acceptance or release approval.

Final scoped gates pass lint/format, complete runtime and management typing, whitespace,
offline secrets and the full private-origin guard. Reference validation passes 53 documents
with 42 unchanged diagram sources; no new rendering is claimed. The approved-feed recheck
again returned 404 for fixed minimatch 11.0.1, and its root metadata omitted that version.
No pin, lock, registry, TLS or advisory policy changed. Terraform checks were not applicable
to this application-only increment.

## Earlier Convergence Local Increment

T107-T109 are locally complete. Eight new Node cases include seven failing-then-passing
regressions and one already-passing stale-message control. They cover stale
socket close/error callbacks after an explicit replacement, capture flush identity and credits,
and active AudioContext suspension/interruption/closure. The complete console unit module
passes **56 tests**. Existing native-worklet assertions now also verify stale grants and flush
acknowledgements without weakening PCM, queue, credit, generation or deadline bounds.

The affected compiled Reflex text/speech journeys pass **11 tests** (225.84 seconds), using
both owned HTTPS/WSS apps and synthetic media at 360/768/1440. They verify delayed old socket
callbacks do not terminate a replacement, stale capture is discarded, audio continues after
authorized interruption and suspended/closed playback disposes media with a truthful error.
This is not physical-device or actual Foundry qualification.

The subsequent complete management suite collected **718 tests**: **715 passed, 3 setup
errors**, no skips, in 666.25 seconds. All 78 compiled graph/console cases passed. The three
unchanged responsive-shell cases failed in `smoke.running_apps` / `wait_ready` with
`smoke_readiness_deadline_exceeded` before their assertions, at 360/768/1440. The cause was
not established; no retry, timeout increase, assertion weakening or unrelated fixture repair
was applied. Further implementation stopped at this failed required gate. The earlier full
aggregate below is historical, not a current full-source pass. Backend/tools suites were not
rerun for this browser-only increment.

The approved-feed recheck still returned 404 for minimatch 11.0.1 and package metadata still
omitted that fixed release. No lock or package pin changed, and T106/T100 remain blocked.
Task status is **103/114 completed**; only T107/T108/T109 were newly checked. The original
T062/T086/T087/T100/T101 and convergence T106/T110/T111/T112/T113/T114 remain unchecked.
No actual provider, physical device, endurance, approved-sink delivery or native CI evidence
was obtained, and no new observation binding, publication or live action was attempted.

Final scoped hooks pass lint/format, complete management application typing, whitespace,
offline secrets and the full private-origin guard. Reference validation passes all 53
documents; all 42 diagram sources are unchanged, so no new rendering is claimed. Runtime
typing and Terraform hooks were not applicable to this six-file increment. These checks do
not convert the three full-suite setup errors into a pass.

## Pre-Convergence Local Regression

The corrected feature-aware profile completed all three full test directories and parsed actual
JUnit results: **851 runtime, 704 management and 284 tools tests**, each with zero failures,
errors or skips. No test selection/exclusion was added. Its report is `local_status=passed`,
`live_status=pending`, `release_status=blocked`. This is not a clean dependency or live gate.

The original aggregate run failed to return runtime results. Closing inherited stdin alone
did not resolve it. A sanitized owned-process diagnostic located the stall in an existing
Windows spawned-process authority test; the direct `python -m pytest` path passed the full
runtime suite and both existing spawned-process cases with JUnit. Release and native CI now
use each locked project's `python -m pytest` entrypoint. Seven failing-then-passing command
regressions and all 18 release-tool tests guard the change; the original 900-second suite
limit and every process/test assertion remain. The superseded owned process tree was stopped
without touching unrelated processes. The corrected actual aggregate then passed as above.

Root documentation validation passes **53 documents** and all **42 actual Mermaid renders**
using pinned 12.0.0, original sandbox and budgets. Current Python advisory lookups passed;
the Moderate frontend advisory remains failed/blocked, not suppressed or reclassified.

The final exact local CI source gate also passes canonical offline locks, complete Python
lint/format, all 46 runtime/39 management source type surfaces, whitespace, existing credential-
free Terraform validation, offline secrets and worktree/index/history public-origin checks.
Its complete tools rerun passes **284 tests** (56.82 seconds). No root frontend lock files were
created; the read-only quality checklist remains 16/16 and the root docs tree is the sole
maintained home. This leaves **100/105 tasks completed** with the five gates below unchecked.

## Earlier Local Checkpoints

| Checkpoint | Observed Result | Boundary |
|------------|-----------------|----------|
| US3 complete runtime | 845 tests, 219.40 seconds | Before later alert/API polish; not final-source evidence. |
| US4 full management | 699 tests, 501.05 seconds | Includes real compiled Reflex and both owned TLS apps; before alert polish. |
| Compiled editor/console | 72 tests, 309.66 seconds | Generated media and controlled devices at 360/768/1440, not physical hardware. |
| Current runtime lifecycle slice | 224 tests, 39.94 seconds | Controlled phase clocks, actual TLS and separate-process authority contention. |
| Root docs/tools checkpoint | 255 tools tests, 55.83 seconds | Before later qualification/alert inventory additions. |
| Root documentation | 52 documents, 42 diagrams rendered | Pinned Mermaid 12.0.0, original sandbox/budgets; before this new report. |
| Alert/runtime integration | 155 tests, 32.66 seconds | Controlled sinks/provider/store; restart does not adopt or release authority. |
| Alert/management integration | 93 tests, 17.91 seconds | Bounded local relay/console/composition observations. |
| Qualification/observer driver | 49 tests, 4.00 seconds | Offline manifests, explicit ports, unknown/short acceptance denied. |
| Application types/lint | 46 runtime and 39 management sources pass | Source checkpoint; final cross-cutting rerun still required. |

The actual two-app contract verifies new-cookie/different-mode stable-actor refusal, original
session liveness, exactly five occupied managers, sixth denial, text stop/restart with zero Live
allocation and ambiguous attempted readiness retaining capacity without replay. Synthetic
speech exercises native 24-kHz PCM, credits/played accounting, mute/interruption and cleanup.
Late permissions, device/playback failures, hidden documents, navigation, logout and socket loss
stop local media. Unknown closure remains pending. Screenshots were inspected at all widths;
the supported Reflex badge setting removed the observed mobile composer overlap.

## Functional Traceability

Each row identifies executable local evidence; a local result never substitutes for the
explicit actual gate in the final column. See [task evidence][tasks] for red-first fixes/counts.

| Requirement | Primary Local Evidence | Remaining Qualification |
|-------------|------------------------|-------------------------|
| FR-001 | [Exact Live protocol][live], [shared execution][execution], [opt-in driver][qualifier] | T062 actual Foundry model/version/delegation/features. |
| FR-002 | [Server ownership][execution], [admission proof][admission], [capability contracts][capabilities] | Actual approved environment; no browser/model authority. |
| FR-003 | [Same pinned registry/policy][execution], [two-app journeys][browser] | T062/T087 live modality parity. |
| FR-004 | [Configured graph/schema][validation], [registry tests][execution] | No separate modality definitions. |
| FR-005 | [Publication/projection][publication], [cache freshness][cache] | Actual existing store/identity approvals. |
| FR-006 | [No alternate protocol/fallback][live], [explicit composition][runtime-integration] | Unsupported/unapproved deployment stays unavailable. |
| FR-007 | [Community graph/forms/keyboard][browser], [draft commands][drafts] | Local complete. |
| FR-008 | [Stable child links and safe missing routes][browser] | Local complete. |
| FR-009 | [Agent graph/forms/entry/capability editing][browser], [complete graph rules][validation] | Local complete. |
| FR-010 | [Directed source-preserving Routing][visibility], [shared execution][execution] | Actual model execution T062. |
| FR-011 | [Discovery versus handover][visibility], [context isolation][execution] | Actual model execution T062. |
| FR-012 | [Global protected roles][validation], [protected editor][browser] | Local complete. |
| FR-013 | [Atomic rejected/publication handling][publication] | Actual private-store durability not inferred. |
| FR-014 | [Conflicts/save acknowledgements/draft retention][drafts], [navigation journeys][browser] | Local complete. |
| FR-015 | [Compiled all-width accessibility/overlap][browser] | Physical audio is separate T086. |
| FR-016 | [Guarded ticket/upgrade/TLS relay][manager-contract], [actual console][browser] | Actual identity/model access T062/T086. |
| FR-017 | [Native PCM/gesture/device doubles][console], [synthetic browser speech][browser] | T086 exact stable Edge/Chrome versions and physical output. |
| FR-018 | [Permission/late-track/exit matrix][browser], [terminal fences][console] | T086 physical audible cutoff. |
| FR-019 | [Text without microphone/Live][browser], [shared backend][execution] | T087 actual mixed acceptance set. |
| FR-020 | [Published pin/draft isolation][browser], [twenty fresh publications][runtime-integration] | T087 actual publication timing set. |
| FR-021 | [Owner/generation/phase invariants][admission], [actual old-socket fencing][runtime-integration] | T086/T087 physical/live behavior. |
| FR-022 | [Current/pinned/reviewed grants][capabilities], [next-action denial][runtime-integration] | Actual reviewed capability boundary. |
| FR-023 | [Cookie/Origin/CSRF/RS256/workload attack cases][manager-contract], [content privacy][privacy] | Actual approved identity/sink security review. |
| FR-024 | [Runtime content disposal/privacy][privacy], [volatile browser controls][console] | Actual provider residency/retention T062. |
| FR-025 | [Service telemetry][runtime-observation], [manager telemetry][manager-observation], [alert contract][qualifier] | Actual approved sink delivery/recipient acknowledgement. |
| FR-026 | [Five-slot/stable-actor/unknown retention][manager-contract], [phase/queue/expiry rules][admission] | T087 five full 600-second live participants. |
| FR-027 | [Immediate local suppression][browser], [bounded failure/cleanup][runtime-integration] | T086/T087 physical <=1-second cutoff and live timing. |
| FR-028 | [Default no-effect composition][runtime-integration], [offline driver][qualifier] | No actual gate is auto-executed. |
| FR-029 | [Suffix/hash-preserved root move and history][tasks], [static docs tests][docs-tests] | Local complete; no second maintained tree. |
| FR-030 | [Root assets/fragments/discovery/CI coverage][docs-tests], [actual renderer][docs-validator] | Local complete; final source scan required. |
| FR-031 | [Schema-1 refusal and fresh bootstrap][validation], [runtime unavailable rules][runtime-integration] | No migration/reset/legacy fallback; controlled deployment-404 tests pass in both modes. |
| FR-032 | [Setup][setup], [configuration guide][guide], [browser tests][browser], [task evidence][tasks] | Final full gates and actual acceptance remain explicit. |
| FR-033 | [Independent browser/provider boundaries][runtime-integration], [scope audit][tasks] | No phone research/provider/PSTN qualification performed. |
| FR-034 | [Existing dev/two-app verification][manager-contract], [root-only path consumers][docs-tests] | No hosting/IaC behavior or infrastructure activation. |

## Success Traceability

| Criterion | Local Evidence | Actual Acceptance Status |
|-----------|----------------|--------------------------|
| SC-001 | [Shared policy and text/synthetic speech][execution], [browser][browser] | T087 twenty eligible ten-text/ten-speech journeys pending. |
| SC-002 | [Authorization/protocol negatives][manager-contract], [visibility][visibility], [grant denial][capabilities] | No unreviewed alternate service; actual environment pending. |
| SC-003 | [All-width graph/forms/console journeys][browser] | Local complete; final full gate pending. |
| SC-004 | [Atomic publication/conflict/reopen][publication], [drafts][drafts] | Local complete; actual store qualification separate. |
| SC-005 | [Denied/late/unavailable/playback device doubles][browser] | T086 stable Edge/Windows and Chrome/Windows/Linux physical evidence pending. |
| SC-006 | [Terminal/expiry/content/owner fences][admission], [synthetic exits][browser] | T086 actual audible cutoff pending. |
| SC-007 | [Bounded concurrency and clocks][admission], [endurance refusal rules][qualifier] | T087 real-duration five-manager capacity/latency pending. |
| SC-008 | [Twenty publication/new-start and revocation path][runtime-integration] | Local controlled transport complete; T087 actual evidence pending. |
| SC-009 | [Root discovery/assets/fragments][docs-tests], [42 actual renders][tasks] | Local complete; final changed-source revalidation pending. |
| SC-010 | [Credential-free default][runtime-integration], [offline driver][qualifier], [two-app fixture][browser] | No actual provider/device/phone/hosting claim inferred. |

## Security and Scope Review

Current scoped runtime privacy/authorization/unavailable/owned-transport verification passed
138 tests (33.40 seconds); manager identity/ticket/relay/console/telemetry privacy passed 172
(228.88 seconds). The review repaired independent reply/context/structural history bounds and
counted unfinished relay disposal in local admission, with failing-then-passing regressions.
Unsupported deployment tests pass in both modes with no fallback or fabricated cleanup.
Full suites now pass as recorded above; the frontend advisory remains blocked. This is not
a blanket clean-security certification or proof of actual approved identity/provider/sink use.

Read-only source-path and history checks found no new telephony research/provider/PSTN path,
Azure Voice Live, Responses delegation, alternate frontend, hosting/IaC definition, Docker,
deployment controller or infrastructure activation. The constitution is unchanged. Historical
feature-001 task/evidence text and every marker remain exact apart from three current link
destinations. Existing Terraform validation used its pinned credential-free preparation and
32 mock native tests only, not a plan/apply, deployment or old-task qualification.

## Constitutional Gates

| Principle | Evidence and Disposition |
|-----------|--------------------------|
| I Azure-first | Explicit Foundry/Entra/resource ports; actual model/region/quota approvals remain T062. |
| II Security-first | Server authority, fixed signatures/origins, private-store ownership, privacy and final secret/public-origin checks pass locally; Moderate frontend advisory blocks the strict dependency gate, with no waiver. |
| III Documentation | One root maintained tree, reconciled guides/contracts, 53 checked documents and 42 actual Mermaid renders; history preserved. |
| IV Service boundaries | Separate locks/artifacts/private stores; management publication/gateway and runtime authority communicate through versioned APIs. |
| V Python/Reflex | Python applications and narrow Reflex-owned browser bridge; no alternate frontend application. |
| VI Bounded realtime | Declared queues/clocks/capacity, CAS/closure proof and local pressure tests; real five-manager endurance pending. |
| VII Observability | Bounded correlated test metadata and alert thresholds; approved-sink delivery remains unverified. |
| VIII Terraform | No infrastructure behavior changed; only existing documentation/evidence path consumers moved. |
| IX Automated tests | Current local 852/719/284 suites pass; observed source CI fails Ubuntu's graph drag journey and Windows/image dependency auditing. Required native gates remain blocked. |

## Final Gates

Read-only source checkpoint: base HEAD `a4379c987a3c696a94175bddd5fbdfd83d41d2bd`,
observed user-owned branch `feat/spec-002`. Tracked `git diff --binary -- code` SHA-256 is
`342bfbac8cbc5abf22984c1e47f207983889bb15ba739ce590bcc6106b9d57a5`; the sorted
path-to-SHA-256 manifest for 27 untracked nonignored code files hashes to
`6771ad0c3c4318da47e4753ffcafaa0b404b0699bd9a7b9954c402757e5e7b8c`.
All prior Python/frontend lock entries are unchanged; only declared feature dependencies
were added. This includes the existing vulnerable minimatch pin, which remains blocked,
not silently overridden. No source was staged or committed by this implementation.

- T099: Scoped privacy/authorization review and regression checks passed as recorded above;
  remaining dependency/full-source/actual environment gates are not declared clean.
- T100: Full backend/management/tools suites pass as recorded above; the frontend advisory
  still fails and this task remains blocked. The final exact local CI source gate passes
  locks, lint/format, application types, whitespace, existing credential-free Terraform checks,
  secrets/public-origin and 284 tools tests (56.82 seconds).
  The first full feature profile reported management 704 and tools 283 passed with zero
  failures/errors/skips; runtime returned `runner_or_result_unavailable`, so the profile
  failed. A complete visible runtime rerun passed 851 tests (124.24 seconds), without skips
  or exclusions. The unattended runner now closes inherited stdin, guarded by a failing-then-
  passing regression and all 18 runner tests. That alone did not repair the stall; the tested
  project-module launch change produced the separate complete passing profile above with
  unchanged scope/limits. The original failed attempt remains historical, not overwritten.
  Current Python advisory lookups passed; the frontend advisory gate remains blocked above.
- T101: An executed wiring regression confirms complete app suite commands, native Windows/
  Linux matrix, docs/advisory gates, read-only permissions and no test exclusions/live-endurance
  flags. The owner-authorized published source now has observed failed native CI: Windows
  application/docs steps passed but auditing failed; Ubuntu failed its graph drag journey.
  The source-specific checkpoint above preserves actual counts and unexecuted later gates.
- T102: Unsupported-deployment cases pass in both modes; [unavailable recovery](../runbooks/configuration.md#feature-002-unavailable-and-recovery-rules) documents schema/authority/older-artifact refusal without reset or fallback.
- T103: Read-only excluded-scope/constitution/deferred-task preservation checks passed above.
- T104/T105: Contracts/quickstart are reconciled and the completed nine-principle assessment
  explicitly withholds live/release acceptance; final source/reference/security checks pass.
  Final root/contract/spec validation passed 53 documents and actually rendered all 42
  Mermaid diagrams with pinned 12.0.0, original sandbox and budgets. Later prose-only
  evidence updates pass local reference/whitespace/secret/public-origin checks.
- T062/T086/T087 and approved-sink delivery remain unrun/unconfirmed; missing proof blocks
  affected live acceptance and never becomes release, deployment or merge approval.

The unresolved task gates are T062, T086, T087, T100 and T101. Approved-sink alert delivery is
also unverified and blocks affected acceptance under Principles II/VII and T105. No missing
result is a pass, and completing this assessment does not authorize publication or deployment.

[tasks]: ../../specs/002-multimodal-agent-management/tasks.md
[live]: ../../code/backend/tests/contract/test_gpt_live.py
[execution]: ../../code/backend/tests/unit/test_conversation.py
[admission]: ../../code/backend/tests/unit/test_test_admission.py
[runtime-integration]: ../../code/backend/tests/integration/test_conversation.py
[validation]: ../../code/management/tests/unit/test_configuration_validation.py
[publication]: ../../code/management/tests/integration/test_configuration_publication.py
[cache]: ../../code/backend/tests/unit/test_configuration_cache.py
[drafts]: ../../code/management/tests/unit/test_configuration_draft.py
[visibility]: ../../code/backend/tests/unit/test_community_visibility.py
[capabilities]: ../../code/management/tests/contract/test_capability_gateway.py
[manager-contract]: ../../code/management/tests/contract/test_browser_tests.py
[console]: ../../code/management/tests/unit/test_test_console.py
[browser]: ../../code/management/tests/browser/test_configuration_editor.py
[privacy]: ../../code/backend/tests/integration/test_content_privacy.py
[runtime-observation]: ../../code/backend/tests/unit/test_observability.py
[manager-observation]: ../../code/management/tests/unit/test_observability.py
[qualifier]: ../../code/tools/tests/test_verify_multimodal.py
[docs-tests]: ../../code/tools/tests/test_static_checks.py
[docs-validator]: ../../code/tools/scripts/verify_docs.py
[setup]: ../setup.md
[guide]: ../configuration-guide.md
