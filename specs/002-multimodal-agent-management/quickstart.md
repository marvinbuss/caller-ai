# Quickstart: Feature 002 Validation

**Purpose**: Verification guide for the locally implemented feature. Commands do not authorize
cloud/device use or establish actual acceptance. Read the current
[evidence and blockers](../../docs/acceptance/multimodal-agent-management.md),
[plan](plan.md), [configuration contract](contracts/configuration.md),
[conversation contract](contracts/conversation.md) and [data model](data-model.md).

## Prerequisites

Use the existing independently locked backend, management and tools environments with the
repository's pinned Python/uv/Node/Bun versions. Do not edit pins, regenerate locks or create
another frontend app merely to run this guide. Follow the maintained root
[development setup](../../docs/setup.md). Feature artifacts remain under `specs/` and current
application contracts under `code/contracts/`. Local tests require prepared browser dependencies, not
Azure login, provider tokens, Cosmos credentials, microphone permission or phone connectivity.

When dependency preparation is needed, the existing approved local-feed helper is:

```text
uv run --no-project --python 3.12.15 python code/tools/scripts/ci.py prepare
```

It prepares dependencies; it does not provision/deploy Azure. Keep private feed configuration
ignored and unprinted, preserve TLS verification and use each project's own locked environment.
Privileged OS browser prerequisites must be performed separately by the operator. Do not
disable browser sandboxing or omit required tests to work around missing prerequisites.

## Local Automated Validation

Run from repository root after implementation. These existing suite entry points must collect
the new feature tests, not only the old regression suite:

```text
uv run --directory code/backend --locked --no-sync python -m pytest tests -q --tb=short
uv run --directory code/management --locked --no-sync python -m pytest tests -q --tb=short
uv run --directory code/tools --locked --no-sync python -m pytest tests -q
uv run --project code/tools --locked --no-sync python code/tools/scripts/ci.py check
uv run --project code/tools --locked --no-sync python code/tools/scripts/verify_docs.py
uv run --project code/tools --locked --no-sync python code/tools/scripts/check_publication.py check
uv run --project code/tools --locked --no-sync python code/tools/scripts/verify_release.py --feature 002-multimodal-agent-management --profile local
uv run --project code/tools --locked --no-sync python code/tools/scripts/audit_dependencies.py
```

