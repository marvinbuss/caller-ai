# Bug Assessment: Image publish and CI fail on HIGH krb5 CVEs; a slow delegated model step ends speech sessions and locks the manager out of new tests

- **Slug**: gh-wofkflows-and-speech-session
- **Created**: 2026-11-12
- **Source**: pasted text with two GitHub Actions links (see below)
- **Verdict**: valid (all four reports, confirmed from CI logs and int environment logs)
- **Severity**: high

The report bundles four symptoms. They share two root causes:

| # | Symptom | Root cause | Severity |
|---|---------|-----------|----------|
| 1 | Publish Images fails for both images | A: base image ships `krb5-libs` 1.22.1-r0 with 16 HIGH CVEs | high (no image can be published) |
| 2 | Foundation CI "Linux image health and browser smoke" fails on the merge of PR #40 | A: same CVEs, found by the dependency and image audit | high (merge gate red on `main`) |
| 3 | Test console stuck on "Busy" (text and speech) on 2026-11-12 ~10:09 CET | B: the session that ended in #4 left its admission slot in `cleanup_pending` forever, so every new start by the same manager is refused as `manager_busy` | high (manager locked out of all tests) |
| 4 | Speech session ends abruptly while the user is talking (2026-11-11 20:25–20:35 CET) | B: one delegated model step ran past its 10-second work budget; any work-item failure closes the whole session, and the timed-out model attempt can never be settled | high |

## Report (verbatim)

> 1. Image Build: Image build failed for both images. See here: `https://github.com/marvinbuss/caller-ai/actions/runs/43204396281`
> 2. GH job failure: The following GH job failed: `[Merge pull request #40 from marvinbuss/005-management-frontend-redesign · marvinbuss/caller-ai@ec10cb8](https://github.com/marvinbuss/caller-ai/actions/runs/43204396551/job/131135764361)`
> 3. Test plane in management app stuck: When I try to run a text or speech session, it is stuck and just shows `Busy` or similar. I tried today at ~10.09 CET.
> 4. Speech session ending abruptly: When I talk to the agent for some time I still have scenarios, where the session just ends abruptly even though I was speaking and even though there should not have been any timeout. I experienced this again yesterday between 20:25 CET - 20:35 CET.

### URL handling

| URL (verbatim) | Host | Policy branch |
|----------------|------|---------------|
| `https://github.com/marvinbuss/caller-ai/actions/runs/43204396281` | github.com | allowlisted; read with the authenticated `gh` CLI (`gh run view --log-failed`) |
| `https://github.com/marvinbuss/caller-ai/actions/runs/43204396551/job/131135764361` | github.com | allowlisted; read with `gh api .../actions/jobs/131135764361/logs` |

No instruction-like content was found in the fetched logs.

### Additional evidence gathered (read-only)

- Int environment logs, Log Analytics workspace `log-caller-int`, table `ContainerAppConsoleLogs_CL`. Only structured event fields were read; no prompts, transcripts or tokens.
- Times below are UTC. CET is UTC+1, so 20:25 CET is 19:25 UTC and 10:09 CET is 09:09 UTC.

## Symptom

1. **Publish Images:** both images build, then the Trivy scan fails each one with `Total: 16 (HIGH: 16, CRITICAL: 0)`. All 16 findings are `krb5-libs` 1.22.1-r0 (fixed in 1.22.3-r0) in the `alpine 3.23.7` layer.
2. **Foundation CI:** in job "Linux image health and browser smoke", the step "Audit dependencies and actual runtime/build images" fails. `audit_dependencies.py --images` raises `CalledProcessError` from the Trivy `docker run` on the first image (exit code 1, same HIGH findings).
3. **Test console:** starting a text or speech test shows "busy" / "Another test active". Expected: a new session starts.
4. **Speech session:** a speech session ends with an error after about 4.5 minutes while the user is talking. Expected: the session continues until the user stops, the idle limit or the 10-minute lifetime.

## Reproduction

