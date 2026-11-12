# Bug Verification: New management image cannot start on int: revisions stored by the previous release fail the integrity check

- **Slug**: failed-management-redesign-startup
- **Tested**: 2026-11-12
- **Assessment**: ./assessment.md
- **Fix**: ./fix.md
- **Result**: partial

## Summary

The fix holds everywhere it could be tested. No regressions were found.

- **Local reproduction:** the assessment's local reproduction no longer fails. A schema-2.0.0 revision without `routable` is accepted, and a tampered revision is still rejected.
- **Int data:** inside the running int management container, the active revision's stored hash matches its graph as stored, which is the form the fix now accepts. It does not match the normalized-with-`routable` form that caused the failure.
- **Test suites:** the fix's tests and the full non-browser management suite pass.

The result is `partial` because the fixed management image has not been built or started on int yet. The original symptom, the container failing to start, was not re-run end to end.

## Checks Performed

| Check | Command / Action | Result | Notes |
|-------|------------------|--------|-------|
| Reproduction (local, post-fix) | The assessment's script: a synthetic schema-2.0.0 revision passed to `verified_revision`, plus a tamper case | pass | No `routable` → accepted (was `invalid_snapshot`); with `routable` → accepted; tampered after hashing → `invalid_snapshot` |
| Fix rule against real int data | Read-only `az containerapp exec` into `ca-caller-int-management--0000021`. On the active revision, compare `graphHash` with the hash of the stored graph fields and with the normalized form (`routable: true` added). Only booleans printed | pass | `stored_graph_hash_matches True normalized_with_routable_matches False`. The fix accepts int's revision, and the stored form survives the Cosmos round trip unchanged |
| New tests | `pytest tests/integration/test_configuration_persistence.py tests/integration/test_live_composition.py -k "earlier_release or previous_release or non_routable or startup_failure or tampered"` | pass | 7 passed. An earlier run with the repository fix stashed showed the two int-shape tests fail without it (see fix.md) |
| Regression suite | `pytest tests/unit tests/contract tests/integration -m "not browser" --deselect tests/integration/test_foundation_recovery.py` (`code/management`) | pass | 1056 passed, 6 deselected. `reflex.lock` was restored afterwards |
| Lint and type checks | `ruff format --check`, `ruff check`, `mypy management_app` | pass | 114 files already formatted; no lint or type findings |
| Whitespace | `check_whitespace.py` on the changed files | pass | Exit 0 |
| Docs check | `verify_docs.py` | skipped | It renders every Mermaid diagram (more than 15 minutes locally). The change adds one paragraph with no diagram; CI runs it |
| Fixed image starts on int | Build and publish the image, set the management tag, apply | not-run | The fix is not committed, merged or published, and deploying would change shared infrastructure. Local image builds cannot reach PyPI from this workstation |
| Browser tests | `pytest -m browser` | skipped | Not on the changed paths |

## Output Excerpts

```text
old writer, no routable -> accepted
same graph with routable -> accepted
old writer, tampered after hashing -> ValueError invalid_snapshot
```

```text
# int, active revision (read-only, inside --0000021)
stored_graph_hash_matches True normalized_with_routable_matches False
```

```text
7 passed, 39 deselected, 1 warning in 12.67s
1056 passed, 6 deselected, 3 warnings in 276.79s (0:04:36)
Success: no issues found in 66 source files
```

## Residual Risks

- **Not deployed yet:** the fixed image has not run on int. The in-container check proves the hash rule against the real document. That the new `ConfigurationRevision` model accepts the document is inferred, not tested on int: it accepts schema `2.0.0`, the previous model accepted the same document, and 005 only added optional fields.
- **Other startup steps on int:** steps after `bootstrap` (Reflex app construction, the new status and auth routers) have not been exercised against int. If one of them fails, the new `management.startup.failure` log line will now name its fixed codes.
- **Earlier revisions:** older revisions in int's history were not checked one by one. They were written by the same release, so the same rule applies.
- **Local tfvars change:** the uncommitted `int.tfvars.json` edit still moves both images to `sha-817ba2ba…`. Applying it before the fixed management image exists would fail again for management.

## Recommendation

Hold, then close once deployed. Commit and merge the fix and let CI publish the management image. Then set only the management tag in `code/infra/environments/int/int.tfvars.json` to the new image and apply. Close the bug when the new management revision reaches `Healthy` on int and `/readyz` returns 200. If it fails again, read the `management.startup.failure` log line and re-run `/speckit-bug-assess` with those codes.
