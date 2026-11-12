# Bug Assessment: New management image cannot start on int: revisions stored by the previous release fail the integrity check

- **Slug**: failed-management-redesign-startup
- **Created**: 2026-11-12
- **Source**: pasted text
- **Verdict**: valid (reproduced locally against the int data shape)
- **Severity**: high

## Report (verbatim)

> I just tried to run thew new management container in `int` but the container failed to start. Review!

### Evidence gathered (read-only)

- **Deployment.** The local, uncommitted `code/infra/environments/int/int.tfvars.json` changes both image tags from `sha-60f740e…` to `sha-817ba2ba…`, the merge commit of PR #41, which includes the 005 management redesign.
- **Revisions of `ca-caller-int-management`** (single revision mode):

  | Revision | Image | Created (UTC) | Health | State |
  |----------|-------|---------------|--------|-------|
  | `--0000022` | `sha-817ba2ba…` | 2026-11-12 13:08 | Unhealthy | `ActivationFailed` |
  | `--0000021` | `sha-60f740e…` | 2026-11-11 | Healthy | `RunningAtMaxScale` |

- **Who is serving.** The old revision `--0000021` still answers on the public origin: `/healthz` 200 and `/readyz` 200 `ready`. So int management is not down; the redesign simply never rolled out.
- **Runtime.** `ca-caller-int-runtime--0000023` (`sha-817ba2ba…`) is Healthy and takes 100 % of traffic, so only management is affected.
- **Console logs of `--0000022`** (Log Analytics `log-caller-int`, `ContainerAppConsoleLogs_CL`). There were 12 restarts, all with the same cycle about 2–3 s after the worker spawns:

  ```text
  13:08:41.171 [INFO] Starting granian (main PID: 1)
  13:08:41.171 [INFO] Spawning worker-1 with PID: 7
  13:08:44.083 [ERROR] store_unavailable
  13:08:44.083 [ERROR] ASGI lifespan startup failed
  13:08:44.083 [ERROR] Unexpected exit from worker-1
  ```

  No other application line is logged; the real cause is masked (see "Suspected Code Paths").
- **Stored configuration on int**, read inside the still-running `--0000021` container. Only counts and keys were printed, no content:

  ```text
  revision 3 schema 2.0.0 communities 1 with_routable 0 agents 4
  ```

## Symptom

The new management container exits during ASGI lifespan startup with `store_unavailable`, so the revision never becomes ready. Container Apps keeps the previous revision serving. Expected: the redesigned management app starts against the existing int data and serves the new frontend.

## Reproduction

1. Have a configuration revision published by the previous release, with schema `2.0.0`. Its communities carry no `routable` key, and its `graphHash` was computed without it. Int has exactly this: active revision 3, one community, `routable` absent.
2. Start the `sha-817ba2ba…` management image in live mode against that store. `compose_management` calls `bootstrap(repository)`, then `CosmosConfigurationRepository.initialize`, `_active()` and `verified_revision()`.
3. `verified_revision` raises `ValueError("invalid_snapshot")`. The error surfaces as `configuration_store_unavailable`, then `management_composition_unavailable`, then `StartupError("store_unavailable")`, and lifespan startup fails.

Local reproduction with the management venv at HEAD (`817ba2b`) and a synthetic revision of the old shape:

```text
old writer, no routable  -> ValueError invalid_snapshot
same graph with routable -> accepted
no communities           -> accepted
```

## Suspected Code Paths

- `code/management/management_app/models/configuration.py`, `Community`: 005 added `routable: bool = True`. Loading an old community fills in `routable: true`, so `model_dump` now emits a key the stored document never had.
- `code/management/management_app/adapters/configuration_repository.py`, `verified_revision()`: it recomputes `sha256(encoded(graph_document(snapshot)))` over the re-dumped, normalized model and compares it with the stored `graphHash`. Any field added with a default changes the normalized graph, so every revision written before 005 that has at least one community now fails integrity. The other additive fields (`conversationSettings`, `publication`) are safe only because they use `exclude_if=_absent`; `routable` does not.
- `code/management/management_app/services/configuration_validation.py`, `GRAPH_FIELDS`: `communities` is part of the hashed graph.
- `code/management/management_app/services/configuration_publication.py`, `publication()`: new revisions hash the normalized dump. Revisions published by the new code include `routable` and verify, which is why tests that only publish with the new code pass.
- `code/management/management_app/composition.py`, `compose_management()`: `await bootstrap(repository)` runs on every startup and reads the active revision. Any exception becomes `RuntimeError("management_composition_unavailable")`.
- `code/management/management_app/live.py`, `build_live_management()` and `LiveManagementApplication._lifespan()`: every non-permission, non-`StartupError` failure is reported as the single code `store_unavailable`. The original reason (`invalid_snapshot`) is lost, which made the int failure opaque.
- The same `verified_revision` path also serves `read_active` (test relay `active_revision`, editor and runtime configuration API) and `read_revision`. So even without the startup check, the old active revision and the revision history would be unreadable.