1. **Image scan:** push to `main` (or run the "Publish Images" workflow). Trivy 0.76.1 reports CVE-2026-107364, CVE-2026-112315…112323, CVE-2026-118380, CVE-2026-119079, CVE-2026-40355, CVE-2026-40356, CVE-2026-71199 and CVE-2026-71200 in `krb5-libs` for `ghcr.io/marvinbuss/caller-ai/runtime` and `.../management`.
2. **CI audit:** run `uv run --project code/tools --locked python code/tools/scripts/audit_dependencies.py --images` on Linux with Docker. It fails in `audit_images()` on `caller-backend:audit-*`.
3. **Ended speech session and stuck slot** (observed on int, 2026-11-11, correlation `1da50cd100bb40e1a5094c8182468622`):
   1. 19:27:08 — the speech session is admitted (`test.admission success`); the user talks continuously (`voice_seconds_snapshot` every 15 s up to 270 s).
   2. 19:30:27–19:32:15 — five delegations get `test.delegation failure invalid_input`. These are the clarity check judging the request ambiguous and the session answering "Please clarify". One delegation at 19:31:52 succeeds in 2.69 s.
   3. 19:32:23 — a delegated work item fails after `work_seconds 9.003` with `test.work.failure error=PermissionError code=model_unavailable`, followed by `test.work failure unavailable`.
   4. 19:32:23 — the session ends: `test.session failure cleanup_pending`, `test.cleanup failure cleanup_pending`, `voice_seconds_final 270`.
   5. 19:32:33 — `test.alert failure cleanup_uncertain`. The slot stays occupied.
4. **Stuck console** (2026-11-12, 09:08–09:10 UTC):
   - Management opens consoles (`test.relay ready` at 09:09:14 and 09:09:42) and the runtime accepts a voice WebSocket at 09:08:58.
   - Both text turns get `POST /v1/conversations/text 503 Service Unavailable` (09:09:24, 09:09:50), which is `capacity_unavailable`, reason `manager_busy`.
   - No `test.admission` event is logged for these refusals.
5. **To reproduce #4 locally**, use a model double whose streamed reply stalls past the work budget. In a speech session, a delegated reasoning step that runs longer than the remaining budget triggers the same `model_unavailable`, and the session ends.

## Suspected Code Paths

**Root cause A (images):**
- `code/backend/Dockerfile:2,12` and `code/management/Dockerfile:3,23`: the build and final stages use `python:3.12.15-alpine3.23@sha256:3d0ca66b…`, which contains `krb5-libs` 1.22.1-r0. No stage upgrades it. The management build stage already pins `curl` / `libcurl` versions for an earlier CVE, which is the precedent for this fix.
- `.github/workflows/images.yml:74-75`: Trivy with `--severity HIGH,CRITICAL --exit-code 1` (correct gate).
- `code/tools/scripts/audit_dependencies.py:397-440` (`audit_images`): the same gate in Foundation CI; it builds `code/backend`, `code/management` and the management `build` target.
- `code/tools/tests/test_static_checks.py:575-583`: asserts the management Dockerfile's `curl` pins. This is the place to lock in a `krb5-libs` pin.

**Root cause B (sessions):**
- `code/backend/src/caller_runtime/services/conversation.py` (`process_next`, about lines 693–745): any exception from `_execute` sets `self._failed = True` and calls `await self.close()`. One failed work item therefore ends the whole speech session.
- `code/backend/src/caller_runtime/services/conversation.py` (`_execute` and `_clear_task`): the clarity check and the reasoning loop (`max_turns=1` per pass, repeated after tool calls) share one `WorkBudget`. They run under `asyncio.timeout(item.receipt.work.remaining(...))`, which is at most 10 s from acceptance.
- `code/backend/src/caller_runtime/domain/conversation.py:409,421` and `services/delegation.py:207`: work budgets are fixed at `now + 10 s`.
- `code/backend/src/caller_runtime/adapters/foundry_text.py` (`_attempt` and `_stream`): when the budget runs out mid-call, the `TimeoutError` becomes `PermissionError("model_unavailable")`. The model attempt was dispatched but is never settled; only `_settle_refusal` settles a definite provider refusal.
- `code/backend/src/caller_runtime/services/admission.py` (`_cleanup_matches`, about line 870): release requires `slot.settled_through == slot.attempted_through`. With an unsettled model attempt the evidence never matches, `release()` returns `False`, and the slot stays `cleanup_pending`.
- `code/backend/src/caller_runtime/services/admission.py:249-254` (`reserve`): `manager_busy` is returned when *any* slot holds the same `actor_key`, including terminal `cleanup_pending` slots. One stuck slot locks that manager out of every new text or speech test indefinitely.
- `docs/runbooks/scaling.md:73` and `docs/runbooks/monitoring.md:10`: the documented design keeps uncertain slots occupied and forbids force-clearing. No bounded, evidence-based recovery exists, so the lockout has no end.
- `code/management/management_app/components/live_audio_js.py:489,821`: `manager_busy` is shown as "Another test active" / status `busy`, which matches the user's "Busy".

