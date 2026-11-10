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
| Environment files | `validate_environment.py` passes for `int` with the expected `open_range` warning (public access open to `0.0.0.0/0`) |
| Terraform | `verify_terraform.py check`: formatting, validation and native tests for three roots and thirteen modules, and the pinned Trivy configuration scan |
| Layout | `check_terraform_structure.py` reports a valid structure |
| Delivery, check and image tooling | Offline tests with fake registries, fake Azure responses and a fake Terraform runner pass |
| Application settings | Settings validation for both applications passes; the settings names match the contract and the deployment |

## Live gates

Fill in the date, version tags, result and any deviation as each gate is run.

| Gate | Date | Image tags | Result | Deviation |
|------|------|------------|--------|-----------|
| Real values in the environment files, validated | 2026-11-09 | `sha-9c78b00c6637fd6697fa215c28b282bc467b2f8f` | Passed: no placeholders remain; `validate_environment.py` passes with the expected `open_range` warning (`0.0.0.0/0`) | The validator flagged the 64-hex reviewed fingerprint as secret-like; it now accepts a SHA-256 digest in that one setting |
| State storage created and migrated (`state-create`) | 2026-11-09 | | Passed: `callerintstate0001` created with shared keys off, state migrated to `int/state.tfstate`, local copy removed | Run from a workstation after two fixes to `state-create` (Windows `az.cmd`, local backend override) |
| Bootstrap applied; only default security rules and no routes | 2026-11-09 | | Passed: run 42632204371; `nsg-vnet-caller-int` has 0 custom rules, `rt-vnet-caller-int` has 0 routes | First run failed until the callers passed the environment secrets to the reusable workflow |
| Package visibility and the owner's approval to publish the application code | 2026-11-09 | `sha-9c78b00c6637fd6697fa215c28b282bc467b2f8f` | Passed: the owner (marvinbuss) approved publishing the application code on 2026-11-09; images published and pulled without credentials (run 42632174721) | |
| Identity objects created and the secret stored once | 2026-11-09 | | Passed: registrations, roles, runtime identity and role assignments created; the sign-in secret was rotated after an exposure, stored once as `MANAGEMENT_SIGNIN_SECRET` in the `int` environment, and `code/.local/identity/int/` deleted | Script fixes for Graph writes on Windows and the access token version |
| Settings fingerprint reviewed and recorded | 2026-11-09 | | Passed: fingerprint of the management app's planned non-secret settings recorded as `reviewed_fingerprint` with review reference `int-live-review-1` | `CALLER_COSMOS_ENDPOINT` assumed in the `https://<name>.documents.azure.com:443/` form; recompute if the first start reports `fingerprint_mismatch` |
| App environment applied; first check fails only at `signin-redirect-matches` | 2026-11-09 | `sha-9c78b00c6637fd6697fa215c28b282bc467b2f8f` | Not passed yet: the first apply (run 42653280994) created the Foundry account and both deployments, Cosmos DB, Key Vault, the workspace and the private endpoints, then failed | Azure rejected both log-query alerts because `ContainerAppConsoleLogs_CL` exists only after the first logs arrive; the alerts now skip query validation. The next plan (run 42660146973) failed once the workspace ID was known: the azurerm provider requires `logs_destination = "log-analytics"` alongside `log_analytics_workspace_id`; the Container Apps environment now sets it. The apply then needed the subscription feature `Microsoft.Network/AllowBringYourOwnPublicIpAddress`, now registered. Run 42668181139 applied; both apps failed to start (the runtime rejected the dotted deployment name `gpt-6.1-sol`; both images lacked `aiohttp`, which the async Azure clients need), and the check lost its Azure sign-in after the long apply and read the environment's public access with an API version that omits it; all four are fixed. With `sha-e12996269d3ee7fc63444bc32cd74b75b98dcb78` the runtime got 401 from the management app because the management registration did not request the `idtyp` claim; the identity script now requests it, and the runtime needed a new revision to drop its cached token. Run 42722834840 passed every check except the functional journey (`configuration-read: read_refused`): the check's HTTP client kept the sign-in flow cookie from `/auth/login` and sent it with the tester token, and the management app refuses a bearer token that arrives with a cookie; the journey now sends no cookies |
| Redirect registered; every check passes | Not run | | | |
| Zero planned changes on a repeat run (`expect_no_changes`) for `app_environment` and `bootstrap` | Not run | | | |
| Public access disabled, refusal confirmed, enabled again | Not run | | | |
| Editor and test console walked in a browser by a signed-in group member | Not run | | | |
| Alert test notification received by the named owner | Not run | | | |
| Destroy and recreate `app_environment` and `bootstrap` | Not run | | | |
| A second engineer follows the runbook to a verified environment | Not run | | | |
