# Bug Assessment: After the redesign deploy, the Test, Settings and Community and Agent pages fail on int: verified revisions are verified a second time and rejected

- **Slug**: management-redesign-bugs
- **Created**: 2026-11-12
- **Source**: pasted text
- **Verdict**: valid (reproduced locally; confirmed in int logs)
- **Severity**: high

## Report (verbatim)

> when opening the management portal, I am having two issues:
> 1. Test blade not working: Speech and Text messages are both broken with messages like `Unavailable` or `cleanup pending`.
>
> 2. Main Settings not working: When I open the settings blade, I just see `Settings unavailable`.
>
> 3. Configuration unavailable: When I open the community and agent blade, it just shows configuration unavailable.

### Evidence gathered (read-only)

- **Deployment.** Both int apps run `sha-11f91d467f31e0edba6288fe45a1f140c3b07c21`, the merge commit of PR #42, the startup fix. They have run since 14:59 UTC and both are Healthy at 100 % traffic:
  - `ca-caller-int-management--0000023`
  - `ca-caller-int-runtime--0000024`
- **Management events** (Log Analytics `log-caller-int`, `ContainerAppConsoleLogs_CL`, revision `--0000023`):
  - startup success, sign-in success;
  - `ui.page_view` for test, settings, status and communities, all `shown`;
  - `status.check success text=reachable voice=reachable`: the Status page works;
  - `test.relay success text ready`, then `test.relay failure speech unavailable` with `test.relay.failure error_class=BackendRefusal browser_close=1011`;
  - from 15:00 on, `event: request, outcome: failure` lines every 15–30 s: the editor's background revision check and the runtime's configuration refresh.
- **Runtime events** (revision `--0000024`):

  ```text
  15:01:47 test.admission failure kind=text  reason_code=unavailable
  15:01:47 test.admission.failure kind=text  error_class=AdmissionError error_code=configuration_unavailable
  15:02:05 test.admission.failure kind=text  error_class=AdmissionError error_code=configuration_unavailable
  15:02:15 test.admission.failure kind=voice error_class=AdmissionError error_code=configuration_unavailable
  ```

  The runtime cannot get the configuration from management, so every text and speech start is refused.

## Symptom

On int, after the PR #42 deploy:

- the Community and Agent page shows "Configuration unavailable";
- the Settings page shows "Settings unavailable";
- every text or speech test fails: text shows "Unavailable", and speech ends with close 1011 and "cleanup pending".

Expected: all three pages load the active configuration and tests start. The Status page works, and management itself starts.

## Reproduction