## Root Cause Hypothesis

**Root cause A (high confidence).** The pinned Alpine 3.23 Python base image contains `krb5-libs` 1.22.1-r0. Sixteen HIGH CVEs against it were published after the digest was pinned. Trivy now fails both the publish workflow and the Foundation CI image audit. Nothing in the repository changed to cause this; any build after the vulnerability database update fails.

**Root cause B (high confidence for the chain, medium for the exact reason the step was slow).** During a speech session, a delegated reasoning step took longer than its fixed 10-second work budget: 9.0 s measured, the budget's remainder after the clarity check. The Foundry adapter converts the timeout into `model_unavailable`. `process_next` treats any work-item failure as fatal and closes the whole session, which explains the abrupt end while the user was speaking (#4). The timed-out model attempt is dispatched but never settled. So cleanup evidence can never satisfy `settled_through == attempted_through`, the slot stays `cleanup_pending` (`cleanup_uncertain` alert), and `reserve()` refuses every later start by the same manager with `manager_busy` (#3).

Why the model took about 9 s is not known. Candidates: a slow reasoning model, a long tool-call loop within one item, or a clarity check plus reasoning both inside the same 10 s.

## Proposed Remediation

### Root cause A — upgrade `krb5-libs` in the images (fixes #1 and #2)

**Preferred:** in every stage built from the Python Alpine base, upgrade the vulnerable package with an explicit minimum, following the existing `curl` pin pattern:
- `code/backend/Dockerfile`: the final stage.
- `code/management/Dockerfile`: the build stage (scanned by the audit) and the final stage.

```dockerfile
RUN apk add --no-cache --upgrade 'krb5-libs>=1.22.3-r0'
```

Extend `test_static_checks.py` so both Dockerfiles must carry the pin. Then re-run "Publish Images" and the Foundation CI job.

**Alternatives:**
- Bump the pinned `python:3.12.15-alpine3.23` digest (and `node:24.22.0-alpine3.23` if it carries `krb5-libs`) once upstream publishes rebuilt images with `krb5-libs` 1.22.3-r0. This is cleaner long term but depends on upstream timing.
- A Trivy ignore for these CVEs. Not recommended: they are fixed upstream and HIGH.

**Files likely to change:** `code/backend/Dockerfile`, `code/management/Dockerfile`, `code/tools/tests/test_static_checks.py`.

**Tests to add or update:** a static check that both Dockerfiles' Python Alpine stages install `krb5-libs>=1.22.3-r0`. Then the actual Trivy gates: "Publish Images" and `audit_dependencies.py --images`.

### Root cause B — keep the session alive on a failed work item and make the timed-out attempt releasable (fixes #4 and prevents #3)

**Preferred**, in three parts:

1. **A failed work item fails only that item** (`services/conversation.py`, `process_next`). Only failures that end the session should close it: terminal fence, authority loss, context limits. For a bounded per-item failure such as `model_unavailable` or `work_deadline_exceeded`, report `work_unavailable` for that task (already emitted) and tell the live model it could not complete ("I could not finish that, please try again") through the speech segment. The session stays open. Accepting new work while an earlier model attempt is still unsettled must stay blocked, as it is today (`work_busy` per resource kind), until part 2 settles it.
2. **Settle a model attempt whose client was disposed after a timeout or cancel** (`adapters/foundry_text.py`, `services/cleanup.py`, `services/admission.py`). The Responses call runs with `store=false` and the adapter closes its HTTP client on every path. Once disposal is confirmed, record a settlement such as `model-abandoned-<id>` through the cleanup observations. The independent verifier can then match `work_disposed` evidence and release the slot. This must be a deliberate, documented decision: the provider may still bill the abandoned request, but nothing persists remotely.
3. **Recover existing stuck slots.** Add a reviewed, idempotent recovery path for slots already stuck in `cleanup_pending`; the int authority has at least the 2026-11-11 19:32 slot. Two options:
   - Re-run the cleanup verifier with the new settlement rule at startup.
   - A runbook-backed script with a preview mode that releases only terminal slots whose session expired, whose owner process is gone and whose only unsettled attempts are `model` kind.

   Do not delete the authority item or force-clear live (non-terminal) slots.