## Root Cause Hypothesis

**High confidence.** The 005 redesign added `Community.routable` with a default of `true`, without excluding it from serialization when it equals that default. `verified_revision` checks integrity by re-serializing the validated model and hashing the result, rather than hashing the graph as it was stored. Every revision written by the previous release that has a community therefore hashes differently and is rejected as `invalid_snapshot`. Startup reads the active revision through `bootstrap`, and int's active revision 3 has one community without `routable`. So the composition fails, `live.py` maps the failure to `store_unavailable`, and the revision never activates.

This was confirmed in two ways:

- a local reproduction with the same document shape;
- a read-only check of the int data, which has 1 community and 0 with `routable`.

CI did not catch it because every test and the image smoke start from an empty or newly published store; none loads a revision written by the previous schema.

## Proposed Remediation

**Preferred: verify integrity over the graph as stored, and keep the new field backward compatible.**

1. In `verified_revision`, compute the hash over the graph fields of the stored document, after removing Cosmos metadata, and still validate the document with `ConfigurationRevision`. Then return the normalized dump as today. This keeps the integrity guarantee ("these bytes are what was published") independent of later model defaults, so future additive fields cannot break old revisions again. New revisions are unaffected, because `publication()` stores exactly the dump it hashes.
2. Also give `Community.routable` the same treatment as the other 2.1.0 additions: omit it when it equals the default (`exclude_if` for `True`). Old and new graphs then serialize identically unless a community is actually non-routable. Do the same in the runtime projection only if the runtime contract allows an absent `routable` (to check in the fix: the runtime schema `code/contracts/configuration.schema.json` and the runtime `domain/configuration.py`).
3. **Observability.** Log the internal startup failure code as a closed-set diagnostic before collapsing it to `store_unavailable`, for example a `management.startup.failure` event with `error_code=invalid_snapshot` or `configuration_store_unavailable`. This mirrors the runtime's `test.admission.failure`, so the next startup failure can be diagnosed from the logs.

**Alternatives:**

- Only (2), excluding `routable` when true. This is the smallest change and fixes int today, but the hash check stays fragile for the next added field.
- A one-off data migration that rewrites the stored int revisions with the new hash. Not recommended: it rewrites published, integrity-protected history and touches shared data, and every other environment would need the same migration.
- Roll back int to the old management image only. That is a mitigation, not a fix; the old image is in fact still serving.

**Files likely to change:**

- `code/management/management_app/adapters/configuration_repository.py` (`verified_revision`)
- `code/management/management_app/models/configuration.py` (`Community.routable` serialization)
- `code/management/management_app/live.py` (startup diagnostic)
- `code/management/management_app/observability.py` (allow the diagnostic, if events are a closed set there)
- Tests: `code/management/tests/unit/test_configuration_repository.py` (or wherever `verified_revision` is covered), the live startup and composition tests in `code/management/tests/integration/`, and a frozen fixture of a schema-2.0.0 revision.

**Tests to add or update:**

- A frozen schema-2.0.0 revision fixture with communities and no `routable`, hashed the old way:
  - `verified_revision` accepts it;
  - `read_active` and `read_revision` return it with `routable: true` in the normalized snapshot.
- Upgrade path: `compose_management` and live startup against a store seeded with the old revision start successfully, and `bootstrap` does not rewrite it.
- New publications still verify. A tampered stored graph is still rejected, with one byte changed in the stored graph fields.
- A non-routable community (`routable: false`) round-trips and verifies.
- A startup failure logs its internal code while the lifespan message stays `store_unavailable`.

## Risks & Considerations

- **Integrity semantics.** Hashing the stored graph instead of the normalized one must still reject tampering. The model validation still runs, so a stored document that is valid JSON but invalid for the model is still refused.
- **Runtime projection.** If the runtime requires `routable` to be present, the projection must keep emitting it even when the stored revision omits it; the projection's `policyHash` is computed over what is sent, so that stays consistent. Check this before changing serialization in step 2.
- **Rollout state on int.**
  - The runtime already runs `sha-817ba2ba…`, while management runs `sha-60f740e…`. That combination is healthy now: the old management still serves its projection and the runtime is ready.
  - The failed revision `--0000022` keeps being restarted. Deploying a fixed management image will replace it.
  - Until then, the local `int.tfvars.json` change should not be applied again for management, or management should stay pinned to `sha-60f740e…`.
- **Other environments.** Any environment with configuration published before 005 has the same problem; int is the only one known.
- **Stamps on old revisions.** The first publish after the fix stamps every agent as changed, because the previous revision has no `publication` record. That is expected and not part of this bug.

## Open Questions

- None blocking. In the fix, check whether the runtime contract (`configuration.schema.json` and runtime `domain/configuration.py`) accepts a projection without `routable` before deciding on step 2's serialization.
