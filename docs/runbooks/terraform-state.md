# Terraform state

Each environment keeps its Terraform state, and the saved plans waiting for approval, in one storage account that
the `state` root creates. This guide covers what protects it, how it is created, how roots find it and how to
recover. The deployments are described in [Environments](../environments.md) and the delivery steps in
[Terraform delivery](terraform-delivery.md).

## What holds state

| Item | Detail |
|------|--------|
| Storage account | One per environment, in its own resource group, created by the `state-backend` module |
| Containers | `tfstate` holds one blob per root, `tfplans` holds saved plans and their review artifacts. Both are private |
| Keys | `<env>/state.tfstate`, `<env>/bootstrap.tfstate`, `<env>/app_environment.tfstate`; the key comes from each root's backend file, never from code |
| Redundancy and recovery | Geo-redundant storage, blob versioning, and soft delete of blobs and containers for `state.state_retention_days` |
| Protection | A `CanNotDelete` lock on the account; remove it deliberately before any deletion |

State and plans can hold two approved secrets, the management sign-in client secret and the permit private key,
so the account is identity-only: shared keys and anonymous access are off, TLS 1.2 or later is required and data
is encrypted twice. The account keeps a public endpoint so that GitHub-hosted runners can reach it; access is by
Azure role only (`Storage Blob Data Contributor` for the deployment identity, per container). This is the one
accepted finding of the Terraform security scan and is annotated beside the resource.

## First creation

The state account cannot hold its own first state, so `state-create` creates it once, run locally by an
engineer who is signed in with the Azure CLI. It is the only operation allowed to use local state.

```powershell
az login --tenant <tenant-id>
uv run --project code/tools --locked python code/tools/scripts/terraform_delivery.py --operation state-create --environment int --root state --plan-container tfplans
uv run --project code/tools --locked python code/tools/scripts/terraform_delivery.py --operation state-create --environment int --root state --plan-container tfplans --confirm-apply
```

The first command only plans. The second applies and then runs `init -migrate-state -force-copy` with the `state`
backend file. It retries the migration while the new role assignment propagates, confirms that the remote blob
exists, and only then removes the local state. If the migration fails, the local state is kept and nothing is
removed. The command refuses to run when the backend blob already exists.

## Every other run

All other operations refuse a missing or unreachable backend and never fall back to local state. They read the
reviewed backend file `<root>.azurerm.tfbackend.json` of the environment and authenticate with OIDC and Azure AD
only. The workflows take a lock with a wait of five minutes so that two runs cannot change the same state.

## Sensitivity

Treat the storage account and its versions as secret-bearing. Grant roles to people only for a reason and a
period, never read a state blob into a ticket or a log, and rotate the two secrets (see
[Environment bring-up](environment-bring-up.md)) after any suspected exposure.

## Recovery

| Situation | Action |
|-----------|--------|
| A run left a lock | The blob lease expires after the lock timeout. Read the holder in the run's log before releasing it |
| A state blob was overwritten or deleted | Restore a previous blob version or the soft-deleted blob within the retention days |
| The account was deleted | Remove the lock deliberately only for a planned teardown. After an accidental deletion, restore the soft-deleted container if it is within retention, otherwise recreate with `state-create` and import the other roots' resources |

The destroy input of the workflows is refused for the `state` root. Removing the state storage is a deliberate
manual operation.

## Verification

Offline, without a credential: `verify_terraform.py check` runs formatting, validation and the native tests of
every root and module with mocked providers, and `validate_environment.py` checks the environment files.