Additionally:
- Measure the slow step: add `backend_first_text_seconds` and per-pass latency for delegated speech work, plus a fixed `error_code` for budget expiry (`work_deadline_exceeded` instead of `model_unavailable`). This separates "model unavailable" from "budget exhausted" in the logs.
- Make refused admissions observable: log `test.admission failure` with `reason=manager_busy|capacity_unavailable`, so a lockout is visible in monitoring without access logs.
- Make the console message actionable: when the refusal is `manager_busy` and no session exists in this tab, say that an earlier test of yours is still being cleaned up, rather than "Busy".

**Alternatives:**
- Raise the work budget for speech delegation, for example to 20 s for reasoning while the live model holds the turn. This reduces how often the failure happens but does not remove the session-ending behavior or the lockout, and it touches documented capacity targets (`docs/runbooks/scaling.md`).
- Let the same actor start a new session while their old slot is `cleanup_pending`. This removes the user-visible lockout but lets stuck slots accumulate until the global 5-slot capacity is exhausted. Only acceptable together with part 2.

**Files likely to change:**
- `code/backend/src/caller_runtime/services/conversation.py`
- `code/backend/src/caller_runtime/adapters/foundry_text.py`
- `code/backend/src/caller_runtime/services/cleanup.py`
- `code/backend/src/caller_runtime/services/admission.py`
- `code/backend/src/caller_runtime/observability.py` (`test.admission` reasons)
- `code/management/management_app/components/live_audio_js.py` (message)
- `docs/runbooks/scaling.md`, `docs/runbooks/monitoring.md`, `docs/conversation-endpoints.md`
- Tests: `code/backend/tests/unit/test_conversation.py`, `code/backend/tests/unit/test_admission.py`, `code/backend/tests/integration/test_conversation.py`

**Tests to add or update:**
- A speech session whose delegated work item exceeds its budget (fake model that sleeps past the deadline):
  - the session stays open;
  - the live model receives the "could not complete" context;
  - the next delegation runs;
  - `test.work failure` is recorded.
- After such a timeout and client disposal, the model attempt is settled; at session stop the cleanup evidence verifies and the slot is released.
- Regression for #3: after a session that ended with a timed-out model attempt, the same actor can start a new text and speech session.
- A refused admission logs `test.admission failure` with `reason=manager_busy`.
- The existing guarantees still hold: no attempt is ever replayed; a live (speech) attempt without a closure receipt still keeps its slot; a definite provider refusal still settles.

## Risks & Considerations

- **Security and cost (part B.2):**
  - Treating an abandoned model request as settled ends the guarantee that unknown provider outcomes hold capacity.
  - The change must be limited to `model` attempts whose client is confirmed disposed. `live` attempts without a closure receipt and capability calls with side effects must keep the current rule.
  - It needs the same review as the original cleanup design (`docs/runbooks/configuration.md`, `docs/runbooks/scaling.md`).
- **Recovery of the existing stuck slot:** acting on the shared int Cosmos authority affects a shared environment and needs the owner's confirmation. Use a preview first; never delete the authority item.
- **Image change:** `apk add --upgrade` makes the image depend on the Alpine mirror state at build time. The `>=` pin keeps it deterministic enough and matches the existing `curl` pattern; revisit when the base digest is bumped.
- **Frontend audit exception expires soon:** the minimatch exception `GHSA-g3hv-75wg-m249` in `code/dependency-mirror-gaps.yaml` expires 2026-11-17T09:25:22Z. After that, the same CI audit step fails again unless it is renewed or Reflex's dependency is updated.
- **Log noise in live runtime logs:** the int runtime console log contains Azure SDK HTTP request and response header lines (Cosmos, at INFO), despite the documented intent that library request logs stay at WARNING. Authorization values are redacted, but this is noisy and costs ingestion. It is out of scope here and worth a separate bug.
- **Delegation clarity failures:** the 5 `invalid_input` outcomes before the crash are the clarity check rejecting fragments. They are expected behavior, but each costs a model call inside the same budget.

