# Bug Fix: New management image cannot start on int: revisions stored by the previous release fail the integrity check

- **Slug**: failed-management-redesign-startup
- **Fixed**: 2026-11-12
- **Assessment**: ./assessment.md
- **Status**: applied

## Summary

`verified_revision` now accepts a revision whose stored `graphHash` matches either form of the graph:

- the graph as stored (as published);
- the graph re-serialized by the current model.

A revision published before 005 has communities without `routable`. Such revisions, like int's active revision 3, therefore load again, and the redesigned management app can start. A tampered revision still fails both checks.

A startup that collapses to `store_unavailable` now also logs the fixed error codes behind it, so the next failure can be diagnosed from the logs.

## Changes

| File | Change | Notes |
|------|--------|-------|
| `code/management/management_app/adapters/configuration_repository.py` | modified | `verified_revision` accepts the hash of the normalized graph or of the stored graph fields |
| `code/management/management_app/live.py` | modified | `failure_codes()` walks the exception chain and keeps only codes matching `[a-z][a-z0-9_]{0,63}`. `build_live_management` logs one `management.startup.failure` JSON line on the `management.startup` logger before raising `StartupError("store_unavailable")` |
| `docs/runbooks/monitoring.md` | modified | Documents the `management.startup.failure` line under "Diagnostic Events" |
| `code/management/tests/integration/test_configuration_persistence.py` | added tests | Earlier-release revisions load and bootstrap; non-routable round trip; `stored_by_earlier_release` helper |
| `code/management/tests/integration/test_live_composition.py` | added tests | Live start over a schema-2.0.0 store; startup failure logs only fixed codes |

## Diff Highlights

```python
# configuration_repository.py, verified_revision
# A later field default changes the normalized graph; the graph as stored also counts.
if model.graph_hash not in {
    hashlib.sha256(encoded(graph_document(snapshot))).hexdigest(),
    hashlib.sha256(encoded(graph_document(clean))).hexdigest(),
}:
    raise ValueError("invalid_snapshot")
```

```python
# live.py, build_live_management
STARTUP_LOG.error(json.dumps({"event": "management.startup.failure",
                              "reason": "store_unavailable",
                              "error_codes": failure_codes(error)}))
raise StartupError("store_unavailable") from None
```

## Tests Added or Updated

- `test_configuration_persistence.py::test_revisions_from_earlier_releases_still_load_and_bootstrap`, two cases:
  - `schema-2.0.0-without-routable`: the int data shape.
  - `2.1.0-writer-with-explicit-routable`.

  Each case checks that:
  - `bootstrap` and `read_active` return the stored revision and its etag, and `read_revision` returns the same document;
  - nothing is written back;
  - a later change to the stored graph is still rejected (`configuration_store_unavailable`).
- `test_configuration_persistence.py::test_non_routable_community_round_trips`: `routable: false` publishes and reads back unchanged, in both the local and the Cosmos-double repository.
- `test_live_composition.py::test_a_start_over_a_revision_from_the_previous_release_succeeds`: the live composition restarts over a store whose active revision was rewritten to the schema-2.0.0 shape, starts, and leaves the store unchanged.
- `test_live_composition.py::test_a_startup_failure_logs_its_fixed_codes_but_no_content`:
  - a tampered stored prompt still stops the start with `store_unavailable`;
  - the log line carries `["management_composition_unavailable", "configuration_store_unavailable", "invalid_snapshot"]`;
  - the log line does not contain the tampered text.

## Local Verification

- **Regression tests catch the bug:** with the `configuration_repository.py` change stashed, `test_revisions_from_earlier_releases_still_load_and_bootstrap[schema-2.0.0-without-routable]` and `test_a_start_over_a_revision_from_the_previous_release_succeeds` fail. With the change, they pass.
- **Management checks** (in `code/management`):
  - `ruff format --check`: 114 files already formatted.
  - `ruff check`: passed.
  - `mypy management_app`: no issues in 66 files.
  - `pytest tests/unit tests/contract tests/integration -m "not browser" --deselect tests/integration/test_foundation_recovery.py`: 1056 passed. `reflex.lock` was restored afterwards.
- **Whitespace:** `check_whitespace.py` on the changed files passed (exit 0).
- **Docs check (`verify_docs.py`):** not completed locally. It renders every Mermaid diagram in the repository and ran for more than 15 minutes before I stopped it. The doc change adds a paragraph with no diagram; CI runs the full check.
- **Manual checks:** none. No change was made on int; it still runs the old management revision `--0000021`.

## Deviations from Assessment

- **Step 2 dropped (omit `routable` when true).** I tried it, and four existing tests failed because they require `routable: true` to round-trip explicitly in the configuration API and the model dumps (`test_configuration_validation.py` round-trip tests and `test_configuration_api.py::test_put_publishes_complete_revision_with_actor_and_new_etag`). That makes it a contract change, not a backward-compatibility fix. Step 1 alone fixes the bug, so I reverted step 2. `routable` stays explicit in dumps and in the runtime projection; the runtime already treats an absent `routable` as `true`.
- **Step 1 accepts either hash form.** Instead of replacing the normalized hash with the stored-graph hash, both are accepted. Only the stored form could differ from the original in number formatting after a Cosmos round trip, so keeping the normalized check means existing revisions that verify today can never start failing. Tamper protection is unchanged: a modified graph matches neither hash.
- **Observability.** The diagnostic is one JSON log line on a `management.startup` logger, the same pattern as `management.test-relay.transport`. It is not a `Telemetry.diagnostic` event, because those require a test mode and the telemetry object does not exist yet when the composition fails early.
- **Scope expansion.** `docs/runbooks/monitoring.md` was updated for the new log line; the assessment did not list it.

## Follow-ups

- **Deploy to int:** build and publish the management image from this fix, then set only the management tag in `code/infra/environments/int/int.tfvars.json`. The local edit there, which also moves the runtime to `sha-817ba2ba…`, is uncommitted and not part of this fix.
- **CI upgrade check:** consider an image smoke step that starts management against a store seeded with the previous release's documents. All existing tests started from an empty store, which is why this was missed.
- **Remaining masked failures:** the generic `except Exception` in `LiveManagementApplication._lifespan` and the managed lifespan failure in `_next` still report a bare `store_unavailable`. They were not involved here.
