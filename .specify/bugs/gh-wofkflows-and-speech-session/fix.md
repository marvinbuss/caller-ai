# Bug Fix: Image publish and CI fail on HIGH krb5 CVEs; a slow delegated model step ends speech sessions and locks the manager out of new tests

- **Slug**: gh-wofkflows-and-speech-session
- **Fixed**: 2026-11-12
- **Assessment**: ./assessment.md
- **Status**: applied

## Summary

The fix has two parts:

- **Images (root cause A).** Every Python Alpine stage that the image scan checks now upgrades `krb5-libs` to `>=1.22.3-r0`. This fixes the 16 HIGH CVEs that failed "Publish Images" and the Foundation CI image job.
- **Slow model step (root cause B).**
  - Speech delegations get low reasoning effort, a 30-second work budget and a 5-second holding line.
  - When a single work item fails, only that item ends; the session stays open.
  - A model request that outlives its budget finishes in the background, for at most 60 s, and settles. Cleanup then releases the slot, so the manager is no longer locked out.
  - A one-off recovery script, with a preview mode, can release slots that were held before this fix.

## Changes

| File | Change | Notes |
|------|--------|-------|
| `code/backend/Dockerfile` | modified | Final stage: `RUN apk add --no-cache --upgrade 'krb5-libs>=1.22.3-r0'` |
| `code/management/Dockerfile` | modified | Same line in the build stage and the final stage |
| `code/tools/tests/test_static_checks.py` | modified, added test | Per-stage apk parser; both Dockerfiles must pin krb5 in every Python Alpine stage; curl test kept |
| `code/backend/src/caller_runtime/domain/conversation.py` | modified | `WORK_SECONDS=10`, `SPEECH_WORK_SECONDS=30`, `MAX_WORK_SECONDS=60` |
| `code/backend/src/caller_runtime/services/delegation.py` | modified | `DelegationQueue(speech_work_seconds=…)`: speech items get the speech budget; text keeps 10 s |
| `code/backend/src/caller_runtime/services/admission.py` | modified | `_business` accepts work spans up to `MAX_WORK_SECONDS`; per-store-operation deadlines stay at 10 s |
| `code/backend/src/caller_runtime/composition.py` | modified | `RuntimeOptions.speech_work_seconds` (default 30, 10–60) passed to the executor. A refused start (`manager_busy` or `capacity_unavailable`) logs `test.admission failure denied` and `test.admission.failure` with the code |
| `code/backend/src/caller_runtime/services/conversation_agents.py` | modified | `build_agents(..., reasoning_effort="low" \| None)` → `ModelSettings(reasoning=Reasoning(effort=…))` |
| `code/backend/src/caller_runtime/services/conversation.py` | modified | See the list below |
| `code/backend/src/caller_runtime/adapters/foundry_text.py` | modified | See the list below |
| `code/backend/src/caller_runtime/observability.py` | modified | `test.usage` measurement `reasoning_tokens`; `model_usage(..., reasoning_tokens=)` |
| `code/backend/src/caller_runtime/recovery.py` | added | `python -m caller_runtime.recovery [--apply]`. Preview is the default. It releases only fenced slots that expired at least 5 minutes ago and whose only unsettled attempt is `model`. It is idempotent and never deletes the authority item |
| `code/tools/scripts/check_modules.py` | modified | `caller_runtime.recovery` is registered as an entry module, so the reachability hook passes |
| `code/management/management_app/components/live_audio_js.py` | modified | `manager_busy` → "Another test is active or an earlier test is still being cleaned up" |
| `docs/runbooks/scaling.md` | modified | Work budget row, new "model request past its budget" row, recovery runbook row |
| `docs/runbooks/monitoring.md` | modified | Admission denials, and which `test.work.failure` codes keep the session |
| `docs/conversation-endpoints.md` | modified | Usage and latency measurements; new "Model settings" section (effort and budgets) |
| `code/backend/tests/unit/test_admission.py` | modified, added test | See "Tests Added or Updated" |
| `code/backend/tests/unit/test_conversation.py` | modified, added test | See "Tests Added or Updated" |
| `code/backend/tests/unit/test_recovery.py` | added | Recovery script test |
| `code/backend/tests/integration/test_conversation.py` | modified | A refused start is logged |
| `code/management/tests/unit/test_test_console.py` | added test | Console message |

Changes in `code/backend/src/caller_runtime/services/conversation.py`:

- Low reasoning effort for the clarity check and for speech work.
- A holding line ("One moment, I'm checking that.") after `HOLDING_SECONDS=5`.
- Bounded per-item failures (`model_unavailable`, `work_deadline_exceeded`, `work_busy`, `TimeoutError`):
  - report `work_unavailable` with `cleanupStatus: "active"`;
  - tell the live model "I could not finish that, please try again.";
  - keep the session open.
