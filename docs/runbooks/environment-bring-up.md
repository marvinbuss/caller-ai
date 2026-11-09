# Environment bring-up

How to bring up, verify, change, recover and remove an environment such as `int`. The concepts are in
[Environments](../environments.md). Commands run from the repository root in PowerShell 7.

## Status of the pieces

| Piece | Status |
|-------|--------|
| Environment files, validator, Terraform roots and modules, delivery script, workflows, environment check | Delivered and tested offline |
| Image publishing workflow | Delivered; first publication and the public package setting are manual |
| Identity script | Delivered and tested with Pester against a fake Azure CLI; not yet run against a real directory |
| Application live mode | Delivered and tested with fake clients: settings validation, live composition of both apps, the reviewed fingerprint and the frontend start address (see [Application live mode](application-live-mode.md)); not yet run against Azure |
| Functional journey in the check (`--functional`) | Not delivered yet |
| Live acceptance on `int` | Not run. Results go to [the acceptance record](../acceptance/int-environment.md) |

## Prerequisites

| Need | Detail |
|------|--------|
| Azure | A tenant, one subscription for the environment, an account allowed to create resource groups and role assignments, and a deployment identity with one federated credential: issuer `https://token.actions.githubusercontent.com`, subject `repo:marvinbuss/caller-ai:environment:<env>`, audience `api://AzureADTokenExchange` |
| Directory rights | A directory administrator for the identity objects (app registrations, roles, role assignments, and consent for the Graph read permission `Application.Read.All`; Privileged Role Administrator or Global Administrator), and a group that will hold the editor and tester roles (`identity.user_group_id`) |
| Model | Quota in the subscription for the text model and `gpt-live-1` under the deployment types in `app_environment.model`, in an allowed region. Terraform creates the Foundry account and both deployments |
| GitHub | One environment named `<env>`, limited to protected branches, with required reviewers and self-review prevented; secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` and `MANAGEMENT_SIGNIN_SECRET` in it; the variable `TF_PLAN_CONTAINER` (`tfplans`) |
| Local | Terraform 1.17.0 (the pinned binary prepared by the tools), Python through `uv`, PowerShell 7, Azure CLI, Docker for the security scan |

## 1. Offline checks

```powershell
. .\code\.local\feeds.ps1   # only in the mirror-feed development setup
uv run --project code/tools --locked python code/tools/scripts/validate_environment.py
uv run --project code/tools --locked python code/tools/scripts/check_terraform_structure.py
uv run --project code/tools --locked python code/tools/scripts/verify_terraform.py prepare
uv run --project code/tools --locked python code/tools/scripts/verify_terraform.py check
```

Expected: the environment is valid, the structure is valid, every root and module test passes and the
security scan reports nothing at high or critical level. Nothing here needs an Azure credential.

## 2. Publish the images

Merge to `main` or push a `v*` tag; the `images` workflow builds, scans, publishes and prints digests. The
first time, make both packages public in the repository's package settings and rerun the workflow so that the
anonymous pull check passes. Copy the `sha-<40 hex>` tags into `app_environment.container_apps` in the
environment's `<env>.tfvars.json`. See [Image publishing](image-publishing.md).

## 3. State

Edit the environment files with the real values, validate again, then create the state storage once, locally:

```powershell
az login --tenant <tenant-id>
uv run --project code/tools --locked python code/tools/scripts/terraform_delivery.py --operation state-create --environment int --root state --plan-container tfplans
```

Without `--confirm-apply` this only plans. Add `--confirm-apply` to create the storage account, migrate the
state to `<env>/state.tfstate` and remove the local copy. It refuses when the state blob already exists. Details
are in [Terraform state](terraform-state.md).

## 4. Bootstrap, then identity

1. Run the `terraform-bootstrap` workflow for the environment, review the private plan and approve the apply.
   Expected: network, one network security group with default rules only, one route table without routes, the
   private DNS zones.
2. A directory administrator runs the identity script from the repository root, preview first, then for real. Sign
   in first as a person (not a service principal) in the environment's tenant and subscription:

   ```powershell
   az login --tenant <tenant-id>
   az account set --subscription <subscription-id>
   pwsh code/infra/scripts/identity/Initialize-Identity.ps1 -Environment int -WhatIf
   pwsh code/infra/scripts/identity/Initialize-Identity.ps1 -Environment int
   ```

   The administrator needs a checkout of the repository with the reviewed `<env>.tfvars.json`, which the script
   reads. The script reads that file only and takes no other input.

   It needs PowerShell 7, the Azure CLI signed in as a person in the environment's tenant and subscription, the
   directory role Privileged Role Administrator or Global Administrator, and Owner (or Contributor with User Access
   Administrator) on the subscription. It creates, idempotently: the resource group for the runtime identity and
   the runtime's user-assigned identity; the management app registration with its service principal and the three
   app roles `editor`, `tester` and `runtime` named in `app_environment.identity.roles`; the runtime app
   registration with the app role `management` (the audience of the management app's workload token, FR-052); the
   `runtime` role for the runtime identity; the `tester`
   role and the read-only Graph permission `Application.Read.All` for the deployment identity; the editor and tester
   roles for the user group; and a client secret valid for at most 90 days, written only to
   `code/.local/identity/<env>/management-signin-secret`. Exit codes: 0 success or preview, 1 preflight failure, 2
   invalid input, 3 a failed step (named). A second run changes nothing.
3. Place the results. The script prints a block of values and writes one file; nothing else is produced.

   | Result | Where it goes | Notes |
   |--------|---------------|-------|
   | `management_app_client_id` and `runtime_app_client_id` (printed) | `app_environment.identity.management_app_client_id` and `app_environment.identity.runtime_app_client_id` in `code/infra/environments/<env>/<env>.tfvars.json` | Not secrets; commit them with the reviewed environment files after `validate_environment.py` passes |
   | `tenant_id`, `runtime_identity_name`, `roles.*` (printed) | Already inputs in the same file (`environment.tenant_id`, `app_environment.identity`); compare them with the printed values | The script reads them, so they should match; a difference means the file was edited after the run |
   | Client secret (file `code/.local/identity/<env>/management-signin-secret`) | The secret `MANAGEMENT_SIGNIN_SECRET` in the GitHub environment `<env>` | Paste it once from the file, then delete `code/.local/identity/<env>/`. The folder is ignored by git and the file has owner-only access. It is created only when no valid secret exists, and it is never printed |

   Run the script again at any time: with a valid secret it creates nothing and writes no file. After the first
   app environment deployment run it once more with `-Phase redirect` (step 5).

## 5. App environment

Run `terraform-app-environment`, review the plan and approve the apply. Expected: both apps, the data store,
the vault, the permit key pair, both vault secrets, the private endpoints, model access and monitoring. After
the apply the job runs the environment check and writes its JSON result to the job summary. The job fails when a
check fails.

On the first deployment the check is expected to fail only at `signin-redirect-matches`, because the
management callback address (`https://<management default domain>/auth/callback`) exists only after the apps
exist. The resources are applied. Run `Initialize-Identity.ps1 -Environment int -Phase redirect`. It reads the app address with
`az containerapp show`, registers the callback address, and assigns the `management` role of the runtime registration
to the management identity (which Terraform has created by now). Then rerun the check:

