# Bug Verification: After the redesign deploy, the Test, Settings and Community and Agent pages fail on int: verified revisions are verified a second time and rejected

- **Slug**: management-redesign-bugs
- **Tested**: 2026-11-12
- **Assessment**: ./assessment.md
- **Fix**: ./fix.md
- **Result**: partial

## Summary

The fix holds everywhere it could be tested. No regressions were found.

- **Real int data:** inside the deployed int management container (`sha-11f91d4…`), the deployed check rejects the active revision on the second verification (`invalid_snapshot`), exactly as on int. The fixed rule verifies the same document twice, idempotently, and builds the runtime projection.
- **Local reproduction:** the assessment's local reproduction now returns 200 for both endpoint paths.
- **Runtime contract:** the runtime accepts management's projection for both an earlier-release and a current revision.
- **Test suites:** the fix's tests and the full non-browser management suite pass.

The result is `partial` because the fixed image is not built or deployed. The three symptoms (Test, Settings, Community and Agent pages) were not re-checked on int itself.

## Checks Performed

| Check | Command / Action | Result | Notes |
|-------|------------------|--------|-------|
| Bug and fix on real int data | Read-only `az containerapp exec` into `ca-caller-int-management--0000023`. The active revision went through the deployed `verified_revision` once and twice, then through the fixed rule (`exclude_unset` dump, two-form hash), which was defined in the script itself. Only outcomes were printed | pass | Deployed: once `ok`, twice `ValueError: invalid_snapshot` (the int failure). Fixed: once and twice `ok`, idempotent `True`, projection `ok`, no `routable` added |
| Reproduction (local, post-fix) | The assessment's script: an earlier-release revision through `read_active`, then `verified_revision`, `revision_response` and `runtime_projection` | pass | `GET /v2/configuration` path → 200; runtime projection path → ok. Before the fix both raised `invalid_snapshot` |
| Runtime accepts the projection | Management projection of an earlier-release and a current revision, validated by the backend's `RevisionSnapshot.validate` | pass | Both accepted; `routable()` is `True` for every community |
| New and updated tests | `pytest tests/integration/test_configuration_persistence.py tests/integration/test_live_composition.py tests/contract/test_configuration_api.py -k "earlier_release or previous_release or before_routable or tampered"` | pass | 5 passed. The fix step showed that 3 of them fail without the change |
| Regression suite | `pytest tests/unit tests/contract tests/integration -m "not browser" --deselect tests/integration/test_foundation_recovery.py` (`code/management`) | pass | 1057 passed, 6 deselected. `reflex.lock` was restored afterwards |
| Lint and type checks | `ruff format --check`, `ruff check`, `mypy management_app` | pass | 114 files already formatted; no findings |
| Symptoms on int (Test, Settings, Community and Agent pages) | Deploy the fixed management image and use the pages | not-run | The fix is not committed or published, and deploying changes shared infrastructure. Browser checks need a signed-in manager |
| Browser tests | `pytest -m browser` | skipped | Not on the changed path |

## Output Excerpts

```text
# int, inside --0000023 (read-only)
deployed: once ok twice ValueError:invalid_snapshot
fixed: once ok, twice ok idempotent True projection ok routable_keys 0
```

```text
earlier GET /v2/configuration revision_response -> 200
earlier GET /v2/runtime/configuration projection -> ok
earlier -> runtime accepts projection; routable [True, True]
```

```text
5 passed, 65 deselected, 1 warning in 9.74s
1057 passed, 6 deselected, 3 warnings in 250.30s (0:04:10)
Success: no issues found in 66 source files
```

## Residual Risks

- **Not checked on int:** the in-container check used the fixed rule defined inside the script, not the packaged code, and the pages themselves were not exercised on int. The new image is needed to confirm end to end.
- **Runtime refresh after deploy:** once the fixed management serves `/v2/runtime/configuration`, the runtime's next refresh, which happens before each start, should pick up the configuration. No runtime change is needed.
- **Other fields omitted from older revisions:** `exclude_unset` leaves out any field a stored revision did not contain. Every consumer found reads `routable` with a default of true; other older-revision fields were not checked one by one.
- **Cross-service drift:** the management-to-runtime check is a one-off, not a committed test (see fix.md "Follow-ups").

## Recommendation

Hold, then close once deployed. Commit and merge the fix, then set the new management image tag in `code/infra/environments/int/int.tfvars.json` and run the protected int apply. Close the bug once all of the following hold on int:

- the Community and Agent and Settings pages load;
- a text and a speech test start;
- the runtime logs no more `test.admission.failure` with `configuration_unavailable`.
