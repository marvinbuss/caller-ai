# Contract: Tooling, Scripts and Workflows

All commands run from the repository root. Exit code 0 means success. Non-zero means failure with a
single-line JSON error on standard error that never echoes a secret or an input value.

## `code/tools/scripts/validate_environment.py`

Validates every directory under `code/infra/environments/` before any resource is touched (FR-013).

```text
uv run --project code/tools --locked python code/tools/scripts/validate_environment.py [--environment NAME]
```

| Item | Behavior |
|---|---|
| Input | All environment directories, or one with `--environment`. An incomplete other environment is reported for itself only. |
| Checks | File set is exactly the four expected files. Strict JSON. Rules in [environment-files.md](environment-files.md). Cross-environment uniqueness. |
| Output | `{"result": "environment_valid", "environments": ["int"]}` on success. Error lines per finding on failure. |
| Used by | `ci.py check`, `terraform_delivery.py --operation check`, every Terraform workflow before the plan. |

## `code/tools/scripts/check_terraform_structure.py`

Enforces the layout (FR-001 to FR-003, FR-005, FR-042, FR-044, SC-007).

| Check | Fails when |
|---|---|
| Root files | A root lacks `variables.tf`, `providers.tf`, `outputs.tf` or `main.tf`, or a service module call sits outside its service file. |
| Module files | A reusable module lacks `README.md`, `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf` or `tests/`. |
| Descriptions | A variable or output has no description. |
| Nested calls | A reusable module calls another module outside `main.tf`. |
| Network owner | A virtual network, subnet, network security group or rule, route table or route appears outside `bootstrap` and `modules/network`. |
| Custom outbound | Any custom security rule, route, NAT gateway or firewall resource exists. |
| Directory objects | Any `azuread_` resource, data source or provider appears. |
| Leftovers | Files named after retired stages, AKS-era resources, the old `platform` deployment name, or App Service exist. |

Success output: `{"result": "structure_valid", "roots": 3, "modules": 13}`.

## `code/tools/scripts/verify_terraform.py`

Existing tool, changed for the new roots and modules. `check` runs `fmt`, `validate`, `test` for every root
and module, environment validation, the structure check, and a pinned Trivy `config` scan that fails on HIGH
or CRITICAL findings (FR-030). A seeded misconfiguration in a test fixture must fail it.

## `code/tools/scripts/terraform_delivery.py`

| Operation | Behavior |
|---|---|
| `check` | Environment validation first, then slice the variables, `fmt`, `validate` and `test` for the root. |
| `guard` | Verifies GitHub environment protection and the OIDC context. |
| `plan` | Plans against the remote backend with a lock wait, saves the plan to the plan container, prints its hash and change count. For `app_environment` it passes `TF_VAR_management_signin_secret` from the environment secret and never logs it. |
| `apply` | Applies only a saved plan whose hash matches and that a protected reviewer approved. After an `app_environment` apply it runs the environment check and fails the job if it fails (FR-029). |
| `state-create` | Run locally by an engineer: `init -backend=false`, plan, a confirmed apply, `init -migrate-state -force-copy` with the state backend file, confirm the blob, remove local state. Refuses if the backend blob exists. The only operation allowed to use local state (FR-007). |
| `--expect-no-changes` | Fails when the plan has changes (SC-002). |
| `--destroy` | Destroy plan for `bootstrap` and `app_environment` only, refuses `state`, still needs the protected apply (SC-003). |

Arguments: `--environment`, `--root` (`state`, `bootstrap`, `app_environment`), `--plan-container`,
`--confirm-apply` (state-create). The variable slice is written to a temporary directory outside the
repository and removed afterwards.

## `code/tools/scripts/check_environment.py`

```text
uv run --project code/tools --locked python code/tools/scripts/check_environment.py --environment int [--test-alert] [--functional] [--json PATH]
```

| Item | Behavior |
|---|---|
| Inputs | The `environment_manifest` output, the environment files, Azure Resource Manager read access through the OIDC identity. `--functional` also needs a tester token: the pipeline gets it from the deployment identity, an operator uses their own. |
| Result | `{"environment": "int", "result": "pass", "checks": [{"id": "...", "service": "...", "status": "pass", "detail": "..."}]}` |
| Exit | 0 only when `result` is `pass`. |

| Check id | Verifies |
|---|---|
| `services-provisioned` | Each declared service exists with the configured SKU, name and settings, with no public access where required. |
| `settings-complete` | Each container receives every setting in [application-settings.md](application-settings.md), no secret as a plain value, and the start reason code is reported if a revision failed. |
| `revisions-healthy` | The latest revision of each app reports healthy within 5 minutes of the deploy finishing (SC-012). |
| `images-running` | Each app runs exactly the configured tag or digest. A failed pull is reported with the application and version (FR-037). |
| `public-access-matches` | Environment public network access, each app's external ingress and the allow-list rules equal the flag. When the ranges are not open, a request from the runner is refused (SC-005). With an open range the check reports that no source restriction applies. |
| `no-custom-outbound` | The network security group has only default rules, the route table has no routes, and no NAT gateway or firewall exists (FR-044, SC-014). |
| `private-endpoints-approved` | Every private endpoint connection is approved, including the Foundry account (reports pending). |
| `model-access` | The account and both deployments exist and the runtime identity holds the role (FR-039). |
| `runtime-role-assigned` | The runtime identity holds the `runtime` application role on the management registration (FR-052). |
| `registration-and-roles-exist` | The sign-in registration and the roles named in `identity.roles` exist, read through Microsoft Graph with the deployment identity's `Application.Read.All` permission. A failure names the setting (FR-042). |
| `secrets-present` | The two vault secrets exist (names, never values) and are not older than their validity. |
| `signin-redirect-matches` | The management app's default domain equals the registered redirect address. On the first deployment it fails until the redirect phase has run (expected). |
| `state-isolated` | The environment's state keys are distinct and the state storage has shared keys disabled. |
| `alert-test` | With `--test-alert`, sends an action group test notification and reports acceptance. |
| `functional-journey` | With `--functional` (the pipeline passes it after each `app_environment` apply), from the runner and signed in as the deployment identity that holds the tester role (FR-053): the runtime health probe, an unauthenticated request to the management app that is redirected to sign-in, an authenticated configuration read, a text test conversation that returns a model reply within 15 seconds, and a speech session start and confirmed stop (FR-028, FR-048, SC-012, SC-015). When public access is disabled it is skipped with the reason `public_access_disabled`, the only skip that does not fail the run. |

