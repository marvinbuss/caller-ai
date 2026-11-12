# Bug Fix: After the redesign deploy, the Test, Settings and Community and Agent pages fail on int: verified revisions are verified a second time and rejected

- **Slug**: management-redesign-bugs
- **Fixed**: 2026-11-12
- **Assessment**: ./assessment.md
- **Status**: applied

## Summary

`verified_revision` now returns the revision in its stored shape (`model_dump(..., exclude_unset=True)`) instead of adding model defaults such as `routable: true`. Verifying its own output again is therefore idempotent, and the two endpoints that re-verify the repository's snapshot no longer reject revisions published before 005:

- `GET /v2/configuration` (Community and Agent page, Settings page, editor revision check);
- `GET /v2/runtime/configuration` (the runtime's configuration refresh before every test start).

## Changes

| File | Change | Notes |
|------|--------|-------|
| `code/management/management_app/adapters/configuration_repository.py` | modified | `verified_revision` dumps with `exclude_unset=True`; the PR #42 two-form hash check is unchanged |
| `code/management/tests/integration/test_configuration_persistence.py` | updated test | The earlier-release test also checks the stored shape, idempotent re-verification, `revision_response` (200) and `runtime_projection` |
| `code/management/tests/integration/test_live_composition.py` | updated test | After a start over an int-shaped store, `GET /v2/runtime/configuration` with a runtime token returns 200, with no `routable` added |
| `code/management/tests/contract/test_configuration_api.py` | added test | `GET /v2/configuration` serves a revision published before `routable` existed, given the repository's verified snapshot |

## Diff Highlights

```python
# configuration_repository.py, verified_revision
model = ConfigurationRevision.model_validate_json(encoded(clean))
# Keep the stored shape: added defaults (routable) would break re-verifying this output.
snapshot = model.model_dump(mode="json", by_alias=True, exclude_unset=True)
```

## Tests Added or Updated

- `test_configuration_persistence.py::test_revisions_from_earlier_releases_still_load_and_bootstrap`, both cases:
  - the active snapshot keeps the communities as stored;
  - `verified_revision(active) == active`;
  - `revision_response((active, etag))` is 200;
  - the runtime projection keeps the stored communities;
  - tampering is still rejected.
- `test_live_composition.py::test_a_start_over_a_revision_from_the_previous_release_succeeds`: the live composition over a schema-2.0.0 store serves `/v2/runtime/configuration` with 200 and leaves the store unchanged.
- `test_configuration_api.py::test_get_serves_a_revision_published_before_routable_existed`: `GET /v2/configuration` returns 200 with the communities as stored.

## Local Verification

- **Tests catch the bug:** with the `configuration_repository.py` change stashed, the three tests above fail; the explicit-`routable` case passes in both states. With the change, all 4 selected tests pass.
- **Cross-service check (one-off, not a committed test):** management `runtime_projection(verified_revision(active))` for an earlier-release and for a current revision. The backend's `RevisionSnapshot.validate` accepts both, and `routable()` is `True` for every community:

  ```text
  earlier -> accepted by runtime; routable of ['community-alpha', 'community-beta'] = [True, True]
  current -> accepted by runtime; routable of ['community-alpha', 'community-beta'] = [True, True]
  ```

- **Checks in `code/management`:**
  - `ruff format`: 114 files unchanged;
  - `ruff check`: passed;
  - `mypy management_app`: no issues in 66 files.
- **Full suite in `code/management`:** `pytest tests/unit tests/contract tests/integration -m "not browser" --deselect tests/integration/test_foundation_recovery.py` → 1057 passed, 6 deselected. `reflex.lock` was restored afterwards.
- **Whitespace:** `check_whitespace.py` on the changed files passed (exit 0).

## Deviations from Assessment

- **Cross-service test.** The assessment suggested a committed test that feeds the management projection into the backend validator. Management and backend are separate projects with separate environments, and no existing test spans both, so I ran it as a one-off local check instead (output above). Adding a shared fixture is listed under Follow-ups.
- **Other API callers.** The PR #42 two-form hash check and the second verification in both API layers stay as they are, as the assessment preferred.

## Follow-ups

- **Deploy:** build and publish the management image from this fix, set the management tag in `code/infra/environments/int/int.tfvars.json`, and run the protected int apply. The runtime does not need a new image.
- **Cross-service drift:** consider a contract fixture under `code/contracts/` that management writes, as a runtime projection of an earlier-release revision, and the backend tests read, so drift between the two is caught in CI.