- An expired pending item is dropped without closing the session.
- `close()` retries cleanup once a detached model request has finished.

Changes in `code/backend/src/caller_runtime/adapters/foundry_text.py`:

- Model requests run in their own task (in `_attempt`) or pump task (in `_stream`). That task settles the attempt itself on a definite outcome: completed, incomplete or failed, or a 4xx refusal.
- When the caller stops waiting (budget, cancellation, early close), the request is detached into a drain capped at `MODEL_REQUEST_CAP_SECONDS=60`. Past the cap, the request is cut and settles as `model-abandoned-<id>` only after client disposal is confirmed.
- A budget timeout now raises `work_deadline_exceeded` instead of `model_unavailable`.
- New `draining` and `drained()` members.
- Client timeouts are now the cap.
- `close()` leaves the clients of detached requests open until their drain ends.

## Diff Highlights

Detached model request (`foundry_text.py`):

```python
async def request() -> Reply:
    # Settles in this task, so a reply that lands after the budget still counts.
    try:
        result = await call(opened)
    except APIStatusError as error:
        await self._settle_refusal(recorded, error)
        raise
    await self._settle(recorded)
    return result
...
finally:
    if dispatched and not dispatched[0].done() and client is not None:
        self._detach(dispatched[0], client, intent, cap)  # drain up to 60 s, then abandon
```

Bounded failure keeps the session (`conversation.py`):

```python
ending = not isinstance(error, TimeoutError) and code not in BOUNDED_WORK_FAILURES
if ending:
    self._failed = True
...
await self._work_error(emit, item.receipt.task_id, active=not ending)
if not ending:
    await self._tell_live(item, WORK_FAILED_LINE)
```

## Tests Added or Updated

- `code/tools/tests/test_static_checks.py::test_python_alpine_stages_upgrade_patched_krb5_libs`: three cases (backend final, management build, management final). Each stage must carry `apk add --no-cache --upgrade 'krb5-libs>=1.22.3-r0'`.
- `code/tools/tests/test_static_checks.py::test_management_build_stage_requires_patched_curl_and_libcurl`: rewritten on the per-stage parser.
- `code/backend/tests/unit/test_admission.py::test_foundry_text_slow_request_finishes_in_background_and_settles_once`: four cases (`stream` × `late`/`cap`). In each case:
  - the caller gets `work_deadline_exceeded`;
  - the attempt stays unsettled while the request runs;
  - `close()` does not dispose the client;
  - a late answer settles with `model-…`, and past the cap it settles with `model-abandoned-…`;
  - exactly one request is sent (no replay).
- `code/backend/tests/unit/test_admission.py::test_foundry_text_stream_is_gated_and_early_close_settles_only_after_provider_finishes`: renamed. After early close, the attempt settles only once the provider finishes.
- `code/backend/tests/unit/test_admission.py::test_foundry_text_settles_only_a_definite_provider_refusal`: unchanged and still passes. It confirms that 4xx settles and 5xx does not.
- `code/backend/tests/unit/test_conversation.py::test_slow_speech_delegation_holds_then_fails_alone_and_next_delegation_runs`. Covers:
  - the holding line;
  - the "could not finish" line;
  - the session stays open and `test.work failure` is recorded;
  - the late reply still settles;
  - the next delegation runs and replies;
  - every request uses `reasoning.effort=low`.
