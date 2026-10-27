# Foundation Evidence: Partial Delivery

**Original Snapshot**: 2026-10-23. **Status**: Local Windows behavior verified; foundation acceptance
is incomplete. T001-T025, T027 and T030 are implemented and checked. The user authorized
continuing independent foundation work while T026's security fix is unavailable; the audit
failed in that snapshot and no vulnerability exception is approved. T028/T029 have verified local work
but incomplete external evidence. T031 and later feature phases are not accepted.

Subsequently, the user explicitly authorized local Phase 3 work beyond the original stop
boundary. Local T033-T041 artifacts now include metadata/storage, coordinator, SDK/Azure
protocol boundaries and delegated callback admission. The full backend run reports 154 passed
and 13 errors, all from the absent production media router. The passing foundation counts below are the earlier verified
snapshot, not a current green result for the expanded backend suite. No live functionality
or broader acceptance is implied by that authorization.

## Security Update: 2026-10-26

The historical sections below retain their original scope and counts. The current local
verification update is recorded separately; old missing-media and unavailable-security-fix
statements are not current implementation claims.

## Current Local Verification: 2026-10-27

An earlier complete combined runner passed **384 runtime, 403 management and 119 tools cases**,
with zero failures/errors/skips. All **33 documents and 27 Mermaid diagrams** rendered. The
unattended editor compiler and diagram renderer close inherited stdin; regression tests enforce
that launch contract and original timeout cleanup. Baseline management reproduced 379 passes/
24 export errors, followed by standalone and combined 403-case passes after the change.
This does not prove a universal cause or permanent fix for historical framework intermittency.
No SSR mode, timeout, assertion, dependency or suite selection was relaxed.

T029/T031 remain open for complete native verification; all
qualified-provider/environment/live/release obligations remain pending. See the [current
handoff](final.md) and [release gates](release-gates.md). The following security update and
original tables remain historical evidence rather than another current audit run.

## Observed Remote Evidence: 2026-10-27

