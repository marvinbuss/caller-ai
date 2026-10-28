# Local Implementation Handoff

## Status

**Current renderer launch-path repair is locally verified; changed-source CI is pending.**
The user explicitly reopened timeout diagnosis. One exact-version Mermaid preparation now
precedes offline-only rendering; bounded stage/index diagnostics retain process cleanup,
sandboxing and all existing limits. Sixteen focused contracts and all 28 real local diagram
renders pass. The original hosted stall did not reproduce; its cause is not certified.
Existing run 40672760425 on 18195a2 passed before this repair, confirming intermittency rather
than proving a root fix. Application behavior is unchanged; 113/129 tasks remain checked.

A cold npm cache then reproduces a 300-second package-preparation stall before Mermaid
starts. Owned npm fetches now use a 30-second timeout with zero retries; a second fresh-cache
preparation and all 28 offline renders pass. The original hosted cause remains uncertified.
GitHub API rate limiting prevents reading the repair run; no hosted success is claimed.

**Earlier required Windows documentation failure.** Observed
[run 40669135195](https://github.com/marvinbuss/caller-ai/actions/runs/40669135195) on 2239345
passes Ubuntu and Linux images. Both native platforms pass 448 runtime/413 management/165
tooling cases and the 0/1/0 assertion/cleanup gate. Windows then times out in the actual
Mermaid renderer after the unchanged 300 seconds; its later dependency audit does not run.
Ubuntu completes all diagrams and audits. At that checkpoint the investigation was parked
under user direction, not certified repaired; merge/release stayed blocked without retries,
budget increases, sandbox changes or waived assertions.

An explicitly injected persistent authority now uses one bounded, Strong-read/ETag-protected
runtime-owned item for quota, drain and owner/call leases. All 34 new contract cases and the
full 448-case backend pass; strict runtime typing, protocol compatibility and touched lint
pass. All repository gates and 165 tooling cases pass; the changed sequence actually renders.
This is storage-double evidence, not actual Cosmos/process-crash or live qualification.
Physical coordination/reconciliation remain unfinished. Completion stays 113/129 with T123
unchecked. See [current US6 evidence](us6.md).

**Earlier shared-local lease CI passes.** Observed
[run 40656926575](https://github.com/marvinbuss/caller-ai/actions/runs/40656926575) on 271bc49
passes every Windows/Ubuntu/image step: 414 runtime/413 management/165 tooling cases per
native platform, 0/1/0 assertion/cleanup, all 33 documents/28 diagrams and strict package/
image audits. Shared local reservations, drain phase and lease coordination have thirty new
cases; at that revision persistent authority, cross-process fencing and durable recovery
remained missing. Completion stays 113/129 tasks,
with T123 unchecked and all external gates intact. Earlier host failures and parked
investigations remain recorded without root-cause claims. See [US6 evidence](us6.md).

**Foundation complete; live/release acceptance remains blocked.** Observed
[run 40599488969](https://github.com/marvinbuss/caller-ai/actions/runs/40599488969) on
`f4eae8f722a60cada5c0803989c2883f642abc07` passes every Windows, Ubuntu and Linux image step.
Each native platform passes 384 runtime/413 management/165 tooling tests, the 0/1/0 assertion
and cleanup gate, all 33 documents/28 actual diagram renders and strict package audits.
Actual image smoke and all three image audits pass. T031 is complete on this evidence;
six external tasks remain open. Parked investigations remain closed for now, not certified
root-cause repairs. See [current evidence and preserved history](foundation.md).

**Historical investigation closed for now at the user's direction.** Its cause was not
reproduced; closure is not certification of a root-cause repair. Preserve assertions/history
and move on unless a new failing check or user request reopens it. The separate save-status
repair has six red/green regressions; its complete local profile passes 384 runtime, 413
management and 165 tools cases with zero failures/errors/skips. Its changed-source native/image
verification is now complete as recorded above; six external tasks remain open.

**Earlier diagnostic CI passes; root cause remains unresolved.**
[Run 40590188047](https://github.com/marvinbuss/caller-ai/actions/runs/40590188047) on 6b7f03a
passes all Windows/Ubuntu/image steps, with 384/407/165 cases on each native platform and the
stronger keyboard assertions intact. This is verified probe execution, not a reproduced fix.
T031 remains open, alongside the six external tasks. See [current evidence](foundation.md).

**Earlier native verification failure.** [Run 40552023091](https://github.com/marvinbuss/caller-ai/actions/runs/40552023091)
on 78c54ba passes Windows and images, but Ubuntu has 384 runtime passes and 406 management
passes/one keyboard-journey persistence failure. T031 is reopened. The added read-only
save-boundary probe could not be validated locally because its frontend build timed out;
the subsequent hosted success above does not establish a root-cause repair.

**Local implementation handoff, not full release acceptance.** The user authorized remaining
local-only media/cleanup/audit/release code under injected provider doubles and subsequently
approved a sanitized source commit, branch push and draft PR. No provider/cloud/backend access,
real phone/model call, deployment, image publication or live qualification is authorized.
Source review is not release acceptance. All external obligations remain explicit.

The earlier fully successful hosted suites report runtime **384 passing cases**, management
**407 passing cases** and tools **165 passing cases** on each native platform, zero
failures/errors/skips. Native Windows, native Ubuntu and the Linux image job all succeed in
[run 40549068375](https://github.com/marvinbuss/caller-ai/actions/runs/40549068375) on `57b4e1d`.
The earlier complete combined local profile passed 384/407/149. At that snapshot
`local_status=passed`, `live_status=pending` and `release_status=blocked`.
The earlier missing-media module errors are removed by actual local media implementation,
not test deselection. Root-prerender failures are retained as history; the current local
eager-preview repair passes the complete management suite without exclusions.

## Implemented Local Boundaries

- Provider-neutral authenticated media with pre-upgrade identity/binding/codec denial, one
  local socket/reader/serialized writer, 64 KiB and 200 ms bounds, fresh epochs and safe closure.
- Actual lifecycle interruption/terminal cleanup integration, immediate volatile purge and
  separately truthful phone/storage confirmation. Audio port scopes dispose on accepted exits.
- Runtime-owned strict metadata audit persistence and rejected-credential management privacy.
- 200 synthetic lifecycle journeys with pinned revision, protected role ordering, one terminal
  outcome and safe audit/context disposal; never labeled eligible real phone acceptance.
- Full-directory regression runner with nonempty JUnit gates and no live/release-ready shortcut.
- Manual read-only protected release-review metadata and injected plan/publication interfaces;
  default preflight refuses all effects because real adapters/evidence are absent.
- Consolidated [runbooks](../runbooks/README.md), [coverage matrix](requirements.md),
  [capacity/cost gaps](../capacity.md) and [external release blockers](release-gates.md).

## Remaining Work Requiring Evidence or Approval

Qualified T042 telephony, T097 cloud metrics/alert delivery, T099 actual environment, real
T117 protected planning/publication, T118 live acceptance, and the complete T119 release
decision remain open: six external tasks. T028/T029/T031 have actual two-platform
application, 0/1/0 assertion/cleanup, documentation/render, audit and image evidence recorded
in [foundation acceptance](foundation.md). No source-only change can manufacture the remaining
external results. SC-008 remains deferred, not passed.

The isolated editor host retains the supported `APP_MODULE_IMPORT` selection. Its unattended
build and the Mermaid renderer now close stdin with `subprocess.DEVNULL` instead of inheriting
terminal input. Both invocation regressions failed before those changes and pass afterward.
The unchanged full native baseline reproduced 379 passes/24 export errors; after the launch
change, both standalone management and the combined runner pass all 403 cases. These are
observations, not proof that stdin explains every historical framework timeout. No SSR mode,
timeout, dependency code, assertion or test selector was relaxed; future recurrence must fail.
The subsequent feed-isolation full run did recur with the same 24 setup errors: stdin
hardening is not a complete fix, and the current gate correctly fails.

All actual current audit, image, typing and documentation results must be read with their
recorded scope/date; no prior passing image or focused test substitutes for a failed full
native regression or missing live measurement. See [security](../security.md) and the
[live environment record](live-environment.md) before any proposed environment action.

## Earlier Audit and Image Evidence

The preceding strict audit passed Python locks (runtime 69, management 92, tools 60 packages),
294 resolved frontend coordinates and three actual rebuilt images (zero HIGH/CRITICAL),
without alternate feeds, ignored findings or scanner changes. It also reruns all 119 tools
cases and credential-free Terraform checks successfully. Actual image smoke passes offline
non-root startup/health and Chromium shell cases at 360/768/1440; it is not the native editor.
All applicable touched-file hooks pass, including both application type checks and secrets.

All 33 documents and 27 Mermaid diagrams render successfully again on 2026-10-27 after the
noninteractive-launch change, with the original 300-second per-render bound and SVG checks.
The earlier repeat timeout/interruption at renderer 18 is retained as a failed attempt;
its batch-termination prompts were not evidence of a successful render. Neither the new
render nor the full native pass qualifies another host, remote CI or a live environment.

## Private Feed Publication Boundary

Private mirror settings and original locks are preserved only under ignored `code/.local/`.
Publishable Python artifact URLs were verified against public filename/SHA-256/size metadata
for 135 unique coordinates. Versions stay unchanged. All three locks pass uv's offline check;
all 296 frontend lock entries retain their versions/metadata/integrities with registry-relative
origins and pass Bun frozen validation. Public artifact downloading is not thereby proved.

The always-on guard checks tracked/untracked nonignored files, Git index and reachable local
history. Red-first regressions cover overrides, URL redaction, hash mismatch, regex literals
and index/history exposure. All repository hooks, both application type checks, credential-free
Terraform checks, 129 tools cases and 12 real-process recovery cases pass. The complete app
runner still reports the management failure above. A local frontend compile reintroduced
private lock URLs; the guard detected them and verified relocation restored the public lock.

Dockerfiles now use public defaults and locks. On source commit c60f7b0, the actual published
Linux image job passes public-input builds, offline/non-root/three-width smoke and strict
Python/frontend/three-image audits (Linux package counts 68/90/59, frontend 294, findings zero;
three images HIGH/CRITICAL zero). This is observed image evidence, not live qualification.
Local public metadata/download attempts encountered TLS failures; Windows native HTTPS could
fetch Python metadata, without bypassing certificate checks. GitHub initially returned HTTP
403 rate limiting. Sanitized source was then published as draft PR #1; its first Windows CI
job failed before tests because setup-python lacked Python 3.12.15 for Windows 2022. A tested
follow-up moves all CI helper execution into the independently locked uv tools environment.
The later Ubuntu native job on c60f7b0 passes runtime 384 and management 403 cases plus the exact
0/1/0 assertion gate with cleanup, closing T028. Its Mermaid step fails; Windows still has
24 editor setup errors. Local follow-up passes 143 tooling cases and all repository checks,
with bounded safe build-failure categories to identify missing renderer/editor diagnostics.
No raw failure content or private URLs are emitted, and no existing gate is relaxed. Full
native workflow acceptance and remaining live/release obligations stay open.

## Earlier Fully Successful CI Result

Run [40549068375](https://github.com/marvinbuss/caller-ai/actions/runs/40549068375) on
`57b4e1d9ad7bca3113e612b198af736d8d264392` passes every native Windows, native Ubuntu and
Linux image step. Both native jobs validate 33 documents and render all 28 diagrams, using
approved sandboxed installed Chrome 156.0.8078.48 on Windows and 156.0.8078.47 on Ubuntu.
Both prove exact 0/1/0 assertion failure/restoration with fixture cleanup. Strict Python and
294-coordinate frontend audits report zero findings; three actual images have zero
HIGH/CRITICAL findings. Actual image smoke passes offline/non-root and 360/768/1440 checks.

That snapshot closed T031's foundation gate, not the six external tasks above. Telephony remains
explicitly deferred and no live/release authorization is implied. Private feed settings
remain ignored locally; no TLS/sandbox/test threshold was weakened. Earlier Ubuntu-only
success and Windows failures are retained in the [foundation history](foundation.md).

## Preview Startup Repair History

A fresh retained SSR module import took 34,037 ms on Windows while React Router lazily
performed it inside its first 10-second preview request. A supported Reflex pre-compile Vite
hook now awaits that module during preview startup, under the original 180-second build
budget. Real Node regressions distinguish awaited initialization from the deliberately
unawaited variant. No dependency, package manager, SSR mode, vendor file or deadline changed.
The first full Windows check passes 405 then-collected cases; the final combined runner
passes 384 runtime/407 management/149 tools cases. At that snapshot the new remote results
remained pending; local evidence alone did not close T031 or establish a global framework fix.

The published eager-preview candidate ebb35db has now been checked remotely: run 40509624842
passes Ubuntu native and image jobs but Windows still has 383 passes/24 editor setup errors.
Thus T031 is still blocked despite the observed complete local pass. A diagnostic-only
follow-up normalizes ANSI internally and adds fixed encoding/type/file categories; all 153
tooling cases and repository checks pass. No raw content or private feed URL is emitted.