- `code/backend/tests/unit/test_conversation.py::test_text_stop_clears_volatile_context_but_only_definitive_work_releases`. Covers stop during an in-flight request:
  - the slot stays held and the same manager gets `manager_busy` (regression for #3);
  - after the request finishes, the late cleanup releases the slot and the manager can reserve again.
- `code/backend/tests/unit/test_conversation.py::test_failed_work_keeps_session_and_never_dispatches_expired_queue`: replaces `test_failed_work_purges_pending_input_and_never_dispatches_expired_queue`. An expired work item reports `work_unavailable` with `cleanupStatus: "active"`, the session stays open, and the expired pending item is dropped without being sent.
- `code/backend/tests/unit/test_conversation.py::test_correlated_fragments_need_shared_backend_clarity_before_any_actions`:
  - the speech budget is 30 s;
  - the clarity check and speech work use low effort;
  - client timeouts equal the 60-second cap.
- `code/backend/tests/unit/test_recovery.py::test_recovery_releases_only_expired_fenced_slots_held_by_an_unsettled_model_request`:
  - nothing is released before expiry;
  - preview writes nothing (the etag is unchanged);
  - apply releases only the fenced, model-only slot, leaving the live slot and the slot with an unsettled capability attempt;
  - a second apply changes nothing;
  - the actor no longer holds a slot.
- `code/backend/tests/integration/test_conversation.py` (composed runtime, `ready` outcome): a second start by the same manager raises `manager_busy` and logs `test.admission failure denied` plus `test.admission.failure` with `error_code=manager_busy`.
- `code/management/tests/unit/test_test_console.py::test_manager_busy_explains_that_an_earlier_test_may_still_be_cleaning_up`.

## Local Verification

- Backend in `code/backend`:
  - `ruff format src tests`: no changes.
  - `ruff check src tests`: passed.
  - `mypy src`: no issues in 40 files.
  - `pytest tests -q -p no:cacheprovider --deselect tests/integration/test_foundation_recovery.py`: 759 passed, 6 deselected.
- Flakiness check: the new and changed adapter, conversation and recovery tests ran 5 times in a row, 16 passed each time.
- Tools in `code/tools`:
  - `ruff format`/`ruff check tests scripts`: passed.
  - `pytest tests/test_static_checks.py`: 119 passed.
  - Full `pytest tests`: 497 passed, 4 skipped. This run was before the build-stage krb5 line was added; `test_static_checks.py` was re-run afterwards.
- Management in `code/management`:
  - `ruff format`/`ruff check management_app tests`: passed.
  - `pytest tests/unit tests/contract tests/integration -m "not browser" --deselect tests/integration/test_foundation_recovery.py`: 1050 passed.
  - `reflex.lock` was restored afterwards.
- `pre-commit` on the changed files:
  - lint, format, whitespace, module reachability, secrets and publication guard passed;
  - the two type hooks failed locally only because the hatchling download hit a TLS handshake failure. `mypy src` was run directly instead (backend).
- Not run locally:
  - the Trivy image gates ("Publish Images" and `audit_dependencies.py --images`) and image builds; they need Docker and network and run in CI;
  - the recovery script against int. It needs the owner's explicit OK.
- Manual checks: none. The user asked for automated tests only.

## Deviations from Assessment

- **B.2 settlement rule.** The assessment's first sketch was to settle any timed-out model attempt once its client is disposed. As the owner decided on 2026-11-12, the request instead finishes in the background up to 60 s and settles on its definite outcome; the abandon-on-disposal rule applies only after that cap. `services/cleanup.py` did not need changes: settlement goes through the existing `SettlementObserver` path.
- **Session lock during model calls.** `admission.perform` now starts the model request as a task and returns at once. Before, it held the session lock for the whole HTTP request. Dispatch-once is unchanged: the attempt is marked dispatched before the task starts.
- **Early stream close settles.** An early consumer close no longer leaves the attempt unsettled for good. The detached stream reads to the provider's end and settles on a definite outcome. The existing test now pins this new behavior instead of "never settles".
- **5xx and transport errors.** A 5xx or transport error before the cap still leaves the attempt unsettled, as before; only a timeout or cancel gets the drain and cap. The owner decision covered slow requests only.
- **Speech budget setting.** `speech_work_seconds` is a `RuntimeOptions` field with default 30 (10–60). It is not wired to an environment variable or Terraform yet.
- **Admission denial reason.** A refused admission logs `test.admission` with the existing fixed reason `denied`, and the specific code (`manager_busy` or `capacity_unavailable`) in the `test.admission.failure` diagnostic. No new `TEST_REASONS` value was added.
- **Error vocabulary.** Bounded failures send `cleanupStatus: "active"` on the existing `work_unavailable` error, so the browser console keeps the session (it already handles `active` as non-terminal).
- **Scope expansion.**
  - `code/tools/scripts/check_modules.py`: registers the new recovery module as an entry point.
  - `services/delegation.py`, `domain/conversation.py` and `composition.py`: budget constants and wiring.

## Follow-ups

- **Int recovery** (needs the owner's explicit OK). Inside `ca-caller-int-runtime`, first run `python -m caller_runtime.recovery` to preview. It should list session `1da50cd100bb40e1a5094c8182468622`. Then run it again with `--apply`.
- **CI re-runs.** After merge, re-run "Publish Images" and the Foundation CI "Linux image health and browser smoke" job. Drop the krb5 lines once the pinned `python:3.12.15-alpine3.23` digest is bumped to an image that ships `krb5-libs` 1.22.3-r0.
- **Expiring audit exception.** The minimatch exception (GHSA-g3hv-75wg-m249) expires 2026-11-17T09:25:22Z. Renew it or update the dependency before then.
- **Configuration.** Consider wiring `speech_work_seconds` to an environment setting if it needs tuning per environment.
- **Unsettled 5xx and transport errors.** Consider whether those attempts should get the same cap-and-abandon rule. Today they still hold the slot until manual recovery.
- **Log noise.** Separate bug: the Azure SDK logs HTTP header lines at INFO in the int runtime logs.
