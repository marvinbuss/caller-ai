# Release Gates

**Current Cosmos-authority increment is locally verified; release remains blocked.** The
injected persistent adapter passes 34 new contracts and the full 448-case backend, strict
runtime typing, protocol compatibility and touched lint. Changed-source native/image CI is
pending; all repository gates/165 tools pass and the changed sequence actually renders.
Physical coordination, uncertain-work reconciliation and actual Cosmos/environment
qualification remain open. T123 is partial and 113/129 tasks are checked. Storage doubles
are not live/release evidence; defaults remain credential-free and fail closed.

**Earlier shared-local lease CI passes.** Observed run 40656926575 on
271bc49 passes every native Windows/Ubuntu and Linux image step, with 414/413/165 tests per
native platform, all assertion/render/package gates and actual image smoke/three-image
audits. That revision's same-object quota/drain/lease authority is not persistent owner/lease
coordination, recovery or live capacity qualification. No merge/release approval follows;
earlier failures and parked investigations remain recorded. See [scoped US6 evidence](us6.md).

**Accepted foundation snapshot.** Observed
[run 40599488969](https://github.com/marvinbuss/caller-ai/actions/runs/40599488969) on f4eae8f
passes every native Windows, native Ubuntu and Linux image step. Both native platforms pass
384/413/165 tests, 0/1/0 assertion/cleanup, all 33 documents/28 diagrams and strict package
audits; image smoke and all three image audits pass. T031 is complete. Earlier renderer and
keyboard failures remain in [foundation history](foundation.md); parked investigations remain
closed for now without claiming root-cause repairs. This is not merge or release approval.

**Release blocked; historical investigation closed for now by the user.** Its cause was
not reproduced, not certified repaired. T031 tracks ordinary verification of the separate
save-status change rather than more diagnosis of that historical issue. The complete local
profile passes 384/413/165 cases with zero failures/errors/skips; current native/image checks
now pass as recorded above. All external gates remain required; no test or assertion is waived.

Earlier diagnostic
[run 40590188047](https://github.com/marvinbuss/caller-ai/actions/runs/40590188047) on 6b7f03a
passes all three jobs, including 384/407/165 tests per native platform, all original and
stronger keyboard assertions, assertion/cleanup gates, diagrams, package audits and image
smoke/audits. No application fix was made and the earlier failure did not reproduce; another
green run does not explain the intermittent missing-community result or local compiler timeout.

Earlier run
[40552023091](https://github.com/marvinbuss/caller-ai/actions/runs/40552023091) on 78c54ba passes
Windows and images, but Ubuntu fails the keyboard journey's post-save community assertion
(384 runtime passes, 406 management passes/one failure). T031 is reopened; the read-only probe
is blocked locally by a 180-second compiler timeout. No root cause or repair is established.

The earlier fully successful native Windows and Ubuntu CI suites each pass runtime
**384**, management **407** and tools **165** cases with zero failures/errors/skips. The earlier
combined local profile passed 384/407/149. That snapshot's local status was passed; live status remains pending
and release status remains blocked.

Earlier T031 foundation acceptance was recorded on [run 40549068375](https://github.com/marvinbuss/caller-ai/actions/runs/40549068375)
at `57b4e1d`: both native jobs and the actual Linux image job succeed. Both platforms prove
the 0/1/0 assertion gate with cleanup, render all 28 diagrams and pass strict package audits;
actual image smoke and all three image audits pass. See [foundation evidence](foundation.md).

The user explicitly authorized remaining local-only implementations with injected doubles.
The user later separately approved sanitized source commit/push/draft-PR review. Neither scope
permits provider selection, cloud/backend authentication, calls, deployment, image publication
or live qualification. Source publication does not waive failed or missing release evidence.

| Remaining gate | Required evidence |
|----------------|-------------------|
| T042 qualified telephony | Approved supported scenario/provider, lifecycle/licensing/region and verified bidirectional codec/control API; no fabricated wire protocol |
| T097 live monitoring | Qualified exporter mapping/query/on-call receipt and recovery for both services; disabled local rules are not delivery |
| T099 actual environment | Approved provisioned/pre-provisioned resources and real identity/probe adapters; eleven offline unavailable checks are not qualification |
| T117 actual protected plan/publication | Reviewed identity, immutable artifacts, protected repository controls and qualified adapters; read-only blocked workflow is not publication |
| T118 live acceptance | Approved real phone/model/Cosmos/load/drain/cost/diagnosis measurements; missing evidence is blocked, never skipped/passed |
| Privacy approvals | Accountable disclosure/residency/retention/provider decisions; no invented legal default or certification |
| SC-008 | Explicitly deferred participant study, not passed; automated management/accessibility remains required |

```mermaid
flowchart TD
    Local[Full runtime, management and tools regression] --> Counts[Nonempty JUnit counts and real exit status]
    Counts --> Passed[Current local regression passes]
    External[Qualified provider, environment, monitoring and live evidence missing] --> Blocked[Release blocked]
    Passed --> Review[Still requires all external reviewed gates]
    Review --> Blocked
```

## Commands and Trust Boundary

```powershell
uv run --project code/tools --locked --no-sync python code/tools/scripts/verify_release.py --profile local
uv run --project code/tools --locked --no-sync python code/tools/scripts/verify_release.py --profile live
python code/tools/scripts/release_checks.py --preflight
```

Local runs full test directories, owns child trees and parses bounded temporary JUnit results.
Missing, empty, malformed, failed, errored, skipped or nonzero results fail. Raw failure bodies
and candidate content are not emitted in the summary; temporary reports are deleted. There is
no automatic retry, selected-test exemption or media-error waiver. Passing this profile would
prove regressions only, not current static/advisory/image/remote/live approval.

The live profile refuses execution and exits 2. Protected preflight also exits 2 with no plan
applied or images published. It loads no execution/identity adapter. Real release ports must
authenticate fresh protected scope, bind exact commit/digests/environment, complete reviewed
plan work, recheck authority and idempotently reconcile publication. Unknown submission remains
uncertain, never a blind retry. The in-process typed port results are trusted abstractions,
not unforgeable credentials or proof that an external service is configured.

The manual `release-review` workflow is read-only, full-SHA pinned, has no PR trigger/secrets/
identity-token grant/apply and invokes only the blocked helper. Its environment name does not
configure GitHub reviewers; those external controls and real adapters still need review.

See [requirement coverage](requirements.md), [local handoff](final.md),
[live environment status](live-environment.md), [security](../security.md) and
[capacity/cost](../capacity.md). No task marker, image existence, virtual latency or manifest
can replace missing evidence or grant merge/release approval.

Current strict advisory/image rerun passes Python 69/92/60 packages, frontend 294 coordinates
and three actual rebuilt images with zero HIGH/CRITICAL findings. Current actual image smoke
also passes offline non-root startup and three shell widths; these preceding image results
are not another audit execution. Feed isolation changes Docker build inputs; fresh public
build/audit results were pending at that snapshot. The later c60f7b0 Linux image job now passes
the public-input build, offline/non-root/three-width smoke and all three HIGH/CRITICAL scans.
Its strict Python audits report 68/90/59 packages, frontend 294, zero findings. These results
are scoped to that actual job/revision, not a future live environment or whole-workflow pass.

## Local Failure and Recovery History

The 2026-10-27 baseline reproduced 379 management passes/24 editor-export errors. Unattended
editor build and diagram processes now receive closed stdin rather than the interactive
terminal; their launch regressions fail before the change and pass afterward. Standalone
management and the combined runner then both pass all 403 cases, and all 33 documents/27
diagrams render again. The earlier renderer-18 timeout/interruption and editor failures remain
recorded; stdin is not established as the cause of every historical timeout. No SSR, timeout,
assertion, dependency or suite-selection waiver exists. Future recurrence still fails locally.
The later feed-isolation complete run recurred with 24 root-prerender setup errors while
runtime 384 and tools 129 cases pass. The guard caught private URLs regenerated by the local
frontend compiler, then verified lock relocation restored publishable state. GitHub inspection
was initially rate-limited (HTTP 403). Later observed c60f7b0 Ubuntu results pass runtime 384,
management 403 and the exact 0/1/0 starter assertion gate; T028 closes. Its Mermaid step fails,
and Windows still has 24 editor setup errors. Local follow-up passes 143 tools cases, adds
safe non-content failure categories and preserves all failed gates; T029/T031 remain open.

The later 381e38d run identifies Ubuntu's renderer failure as `browser_sandbox_unavailable`;
Windows editor failure remains unclassified. A user-approved follow-up selects only installed
sandboxed Chrome on Ubuntu CI, records its version and fails if it is absent. Local rendering
and all timeout/TLS/assertion controls are unchanged. Tail-based categories remain bounded and
content-free. No success is claimed until the next actual remote result establishes it.

The next actual result, run 40495295307 on 9e76e90, confirms full native Ubuntu and Linux image
success. All 33 documents/27 diagrams render with sandboxed Chrome 155.0.8059.39, and strict
Python/frontend audits pass. T029 documentation verification closes. Windows still fails its
application-test step with `editor_frontend_build_failed:unclassified`; T031 and release stay
blocked. No participant study, provider/environment qualification or Windows result is invented.

The subsequent canonical temporary-root repair passes hosted Windows application and assertion
steps in run 40541941197, but its Mermaid step fails. After explicit user approval for Windows
CI's sandboxed installed Chrome, run 40549068375 on 57b4e1d passes all three jobs, including
Windows documentation and audits. T031 closes on that actual evidence. All historical failures
above remain disclosed; telephony and the six external task gates remain deferred or blocked.
