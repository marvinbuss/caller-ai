# Acceptance record: first internal test environment

This is the record for the live release gates of the first internal test environment (`int`, feature 004). It
holds results only; the steps are in [Environment bring-up](../runbooks/environment-bring-up.md). A gate that has
not been run says so. Nothing here is claimed from an offline test.

## Current decision

**Pending, not accepted.** The offline checks pass. The live gates below have not been run: no subscription,
state storage, directory object, image publication or deployment exists for `int` yet, and several pieces the gates
need are not delivered (see the status table in the bring-up runbook).

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
| Real values in the environment files, validated | Not run | | | |
| State storage created and migrated (`state-create`) | Not run | | | |
| Bootstrap applied; only default security rules and no routes | Not run | | | |
| Package visibility and the owner's approval to publish the application code | Not run | | | |
| Identity objects created and the secret stored once | Not run | | | |
| App environment applied; first check fails only at `signin-redirect-matches` | Not run | | | |
| Redirect registered; every check passes | Not run | | | |
| Zero planned changes on a repeat run (`expect_no_changes`) for `app_environment` and `bootstrap` | Not run | | | |
| Public access disabled, refusal confirmed, enabled again | Not run | | | |
| Editor and test console walked in a browser by a signed-in group member | Not run | | | |
| Alert test notification received by the named owner | Not run | | | |
| Destroy and recreate `app_environment` and `bootstrap` | Not run | | | |
| A second engineer follows the runbook to a verified environment | Not run | | | |