```powershell
uv run --project code/tools --locked python code/tools/scripts/check_environment.py --environment int --manifest <manifest.json> --test-alert
```

The manifest is the `environment_manifest` output of the `app_environment` root.

## 6. Repeatability

Run `terraform-app-environment` and `terraform-bootstrap` with `expect_no_changes: true`. The plan must show
zero changes. A change means drift or a nondeterministic resource and stops the job.

## Change

Edit the environment file, validate, run the plan, review it and approve the apply. A new image is a new tag
in `app_environment.container_apps`.

## Rollback

Set the previous `sha-<40 hex>` tag again and apply. Published tags are never deleted, so the earlier image is
always available.

## Recovery

| Situation | Action |
|-----------|--------|
| Apply stopped half-way | Run the workflow again; Terraform converges. A stale lock is released by the state lease after the lock timeout; never force-unlock without reading the holder |
| A revision is unhealthy | Read the failing check: it names the app and image tag, or the start reason code from [Application live mode](application-live-mode.md) |
| Plan expired or inputs changed | Plans are bound to the commit, run and inputs, and expire after one day; run the plan again |

## Secret renewal and key rotation

- **Sign-in secret**: the client secret is valid for at most 90 days. Create a new one with the identity
  script or in the directory, store it in `MANAGEMENT_SIGNIN_SECRET`, run `terraform-app-environment`, then
  restart the management revision so that the Key Vault reference is read again.
- **Permit signing key**: the pair that signs and verifies test-conversation start permits is generated by
  Terraform and the private key lives only in the vault (read by the management app only). To rotate with an
  overlap, read the runtime's current `CALLER_PERMIT_VERIFICATION_KEYS` value (public keys only), set
  `app_environment.signing_key.key_id` to a new ID and add the old ID and its public key to
  `app_environment.signing_key.previous_public_keys` (at most three). Apply: Terraform generates a new pair,
  management signs with the new key and the runtime accepts both. After the longest permit lifetime has passed,
  remove the old entry and apply again. The remote state holds the private key, so protect it as described in
  [Terraform state](terraform-state.md).

## Teardown

Run `terraform-app-environment` and then `terraform-bootstrap` with the `destroy` input and approve each apply.
The state storage and the directory objects remain. `destroy` is refused for the `state` root.