## Decisions (owner, 2026-11-12)

**Reasoning effort**
- Today no call sets a reasoning effort. The agents (`services/conversation_agents.py`) and the text adapter (`adapters/foundry_text.py`, `_settings`) pass none, so every call to `gpt-6.1-sol` (version `2026-09-29`) uses the model default.
- Set the effort explicitly per call type:
  - clarity check: `low`, or the lowest the model offers;
  - spoken requests the voice agent hands off: `low` by default;
  - text test turns: the model default, or `medium`.
- For each delegated step, log the reasoning tokens and the time to the first reply text (`backend_first_text_seconds`), so it is visible how much of the budget reasoning uses.

**Work budget**
- Spoken requests the voice agent hands off get a budget of about 30 s total, configurable. The fixed 10 s no longer applies to them.
- After about 5 s, the runtime tells the voice agent to say a short holding line ("one moment, I'm checking that"), so the caller doesn't hear silence.
- Session start and readiness keep 10 s, the documented admission targets.
- The 600-second session limit and the 120-second idle limit stay as they are; the idle limit keeps resetting while the caller speaks.
- Update the documented capacity targets in `docs/runbooks/scaling.md` and the tests that assert the 10-second work budget.

**A model request that outlasts its budget**
- The runtime stops waiting for it and the voice agent tells the caller it couldn't finish; the session continues.
- The request keeps running in the background until Foundry ends it, up to a hard limit of about 60 s. Its definite outcome (completed, incomplete or failed) is then recorded, the late reply is discarded, and cleanup can release the slot.
- Only a request that also exceeds the hard limit is treated as finished once its connection is confirmed closed. This bounded fallback is limited to `model` attempts; `live` attempts without a closure receipt and capability calls keep the current rule.

**Recovery of the slot already stuck on int**
- A reviewed, idempotent one-off script with a preview mode that changes nothing.
- It releases only terminal slots whose session has expired and whose only unsettled attempts are `model` attempts. It never deletes the authority item or touches live slots.
- It runs against int only after the owner's explicit OK.

## Open Questions

- None open. Both former questions were checked on 2026-11-12; see "Verified facts" below.
- The Windows native-checks job of run 43204396551 was still running when the logs were read; its result is not part of this assessment.

## Verified facts (2026-11-12)

**Base images**
- The upstream `python:3.12.15-alpine3.23` tag still resolves to the pinned digest `sha256:3d0ca66b…` (Alpine 3.23.7) and still contains `krb5-libs` 1.22.1-r0. No rebuilt upstream image exists yet, so a digest bump is not possible today.
- The Alpine v3.23 `main` repository already serves `krb5-libs` 1.22.3-r0. `apk add --no-cache --upgrade 'krb5-libs>=1.22.3-r0'` installs it cleanly in that image.
- Use the `apk` upgrade pin, and switch to a newer base digest once upstream rebuilds.
- `node:24.22.0-alpine3.23` (pinned digest unchanged) has no `krb5` packages. The management Dockerfile copies only the `node` binary from it, so no change is needed there.

**Default reasoning effort**
- Checked with two minimal Responses calls (`store=false`, 256-token cap) from inside the int runtime container. The Foundry account has public network access disabled, so the container is the only route.
- `gpt-6.1-sol` (2026-09-29) with no effort set reports `reasoning.effort = "medium"`, `reasoning.context = "all_turns"` and `reasoning.mode = "standard"`; the call took 2.87 s.
- The same call with `effort = "low"` took 1.52 s. Both trivial prompts used 0 reasoning tokens.
- So every runtime call today, including the clarity check and speech delegations, runs at `medium`.
- The decision stands: `low` for the clarity check and spoken requests. Text test turns stay on the default (`medium`) unless measurements say otherwise.
