# Contract: Environment Files

One Terraform definition per stack. Environment-specific data lives only in two files per stack and environment.

## Files

| Purpose | Name pattern | Formats |
| --- | --- | --- |
| Backend configuration | `config/<name>.<stack>.azurerm.tfbackend[.json]` | HCL or JSON (both natively accepted by the pinned Terraform 1.17.0) |
| Variables | `config/<name>.<stack>.tfvars[.json]` | HCL or JSON |

`<stack>` is `bootstrap` or `platform`. `<name>` is any file-name prefix; it carries no meaning to the configuration.

## Rules

- The Terraform source contains no condition, validation or branch on an environment name.
- The backend file supplies the state location, that stack's own state key and identity values. The `bootstrap` and `platform` stacks each have their own backend file with their own `key`; bootstrap holds no key for the platform stack. The configuration does not construct a state key from an environment variable.
- No list of allowed stage names exists in Terraform, workflows or any data file; `environment` is a free-form workflow input and the protected deployment environment is named `<name>-terraform-<stack>-<operation>` in repository settings.
- The variable file supplies every environment-specific value, including subscription, sizing, retention and tags. An optional free-form `label` may feed naming and tags only.
- Secrets never appear in either file; only non-secret reviewed values are committed, as synthetic `.example` files for `dev`, `test` and `prod` that differ only in data.
- The delivery helper reads the state key and the protected deployment-environment name from these files and the workflow inputs; it does not derive them from a name format.

## Adding an environment

1. Copy any example pair, rename with the new prefix, change the data.
2. Create the protected deployment environment in the repository settings (protection is never created or weakened by tooling).
3. Run the existing plan-review then protected-apply workflow.

No Terraform source, workflow, or script edits are required (SC-008).

## Verification

- Static check fails when Terraform source branches on an environment name.
- Example validation covers all three examples through the same code path.
- Helper tests cover a fourth, unknown name to prove no per-name logic remains.