Run [40480335153](https://github.com/marvinbuss/caller-ai/actions/runs/40480335153) uses source
commit `c60f7b0738815017777b6433b4fe4d6aa6530b46`, not the later local follow-up:

| Check | Observed result |
|-------|-----------------|
| Native Ubuntu runtime | 384 passed |
| Native Ubuntu management, including all browser/editor cases | 403 passed |
| Native Ubuntu starter assertion gate | baseline 0, broken 1, restored 0, fixture cleaned; T028 complete |
| Later Ubuntu Mermaid step | failed with generic renderer error; full native job not green |
| Native Windows management | 379 passed, 24 editor-export setup errors |
| Separate Linux image job | success; actual offline/non-root startup and Chromium 360/768/1440 |
| Linux strict package audits | Python backend 68/management 90/tools 59 and frontend 294; zero findings |
| Actual runtime/build-stage image audits | three images, zero HIGH/CRITICAL |

The preceding diagnostic follow-up passes 143 tooling cases and all repository/type/Terraform/
secret/publication gates. Fixed safe failure categories now distinguish sandbox, shared-library,
package-fetch, syntax, prerender and module errors without emitting raw content. No cause of
the generic remote renderer failure is assumed before a new observed result. No timeout,
sandbox flag, TLS check, test or assertion was relaxed. T029/T031 and all live gates stay open.

Run [40491123126](https://github.com/marvinbuss/caller-ai/actions/runs/40491123126) on 381e38d
again passes the Ubuntu application/assertion steps and Linux image job, but the new fixed
diagnostic identifies `browser_sandbox_unavailable` for Mermaid. Windows still fails editor
setup with an unclassified build error. The user approved CI-only installed sandboxed Chrome
with recorded version; other platforms keep prepared Chromium. The new browser-selection and
bounded-tail diagnostic regressions pass locally; actual repaired remote results are pending.

The subsequent [run 40495295307](https://github.com/marvinbuss/caller-ai/actions/runs/40495295307)
on `9e76e90ea70ce2cf5ac45be9a85ef66cff708529` confirms the repair: native Ubuntu and the
separate Linux image job both succeed. Ubuntu reports all 33 documents/27 actual diagrams
rendered with installed sandboxed Chrome **155.0.8059.39**, and strict Python 68/90/59 plus
frontend 294 package audits report zero findings. No sandbox-disabling flags or host policy
changes were used. All 149 local tooling cases and repository gates also pass.

T029 documentation/command/diagram verification is complete on observed results and disclosed
failure/recovery scope. T031 remains unchecked: the same current run's Windows application
step fails on editor-build setup with an unclassified safe category. Neither that job nor the
whole workflow is green. The bounded-tail improvement did not resolve or classify this failure;
its root cause must be established separately, without dumping raw content or weakening tests.

Windows encoding follow-up: redirected child stdout was reproduced as cp1252/UTF-8 mode off,
and a Unicode checkmark failed with `UnicodeEncodeError`. Owned editor children now explicitly
use UTF-8, verified through a real fresh-process regression. The complete local management
check still has 379 passes and 24 `frontend_prerender_failed` setup errors (259.18 seconds),
so this separate encoding defect does not clear T031. The remaining preview timeout is not
worked around through npm, another SSR mode, vendor changes, retries or increased limits.

Published encoding repair `9517f07ba8a6249fd6647f97282c08743fc173ac` triggered
[run 40500604711](https://github.com/marvinbuss/caller-ai/actions/runs/40500604711).
Its native Windows job 122177030397 still reports failure. The detailed log cannot currently
be inspected because the REST API is rate-limited; GraphQL check metadata confirms the failed
job but does not establish its inner error category. The isolated UTF-8 regression, all 15
smoke-helper cases, all 149 tooling cases and repository gates pass locally. T031 stays open;
no root-cause claim for the remaining build failure or repeated green-run retry is made.

## Cold Preview Initialization Follow-up

A fresh Node import of the retained real SSR bundle took **34,037 ms** on Windows. The
installed React Router preview handler lazily imports that bundle inside the first request,
whose existing budget is 10 seconds. The supported Reflex `add_modify_task` integration now
registers a Vite preview-startup hook that awaits the completed server module before requests.
No framework/vendor file, dependency, SSR mode, package manager or timeout was changed.

The first full native Windows run after the hook passes **405 then-collected tests** with
no skips/deselection (324.86 seconds). Registration/idempotency/template-drift regressions pass;
real Node tests also prove the hook waits, and the deliberately unawaited variant exposes
the initialization gap. These observations distinguish module startup from actual page
response work, not permission to increase budgets. New remote Windows/Linux/image evidence
is still required before T031 closes; earlier failed runs remain recorded above.

The final complete local runner passes **384 runtime, 407 management and 149 tooling cases**,
zero failures/errors/skips, after the actual Node-ordering regressions were added. It reports
`local_status=passed`, `live_status=pending`, `release_status=blocked`. All repository/static/
type/Terraform/secret/publication checks pass. This does not substitute for the new native CI
and actual image results for the changed frontend build; T031 remains pending those results.

Published candidate `ebb35db0e9d774e004d9281090306c6202b78881` is now observed in
[run 40509624842](https://github.com/marvinbuss/caller-ai/actions/runs/40509624842).
Ubuntu native and image jobs succeed, but native Windows reports **383 passes/24 editor
setup errors** with a still-unclassified safe build category. The local complete pass is
therefore not a full cross-platform repair, and T031 remains open. Safe classification now
strips ANSI sequences internally and recognizes encoding/type/missing-file errors, with
red-first regressions and all 153 local tooling cases/repository gates passing. No raw log
body is published and no request/build budget or security boundary is changed.

## Historical Security Update: 2026-10-26

The previously unavailable approved-feed fixes are now installed. A targeted tools-only lock
update moves virtualenv 21.14.7 to **21.14.11** and filelock 4.2.0 to **4.2.4**, with retained
manifest security floors. Strict Python audits pass **69 backend, 92 management and 59 tools
packages**; the frontend audit passes **294 coordinates**. No ignore, alternate feed, TLS
bypass or waiver was added. All 13 baseline findings in the two tools packages are remediated;
application locks are unchanged.

After the user restarted the engine, Linux-container availability was confirmed. The first
current image retry exposed HIGH `CVE-2026-92392` in management builder curl/libcurl 8.22.0-r0.
APK security floors require both packages >=8.23.0-r0; the unchanged complete advisory gate
now passes all three actual runtime/build-stage images with **zero HIGH/CRITICAL findings**.
Python/frontend results remain clean, and the final tooling suite passes **103 cases**.

Current real image smoke passes offline/non-root startup, health/readiness and all three
responsive shell/browser/event cases. Its initial timeout is retained as evidence: the image
harness had selected native editor-export fixtures as well as the image shell. A regression-
tested image-specific selector and owned process-tree cleanup fix that harness boundary without
changing shell assertions, the 180-second limit or full native management discovery.

T026 is now checked based on current executed evidence, not the historical table below.
See [current security gates](../security.md). No native Linux host or remote CI result is
inferred from Linux containers on Windows. Full application media/editor-export, cross-platform
and live obligations still block foundation/release acceptance.

## Executed Local Results

| Check | Observed Result |
|-------|-----------------|
| Runtime `uv run --directory code/backend --locked pytest -q` | 49 passed |
| Management `uv run --directory code/management --locked pytest -q --screenshot=on` | 30 passed on repeat, including browser and recovery |
| Tooling `uv run --project code/tools --locked pytest code/tools/tests -q` | 25 passed |
| Root CI helper `ci.py prepare` | All three independent locked environments prepared |
| Root CI helper `ci.py check` | Lint, format, both app type checks, whitespace and offline secret scanning passed |
| Owned `smoke.py --browser` | Both actual apps ready, health under one second, browser shell passed, child trees cleaned |
| Browser widths | Chromium at 360, 768 and 1440; keyboard access, server-backed event and no error toast |
| Screenshots | Ignored management `test-results/`; mobile and desktop visually inspected |
| Python/toolchain | Python 3.12.15, uv 0.13.4, Reflex 0.10.0, Bun 1.4.2, Node 24.22.0 |
| Platform | Native Windows; Linux and remote GitHub Actions not observed |
| Docker | Docker Desktop 29.9.2, Linux x86-64 engine; both real images built/run |
| Actual image browser smoke | 360/768/1440, real events, one-second health and owned cleanup pass |
| Image advisory scans | Docker Scout 1.27.0: both runtimes and actual management builder report no vulnerabilities |
| Python advisory scans | Backend/management pass; tools fail on virtualenv 21.14.7, CVE-2026-113722 |
| Disposable assertion gate | Baseline exit 0, deliberately broken exit 1, restored exit 0, fixture cleaned |
| Readiness ports | Independent SDK-free imports, local stand-ins and safe 250 ms unavailable/error/timeout cases pass |
| Recovery | Six actual-process cases per service: occupied port, invalid/live modes, missing/stale locks, interrupted startup |
| Documentation | Eight Markdown documents validated, two Mermaid diagrams rendered and visually reviewed |

## Subsequent Phase 3 Local Results

These results supplement, not replace, the incomplete foundation acceptance snapshot.

| Slice | Observed Result |
|-------|-----------------|
| Complete expanded backend pytest, no exclusions | 154 passed, 13 media-router errors; not green |
| Lifecycle and session metadata | 37 passed, including two red-first yielding-store race regressions |
| Conditional repositories | 23 passed over local and injected Cosmos double |
| Scripted real SDK lifecycle | 17 passed, including projection rejection and privacy-safe tracing |
| Azure protocol/SDK wrapper | 16 passed, with explicit identity, bounded retry and sanitized URL failures |
| Provider callback boundary | 12 passed with no live provider or route registration |
| Documentation | Nine documents and two rendered Mermaid diagrams validate |

T042 needs an approved supported provider/scenario and evidence of its actual bidirectional
audio, playback clear and hangup wire/control protocol. T035's assessment leaves these pending;
no actual provider, tenant eligibility, credentials, region or model deployment is qualified.
No live call or model connection was made. T043-T047 remain unimplemented. T026's security
deferral and all missing foundation/native-Linux/remote-CI evidence remain unchanged.

The health tests validate the accepted schema and service identity through the actual FastAPI
runtime and Reflex public ASGI composition. Local readiness keeps both business capabilities
false. Live startup fails closed. Local tests reject external connections and assert no Azure
credential/Cosmos/model client is constructed. Loopback and Unix sockets remain available for
owned servers and Windows asyncio's internal socketpair.

Application-owned traces and JSON events exclude sensitive payloads and raw exceptions.
Safe traceparent context propagates without baggage. Local capture is bounded and cleared at
shutdown. SDK tracing defaults off; explicit SDK opt-in uses fixed-operation in-memory metadata.
Provider realtime tracing opt-in is rejected because the locked facility lacks approved
metadata-only destination/content controls. No live provider tracing is qualified.

## Meaningful Failures and Recovery

- Health, settings, transport and telemetry tests failed against missing implementation before
  their corresponding code was added. Fresh-process SDK policy tests also failed before code.
- The initial blanket socket block prevented Windows asyncio startup. A loopback-only allowlist
  repaired that harness defect; regression tests still reject external connection attempts.
- Actual screenshots exposed a Reflex registration-context error toast despite initially green
  browser assertions. The strengthened toast assertion failed at all widths before the factory
  repair, then passed after the initial app bound to its ambient context and later apps forked.
- Synthetic secret input is rejected without printing its value; safe files pass. Missing scanner
  inputs fail closed. Whitespace failure and explicit recovery are tested without silent mutation.
- Smoke ownership tests reject foreign instance headers and unowned frontend ports, fail on early
  exits, and verify cleanup of actual parent/child processes. No unrelated process is terminated.
- Injectable readiness checks return only safe reason codes on dependency failure or timeout;
  liveness makes no dependency call. Both port modules import no live SDK clients.
- Recovery tests interrupt real application startup and verify process-tree cleanup, preserve
  an existing occupied-port listener, and reject invalid/live mode and missing/stale locks.
  Granian's Windows error 10013 is accepted only alongside failed startup and proof that the
  original listener still accepts a connection.
- The disposable failure gate runs a real health assertion in three fresh pytest processes.
  Its wrong service identity produces an actual assertion failure, then restoration passes.
  Genuine health-test source remains byte-identical; no user files or branches are changed.
- The initial Mermaid CLI download exceeded its bound. Process-tree cleanup was added and
  regression-tested; a warmed-cache retry rendered both diagrams without increasing the limit.
- One full management run exceeded the original 120-second startup deadline. An owned
  diagnostic then reached readiness, and the complete management suite passed on repeat
  under the unchanged limit. This earlier failed attempt is not hidden as first-run success;
  cross-platform startup stability still needs remote evidence.

## Blocking Evidence Gaps

- **Advisory gate resolved locally**: Tools and build-stage curl findings are remediated and
  current strict Python/frontend/three-image Trivy checks pass. This closes T026 only; it
  does not clear full-suite, native-platform, remote CI or live gates.
- **Container remediation**: Debian zlib CVE-2026-85091 prompted same-Python-version Alpine
  bases. Vite CVE-2026-112942 was fixed with an 8.3.3 override; standalone unzip was removed.
  Actual runtime and builder scans now pass. Unused npm files flagged in the Node source
  image are not copied into the real builder; its actual filesystem was scanned separately.
- **CI and platforms**: Extended SHA-pinned read-only native Windows/Linux application/browser
  and Ubuntu image jobs exist and their entry points pass locally. Observed remote runs and
  native Linux host evidence remain pending. Linux containers on Windows are not native Ubuntu.
- **Remaining foundation work**: T026 now has actual current advisory/image evidence.
  T028's local assertion proof and CI step exist, but the remote step has not been observed.
  T029's reconciled docs, links and rendered diagrams exist; native Linux host command
  verification and preceding incomplete gates remain pending. T031 cannot claim complete
  SC-015/SC-016/SC-017 acceptance from local Windows and Linux-container evidence alone.

SC-015 is only partially evidenced locally. SC-016 and SC-017 lack complete native
cross-platform/remote-CI evidence despite passing actual image tests. T031 remains unchecked.
Do not label this milestone complete,
production-ready, or capable of real calls or authenticated agent editing. Independent local
work continued with user authorization; release/acceptance remains blocked until required
regression and external evidence are available. Stop at the foundation checkpoint,
not the later live-call/configuration story phases.