1. Have an active revision written by a release before 005 (schema `2.0.0`, communities without `routable`). Int's active revision 3 is one.
2. Start management at `11f91d4`. Startup succeeds: `bootstrap` and `read_active` accept the revision through the PR #42 rule.
3. Call `GET /v2/configuration` (used by the Community and Agent and Settings pages and the editor's background check) or `GET /v2/runtime/configuration` (used by the runtime before every test start). Both answer 503.

Local reproduction at HEAD (`11f91d4`), using the PR #42 test helper to store a revision in the earlier-release shape:

```text
read_active: ok
validator issues -> []
verified_revision(snapshot) -> ValueError invalid_snapshot
GET /v2/configuration revision_response -> ValueError invalid_snapshot
GET /v2/runtime/configuration projection -> ValueError invalid_snapshot
```

## Suspected Code Paths

- `code/management/management_app/adapters/configuration_repository.py`, `verified_revision()`:
  - It returns `model.model_dump(...)`, which adds `routable: true` to every community that lacked it.
  - Since PR #42 it accepts a stored hash that matches either the stored graph or the normalized graph. Its own output fits neither form: the dump is normalized, but it is not the original stored document, and the stored hash was computed without `routable`. Verifying its own output a second time therefore fails.
- `code/management/management_app/adapters/configuration_repository.py`, `_active()` and `read_revision()`: they already return `verified_revision(document)`.
- `code/management/management_app/api/configuration.py`, `revision_response()`: it calls `verified_revision(snapshot)` again on the already-verified snapshot from `read_active()`. The resulting `ValueError` becomes `problem(503)`, so `GET /v2/configuration` fails for both the editor and the reader role.
- `code/management/management_app/api/runtime_configuration.py`, `configuration()`: it calls `runtime_projection(verified_revision(document))` on the snapshot from `read_active()`. The second verification fails and the endpoint answers 503 `configuration_unavailable`. The runtime's start-time refresh (`refresh(require_current=True)` in `caller_runtime/composition.py`, `start`) then refuses admission with `configuration_unavailable`.
- `code/management/management_app/state/configuration_editor.py`, `load()` and `check_revision()`, and `state/settings.py`, `load()`: a non-200 from `/v2/configuration` becomes "Configuration unavailable" and "Settings unavailable".
- `code/management/management_app/components/live_audio_js.py` and the test relay: a backend refusal shows "Unavailable" (text) and 1011 / "cleanup pending" (speech).

## Root Cause Hypothesis

**High confidence.** PR #42 made `verified_revision` accept a revision whose hash matches the graph as stored. That fixed startup, but the function still returns the normalized dump, which adds `routable: true`, and two API paths verify that returned snapshot a second time. For a revision published before 005, the normalized dump matches neither the stored hash (written without `routable`) nor itself as a stored form. So the second check raises `invalid_snapshot`:

- `GET /v2/configuration` answers 503, which breaks the Community and Agent and Settings pages;
- `GET /v2/runtime/configuration` answers 503, so the runtime cannot refresh its configuration and refuses every test start with `configuration_unavailable`. That breaks the Test page, for both text and speech.

The PR #42 tests covered startup, the repository reads and bootstrap, but not the HTTP endpoints over an earlier-release revision.

## Proposed Remediation

**Preferred: make `verified_revision` return the revision in its stored shape, so verifying it again is idempotent.**

1. In `verified_revision`, dump with `exclude_unset=True` (`model.model_dump(mode="json", by_alias=True, exclude_unset=True)`). Fields the stored document did not contain (`routable` on old communities, and an absent `conversationSettings` or `publication`) are then not added. Every consumer already reads `routable` with a default of true: graph view, editor draft, editor state, runtime model and runtime `routable()`. Revisions written by the current release keep `routable` explicitly, because they store it.
   - A local check of this change (monkeypatched, source unchanged) re-verified both an earlier-release and a current-release revision idempotently. The earlier-release revision kept `routable` absent; the current one kept it present.
   - Keep the PR #42 two-form hash check. It still matters for documents where model validation changes a value.
2. Keep the second verification in the API layers as defense in depth. It becomes a no-op for repository output once step 1 holds.

**Alternatives:**

- Remove the second `verified_revision` call from `revision_response` and `runtime_configuration`, because the repository has already verified. This is smaller, but the defense in depth is lost, and any other caller that re-verifies a snapshot would break the same way.
- Make `verified_revision` additionally accept the hash of the normalized graph with default-valued fields removed. That works, but it guesses at serialization and is harder to reason about than returning the stored shape.

**Files likely to change:**

- `code/management/management_app/adapters/configuration_repository.py` (`verified_revision`)
- Tests:
  - `code/management/tests/integration/test_configuration_persistence.py`: idempotence;
  - `code/management/tests/contract/test_configuration_api.py`: `GET /v2/configuration` over an earlier-release revision;
  - the runtime configuration API tests, e.g. `code/management/tests/contract/test_runtime_configuration*.py`, or `test_live_composition.py`, which already calls `/v2/runtime/configuration`: the same over an earlier-release store.

**Tests to add or update:**

- `verified_revision(verified_revision(doc)) == verified_revision(doc)` for an earlier-release revision (no `routable`, schema `2.0.0`) and for a current-release revision.
- `GET /v2/configuration` returns 200 with the revision and `warnings` for an earlier-release active revision, for both a manager and a reader.
- `GET /v2/runtime/configuration` returns 200, with a projection whose `policyHash` the runtime accepts (`RevisionSnapshot.validate` in the backend), for an earlier-release active revision. A test that feeds the management projection into the backend validator, or a fixture shared by both, would catch any future management/runtime contract drift here.
- Extend `test_a_start_over_a_revision_from_the_previous_release_succeeds` (live composition) to also call `/v2/runtime/configuration` with a runtime token and `/v2/configuration` with a manager token, expecting 200.
- Editing and publishing over an earlier-release revision produces a 2.1.0 revision with explicit `routable` that loads everywhere.

## Risks & Considerations

- **Runtime contract:** with `exclude_unset`, the runtime projection of an old revision omits `routable`, as the old management did before 005. The runtime treats an absent `routable` as true (`community.get("routable", True)` and its model default), and the runtime that was healthy on int before 005 received exactly that shape. Confirm with a cross-service test.
- **Editor display:** pages read `routable` through `.get(..., True)` or `setdefault`, so old communities show as routable; this is the current behavior.
- **Other defaults:** `exclude_unset` also omits any other field the stored revision did not contain, at every nesting level. That restores the stored shape the hash was computed over, which is the intent; check that no page indexes such a field directly. A search for `["routable"]` finds only the editor's own state rows, which are built with `.get`.
- **Int impact until fixed:** testing, configuration editing and settings are unavailable on int. No data is at risk: nothing is written, the stored revisions are intact, and Status still works.
- **Expired test slots:** failed starts were refused at admission, so no runtime slot was reserved. Nothing needs recovery.

## Open Questions

- None blocking.