The feature-aware profile runs all three test directories with actual JUnit counts and the
original 900-second per-suite bound; it closes inherited stdin for unattended execution. It
uses the project Python module entrypoint so Windows spawned-process tests resolve the same
test modules as direct project execution, without changing tests or their own deadlines. It
never turns local success into live/release approval. Current strict frontend advisories are
blocked by [GHSA-g3hv-75wg-m249](../../docs/security.md#feature-002-current-advisory-blocker);
do not ignore that result, alter pins blindly or substitute an unverified fixed artifact.

Use `--directory` for management tests, not root-launched Reflex app imports that generate an
unwanted root frontend lock. Default sockets remain blocked except owned loopback fixtures.
Use existing ASGI/repository doubles and the owned HTTPS editor/browser host; credentials or
test identity must never be enabled by an application production setting. The default local
apps continue to show unavailable live functions without fetching identity/model credentials.

Expected local results are zero failed/error/skipped required tests, meaningful assertions for
the scenarios below, clean applicable lint/type/secret/dependency checks, root-doc link/assets/
fragment validation and actual Mermaid rendering. Record observed counts rather than copying
feature-001 counts. Required native Windows/Linux CI must execute the new tests and actual
Reflex build; a controlled protocol double is not a real Foundry/device qualification.

### Fast Routine Tests Versus Live Endurance

Normal local/native CI advances controlled clocks through the full 600-second session limit,
120-second user-idle timeout, five-minute disposal bound and shared ten-second startup deadline.
Use short actual-WebSocket/concurrency cases alongside those clock-driven tests, not ten-minute
wall-clock sleeps. No default test command above may start live inference or physical audio.

Also test the separation of clocks: start at simulated time zero, reach ready at four seconds,
and accept normal text/delegation work or media at 60 seconds. The original startup cutoff at
ten seconds must not reject that active session; the new work budget ends at 70 seconds, or
earlier on auth/session/idle expiry. Queueing and child steps consume that same work budget.
Reject initial readiness first arriving after startup expiry and expired work before dispatch.
Continuous media has session limits rather than a whole-stream ten-second cap. Expired
startup/work/session/login timers do not prevent trusted one-way cleanup, which retains its
separate bound and evidence checks. See the [deadline policy](contracts/conversation.md#phase-aware-deadline-policy).

T087 is a separate explicitly selected live-endurance mode of the approved qualification
driver, outside default pytest collection/CI. Run it only for a separately authorized acceptance
operation, not every edit, normal test run or CI job. It still requires five managers each
completing the full 600-second lifetime; virtual-clock tests cannot certify real endurance.
Keep required local assertions active rather than hiding them behind skip/deselection flags.
An unexecuted actual-endurance gate remains pending, not passed by the routine suite.

## Scenario Matrix

| Scenario | Procedure and Required Result | Criteria |
|---|---|---|
| Communities | In an owned authenticated fixture create two communities and one directed edge, drag a node, publish, reload and follow each child link. Drag changes presentation only; reverse transfer remains denied. | FR-007/008/010/013; SC-003/004 |
| Agents | Create three ordinary agents in one community, choose entry and directed handovers; verify eligible peer discovery and deny absent/reversed/foreign handover. Protected roles remain global. | FR-009/011/012; SC-001/002 |
| Conflict | Two verified managers edit one ETag; publish first, then second. Second keeps draft and sees conflict. Reject dangling entries/edges without changing publication. | FR-013/014; SC-004 |
| Navigation/accessibility | Reload a selected community, use back/forward and a stale/unauthorized ID; retain draft on internal navigation and require explicit discard on departure. Repeat graph operations via keyboard/forms at 360/768/1440. | FR-008/014/015; SC-003 |
| Text | Start with microphone permission denied. Confirm shared configured Agents flow, visible replies/revision and no GPT-Live voice session allocation. Stop clears volatile display. | FR-003/019/020; SC-001/005 |
| Speech double | Use documented Live startup/client-delegation/audio/transcript/close events over actual owned app WebSockets. Delegate public read-only work to the same Agents path, not provider Responses delegation. | FR-001/002/016/022; SC-001/002 |
| Admission | Race two independent runtime instances for one actor via different cookies/devices/modes; exactly one wins. Fifth distinct manager succeeds, sixth is unavailable; pending cleanup still consumes capacity. | FR-026; SC-006/007 |
| Startup deadline | Combine gateway authentication, relay, snapshot, CAS and model delays under the signed deadline fixed at ticket-POST receipt; prove ready/failure occurs within one ten-second budget, with late readiness fenced and no per-stage reset or cleanup-delayed failure. Microphone permission precedes the request. | FR-023/026/027; SC-006/010 |
| Active-work deadlines | At simulated t=60s after ready at t=4s, accept legitimate work/media despite the historical startup expiry; bound the new work at t=70s including queue/model/tool/handover time, deny on earlier auth/session/idle expiry and prevent duplicate/child-step reset. Continuous media is not a ten-second work item; trusted one-way cleanup remains possible after expiry without permitting new business work. | FR-021/026/027; SC-006/007/010 |
| Lifecycle | Advance controlled time through 120-second user idle, 600-second lifetime and earlier auth expiry. Heartbeats/silence/agent output do not extend idle; late callbacks cannot act/play or restart. | FR-018/021/026/027; SC-006 |
| Interruption | Queue audio and work, interrupt/handover/stop, then deliver late old-segment chunks and capability callbacks. Old generations remain fenced; closure uncertainty blocks replacement. | FR-021/027; SC-006 |
| Resource-aware cleanup | Complete/stop text work, verify settlement/disposal and start a new explicit test with zero Live connections. Fail admission before any external intent and release only from authoritative zero-attempt/fenced metadata. Recorded or ambiguous attempts remain occupied. Retain authenticated speech receipts; reject wrong-kind, foreign/stale/watermark evidence, dispatch-after-terminal races and TCP/lease/cancellation-only proofs, including after restart or lost writes. | FR-019/021/026/027; SC-006/010 |
| Publication/revocation | Across 20 revisions start tests and prove freshness within 60 seconds; old sessions remain pinned. Revoke a grant before the next capability attempt and confirm denial. | FR-020/022; SC-008 |
| Security/pressure | Test missing/null/foreign Origin, expired/replayed permit, wrong cookie/audience/actor, oversized/duplicate-key JSON, sequence replay, invalid PCM, queue/credit overflow and failed cleanup authority. | FR-023/025/026; SC-002/006 |
| Documentation | Move all 32 maintained docs suffix-preserving, repair incoming/outgoing paths and automation, render diagrams and reject duplicate old tree, broken image or fragment references. | FR-029/030; SC-009 |
| Privacy/defaults | Assert no audio/transcripts/prompts/tool payloads/credentials in durable stores, logs, traces, URLs, browser storage or error reports; purge volatile content by five minutes; default startup never calls cloud. | FR-024/025/028; SC-010 |

Use direct WebSocket tests as well as policy-unit tests; an in-process handler invocation is
not proof that authenticated upgrade, binary framing, credit bounds or disconnect cleanup work.
Retain red-first regressions for changed behavior and do not weaken old assertions or budgets.

## Separately Approved Foundry and Device Qualification

This section is an activation checklist, not a command to discover credentials or create
resources. The operator must explicitly authorize the exact test environment, deployment,
identity, content class, time/cost envelope and scenarios first. No telephony or hosting change
is needed or permitted. Missing approval leaves the integration unavailable.

1. Supply reviewed existing Foundry endpoints: GPT-Live mapping to `gpt-live-1`, and one shared
   text/function-calling deployment. Verify exact deployed version, region/processing residency,
   lifecycle/preview terms, supported function calls/streaming/storage settings and Entra scope/
   role. Public catalog tables do not establish this subscription's access or quota.
2. Verify available Live concurrency at least the intended five sessions and approved backend
   RPM/TPM for the workload, accounting for other usage and unconfirmed closures. Document
   voice seconds plus independent reasoning/tool costs and the operator's approved spend cap.
   Do not increase quota/provision anything or execute calls simply to fill this checklist.
3. Use the explicitly composed authenticated apps, shared admission authority and published
   configuration. Existing default `reflex run`/local runtime commands are not a live setup
   and must not silently activate by receiving ambient environment credentials. The
   implementation must document its reviewed composition invocation and fixed safe errors.
4. On stable Edge/Windows and Chrome/Windows/Linux, record browser/OS versions and actual
   microphone/headset. Start by gesture, select available/default output, speak/hear, mute,
   interrupt, stop and test denied/no-response permission, autoplay block, unplug, logout,
   page departure and network loss. Include real loopback/audible measurements; DOM labels
   and screenshots cannot prove headset output or one-second audible cutoff.
5. Execute at least 20 eligible scripted journeys, ten text and ten speech, with the same
   published graph and equivalent routing/capability rules. Text requires no microphone and
   no Live speech session. Confirm every excluded endpoint/delegation/service is absent.
6. Only in the separately opted-in T087 acceptance run, exercise five distinct managers
   concurrently, each for the full 600-second lifetime, supplying real accepted participant
   input before the 120-second idle expiry and confirming termination at the lifetime cap.
   This is not a default local/native CI run. Measure p95 first relevant
   response within two seconds of actual input completion, audible cutoff within one second
   and unavailable/failure outcomes within ten seconds. Transcript fragment cadence is not a
   true turn-end clock; use test input/playback instrumentation, without retaining media.
7. Test 20 publications/fresh starts and a mid-session revocation. Display actual revision,
   preserve old ordinary behavior and deny the next revoked action. Expiry never restarts.
8. Verify resource-appropriate cleanup: settled/disposed text work with no Live attempt, and
   provider `session.closed`/final usage for attempted speech segments. Check volatile content
   disposal and exact occupied-slot release. Unknown work/closure stays pending. Verify
   metadata-only event correlation and application alert delivery using the existing approved
   observer/test sink, without introducing hosting-specific monitoring infrastructure.

Record metadata-only results with exact source revision, real browser/deployment versions,
test environment, counts, timing distributions, failures and cleanup uncertainty. Do not
retain audio, complete transcripts, assembled prompts, payloads, permits or provider errors.
Any failed supported-browser/provider/latency gate blocks affected acceptance; do not relabel
synthetic evidence as live, broaden scope or raise the agreed budgets.

## Stop and Recovery

Stop is always immediately available; it stops capture/playback locally before network work.
Gateway/runtime endings are idempotent and fenced. A ten-second closure uncertainty leaves
the shared actor slot occupied for metadata-only verified cleanup, not automatic retry,
another test or socket adoption. After confirmed closure, a new test requires explicit Start
and selects the current publication. Clear browser content, stop any late media tracks and
dispose AudioContext/worklets. Follow existing session/capability audit recovery ownership;
never replay a tool/publication to repair an audit or missing acknowledgement.

Use the [resource-aware cleanup contract](contracts/conversation.md#resource-aware-cleanup-evidence)
for lost-write/owner-restart recovery. Verify persisted provenance, exact owner/epoch/session/
mode and current attempted/settled watermark. A terminal zero-attempt state with a dispatch
fence and verified local disposal can release a failed start. A text test releases only after
all recorded work definitively settles and resources are disposed, with no attempted Live
allocation; no Live receipt is required or fabricated for it. Speech retains exact authenticated
`session.closed` evidence and non-Live work-disposal checks. Recorded or uncertain work cannot
be reclassified as never attempted because a process restarted, a lease expired or cancellation
was requested. Persist external intent before I/O, reread ambiguous writes and keep missing
applicable proof occupied with an alert. Reviewed operator evidence uses the same verifier;
no force-clear, socket adoption or replay. Content disposal still meets its bound even while
metadata/capacity cleanup remains pending. Exercise these regressions with controlled clocks
and short actual transports, not a ten-minute routine test.

