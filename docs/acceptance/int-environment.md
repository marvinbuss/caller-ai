# Acceptance record: first internal test environment

This is the record for the live release gates of the first internal test environment (`int`, feature 004). It
holds results only; the steps are in [Environment bring-up](../runbooks/environment-bring-up.md). A gate that has
not been run says so. Nothing here is claimed from an offline test.

## Current decision

**Pending, not accepted.** The offline checks pass. Bring-up of `int` is in progress: the state storage, the
bootstrap network and the published images exist; the app environment and the remaining gates below have not been
run.

## Offline results

| Check | Result |
|-------|--------|
| Environment files | `validate_environment.py` passes for `int` with the expected `open_range` warning (placeholder values) |
| Terraform | `verify_terraform.py check`: formatting, validation and native tests for three roots and thirteen modules, and the pinned Trivy configuration scan |
| Layout | `check_terraform_structure.py` reports a valid structure |
| Delivery, check and image tooling | Offline tests with fake registries, fake Azure responses and a fake Terraform runner pass |
| Application settings | Settings validation for both applications passes; the settings names match the contract and the deployment |

## Live gates

Fill in the date, version tags, result and any deviation as each gate is run.

| Gate | Date | Image tags | Result | Deviation |
|------|------|------------|--------|-----------|
| Real values in the environment files, validated | 2026-11-09 | `sha-9c78b00c6637fd6697fa215c28b282bc467b2f8f` | Partly: identity, state, network, model and image values set and `validate_environment.py` passes; `application.voice`, `reviewed_fingerprint` and the monitoring contacts are still placeholders | |
| State storage created and migrated (`state-create`) | 2026-11-09 | | Passed: `callerintstate0001` created with shared keys off, state migrated to `int/state.tfstate`, local copy removed | Run from a workstation after two fixes to `state-create` (Windows `az.cmd`, local backend override) |
| Bootstrap applied; only default security rules and no routes | 2026-11-09 | | Passed: run 42632204371; `nsg-vnet-caller-int` has 0 custom rules, `rt-vnet-caller-int` has 0 routes | First run failed until the callers passed the environment secrets to the reusable workflow |
| Package visibility and the owner's approval to publish the application code | 2026-11-09 | `sha-9c78b00c6637fd6697fa215c28b282bc467b2f8f` | Images published and pulled without credentials (run 42632174721); the owner's approval is still to be recorded here | |
| Identity objects created and the secret stored once | 2026-11-09 | | Partly: registrations, roles, runtime identity and role assignments created; secret stored in the `int` environment; rotation of the secret pending | Script fixes for Graph writes on Windows and the access token version |
| App environment applied; first check fails only at `signin-redirect-matches` | Not run | | | |
| Redirect registered; every check passes | Not run | | | |
| Zero planned changes on a repeat run (`expect_no_changes`) for `app_environment` and `bootstrap` | Not run | | | |
| Public access disabled, refusal confirmed, enabled again | Not run | | | |
| Editor and test console walked in a browser by a signed-in group member | Not run | | | |
| Alert test notification received by the named owner | Not run | | | |
| Destroy and recreate `app_environment` and `bootstrap` | Not run | | | |
| A second engineer follows the runbook to a verified environment | Not run | | | |