## `code/infra/scripts/identity/Initialize-Identity.ps1`

PowerShell 7 and Azure CLI. Run by a directory administrator, once per environment (FR-040 to FR-043, FR-052).

```text
pwsh code/infra/scripts/identity/Initialize-Identity.ps1 -Environment int [-Phase registration|redirect] [-WhatIf]
```

| Item | Behavior |
|---|---|
| Inputs | `-Environment` (reads `code/infra/environments/<env>/<env>.tfvars.json`), `-Phase` (default `registration`), optional `-WhatIf` preview. |
| Preflight | Verifies the signed-in account, tenant and subscription match the file and that the account has the required directory and resource group rights. Stops before any change if not, naming the missing right. |
| Phase `registration` | Creates, idempotently: the resource group if absent, the runtime's user-assigned identity, the management registration with its service principal and the three application roles named in the variable file, the role assignment for the runtime identity, the tester role assignment for the deployment identity (`environment.client_id`) and its read-only Graph permission `Application.Read.All`, the group assignment (`identity.user_group_id`), and a client secret valid at most 90 days when none valid exists. |
| Phase `redirect` | After the first app environment deployment, assigns the `management` role of the runtime registration to the management identity and registers the management app's callback address (the FQDN read with `az containerapp show`, app name from `app_environment.container_apps.management.app_name`) and records nothing else. |
| Secret handling | Writes only the client secret to `code/.local/identity/<env>/management-signin-secret` (ignored by git, restricted access). Never prints it, never logs it, never creates another secret, certificate or key. |
| Output | A table of values to copy into `app_environment.identity`: tenant ID, `management_app_client_id`, `runtime_identity_name`, role names. In `-WhatIf` it prints what it would do, writes nothing and exits 0. |
| Exit codes | 0 success or preview, 1 preflight failure, 2 invalid input, 3 an Azure CLI step failed (the step is named). |

After a successful `registration` run the administrator stores the secret once as `MANAGEMENT_SIGNIN_SECRET`
in the protected GitHub environment and deletes the local file (FR-046).

## `code/tools/scripts/verify_published_images.py`

| Operation | Behavior |
|---|---|
| `tag-free` | Fails if the planned tag already exists for the repository. |
| `anonymous-pull` | Fetches the manifest without credentials and fails if it cannot (package not public yet). |
| `digest` | Prints the digest of the pushed tag. |

## Workflows

| Workflow | Trigger | Jobs and rules |
|---|---|---|
| `terraform-state.yml` | `workflow_dispatch` (`environment`, `apply_changes`) | Reusable deploy with `root: state`. First creation is `state-create`, not this workflow. |
| `terraform-bootstrap.yml` | `workflow_dispatch` | Reusable deploy with `root: bootstrap`. |
| `terraform-app-environment.yml` | `workflow_dispatch` (also `expect_no_changes`, `destroy`) | Reusable deploy with `root: app_environment` and the secret from the protected environment, then the environment check. |
| `terraform-deploy.yml` | `workflow_call` | `plan` and `apply` jobs in the GitHub environment named like the input environment, with required reviewers. OIDC only. Concurrency group per environment and root. For `app_environment` a final step runs the check with `--functional` and uploads its JSON. |
| `images.yml` | push to `main`, `v*` tag, `workflow_dispatch` | Build, Trivy scan (secrets, HIGH and CRITICAL), `tag-free`, push, `anonymous-pull`, digests in the summary. `contents: read`, `packages: write`. Actions pinned by commit SHA. No step deletes or prunes package versions. |
| `ci.yml` | pull request, push | Environment and structure checks, Terraform tests and security scan, tooling and application tests, browser tests, Pester. |

GitHub settings used (names only): environment `int`; secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID` (identifiers, not credentials) and `MANAGEMENT_SIGNIN_SECRET` in it; variable
`TF_PLAN_CONTAINER`. The deployment identity has one federated credential for the subject
`repo:marvinbuss/caller-ai:environment:int`.

## Image references

| Repository | Tags |
|---|---|
| `ghcr.io/marvinbuss/caller-ai/runtime` | `sha-<40 hex>` per commit, `v<semver>` on a `v*` tag |
| `ghcr.io/marvinbuss/caller-ai/management` | `sha-<40 hex>` per commit, `v<semver>` on a `v*` tag |

Both are public. `latest` is never published or deployed.
