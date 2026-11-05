# Quickstart: First Internal Test Environment

How to bring up, verify, change and remove the `int` environment, and how to validate the feature
end to end. Commands run from the repository root in PowerShell 7. Interfaces are in
[tooling-and-workflows.md](contracts/tooling-and-workflows.md).

## Before you start

| Need | Detail |
|---|---|
| Azure | Tenant, one subscription for `int`, an account allowed to create resource groups, role assignments and a deployment identity with OIDC federation to this repository's environments. |
| Directory rights | An administrator able to create app registrations, roles and role assignments and to consent to Graph application permissions (Privileged Role Administrator or Global Administrator) for the identity script (FR-040, FR-053), and a group that will hold the editor and tester roles (`identity.user_group_id`). |
| Model | An existing account with a text deployment and a realtime deployment, in an allowed region (FR-039). |
| GitHub | Environments `int-terraform-<root>-plan|apply` with required reviewers on `apply`, and variables set per [tooling-and-workflows.md](contracts/tooling-and-workflows.md). |
| Local | Terraform 1.17.0, Python through `uv`, PowerShell 7, Azure CLI, Trivy. |
| Open items | The owner answers the items at the end of [plan.md](plan.md) before step 3. |

## 1. Offline checks (no Azure access)

```powershell
. .\code\.local\feeds.ps1   # only in the mirror-feed development setup
uv run --project code/tools --locked python code/tools/scripts/validate_environment.py
uv run --project code/tools --locked python code/tools/scripts/check_terraform_structure.py
uv run --project code/tools --locked python code/tools/scripts/ci.py check
```

Expected: environment valid, structure valid, all tests pass, no Terraform test needs credentials
(SC-001, SC-006, SC-007). Application tests for the live composition, the editor and the console pass with
fake ports.

## 2. Publish the images

Merge to `main` (or push a `v*` tag). The `images` workflow builds, scans, pushes and prints digests.
First time only: set both packages public in the repository's package settings, then rerun the workflow so
the anonymous pull check passes. Copy the `sha-<40 hex>` tags into `app_environment.container_apps` in
`code/infra/environments/int/int.tfvars.json`.

## 3. Bring-up in order

1. Edit `int.tfvars.json` and the three backend files with the real values. Validate again (step 1).
2. State, run locally once:
   ```powershell
   az login --tenant <tenant-id>
   uv run --project code/tools --locked python code/tools/scripts/terraform_delivery.py --operation state-create --environment int --confirm-apply
   ```
   Expected: storage account created, state migrated to `int/state.tfstate`, local copy removed (FR-006, FR-007).
3. Run the `terraform-bootstrap` workflow for `int`. Approve the apply. Expected: network, one NSG with default
   rules only, one route table without routes, DNS zones; outputs listed in
   [deployment-interfaces.md](contracts/deployment-interfaces.md).
4. Identity, run by a directory administrator:
   ```powershell
   pwsh code/infra/scripts/identity/Initialize-Identity.ps1 -Environment int -WhatIf
   pwsh code/infra/scripts/identity/Initialize-Identity.ps1 -Environment int
   ```
   Copy the printed values into `app_environment.identity`. Store the secret in `MANAGEMENT_SIGNIN_SECRET`
   in the two `app_environment` GitHub environments, then delete `code/.local/identity/int/`.
5. Run the `terraform-app-environment` workflow. Approve the apply. Expected: both apps, data store, vault,
   private endpoints, model access, monitoring, permit key pair, both vault secrets; the job runs the
   environment check, including the functional journey from the runner, and fails if it fails (FR-029). On the
   first deployment the check is expected to fail only at `signin-redirect-matches`, because the callback address
   exists only after the apps do. The resources are applied. Continue with step 6 and rerun the check.
6. Run the identity script with `-Phase redirect` to register the management callback address, then rerun the
   check (step 7).
7. Run the check yourself:
   ```powershell
   uv run --project code/tools --locked python code/tools/scripts/check_environment.py --environment int --test-alert
   ```
   Expected: every check passes (SC-012).

## 4. Idempotence (SC-002)

Run `terraform-app-environment` with `expect_no_changes: true`. Expected: the plan shows zero changes. Repeat
for `bootstrap`.

## 5. Public access toggle (SC-005)

1. Set `environment.public_access.enabled` to `false`, validate, then run the
   `terraform-app-environment` workflow. Expected: the apps refuse requests from any source, the check passes (the functional journey is skipped with the
   reason `public_access_disabled`),
   and the plan changed only the access settings.
2. Set it back to `true` with your range (`int` uses the open range `0.0.0.0/0`, so the runner reaches the apps; with
   a restricted list, other sources are refused). Expected: both apps answer from an allowed source.

## 6. Functional journey (FR-048, SC-015)

The pipeline runs the conversation journey after each `app_environment` apply as the deployment identity (step 3.5).
To run it yourself, use `check_environment.py --environment int --functional` with a tester token. A person then
walks the editor in a browser:

1. Open the management app address from the check output. Sign in as a user in the editor group.
2. Create a configuration in the editor, review and publish it.
3. Open the test console, run a text conversation: you get a model reply within 15 seconds.
4. Start a speech session: audio is accepted, a reply is spoken, stopping ends the session.
5. Confirm the audit entries and that a user without the role is refused.

Then restart both apps and move to a second image version. Expected: configuration, audit entries and text history
are still there (SC-016).

## 7. Second environment sample (SC-006)

Copy `code/infra/environments/int/` to `code/infra/environments/dev/`, replace the names and subscription,
change nothing else, and run `validate_environment.py`. Expected: valid, no change to modules, tooling or
workflows. Delete `dev/` after the check (nothing deployed).

## 8. Remove and recreate (SC-003)

Run the app environment workflow with `destroy: true`, then bootstrap with `destroy: true`. Expected: the
state storage, state blobs and the identity script's objects remain. Recreate from step 3. The identity script
reports existing objects and creates nothing new.

## 9. Documentation (SC-009, SC-010)

```powershell
uv run --project code/tools --locked python code/tools/scripts/verify_docs.py
```

Expected: links and diagrams pass. A new engineer can follow the environment guide, the identity guide and
this file to a verified environment without help.

## Recovery notes

| Symptom | Action |
|---|---|
| State lock stuck | Wait for the lock wait. If the workflow was cancelled, break the lease only after confirming no run is active. |
| Private endpoint pending | Approve it on the target resource, then rerun the check. |
| App revision failed to start | The check names the application, version and reason code. Fix the setting or image tag, rerun. |
| Sign-in redirect mismatch | Rerun the identity script with `-Phase redirect`. |
| Secret expired | Rerun the identity script, update `MANAGEMENT_SIGNIN_SECRET`, rerun the app environment workflow. |
| Anonymous pull fails | Make the package public, rerun `images`. |
