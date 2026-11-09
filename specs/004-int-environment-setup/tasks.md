---

description: "Task list for the First Internal Test Environment"
---

# Tasks: First Internal Test Environment

**Input**: Design documents from `/specs/004-int-environment-setup/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), the four contracts ([environment-files.md](contracts/environment-files.md), [deployment-interfaces.md](contracts/deployment-interfaces.md), [application-settings.md](contracts/application-settings.md), [tooling-and-workflows.md](contracts/tooling-and-workflows.md)), [quickstart.md](quickstart.md)

**Tests**: Included. FR-035, Principle IX and the plan's test plan require tests for every module, root, tool, script and application change. Each task that creates code also creates its tests, and the tests are written first.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story, with the exception of the in-process journey test (T094) and the pipeline journey (T097, T098), which need the live mode from US5. The first live apply of `int` (Phase 9) also needs the application live mode from US5, because the published images refuse `live` mode today.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1 to US6, mapping to the user stories in [spec.md](spec.md)
- Every task names its files. Paths are relative to the repository root.

## Path Conventions

- Terraform: `code/infra/terraform/{state,bootstrap,app_environment}/` and `code/infra/terraform/modules/<module>/`
- Environment files: `code/infra/environments/int/`
- Identity script: `code/infra/scripts/identity/`
- Tooling: `code/tools/scripts/` with tests in `code/tools/tests/`
- Runtime: `code/backend/src/caller_runtime/` with tests in `code/backend/tests/{unit,contract,integration}/`
- Management: `code/management/management_app/` with tests in `code/management/tests/{unit,contract,integration,browser}/`
- Workflows: `.github/workflows/`
- Documentation: `docs/`, `docs/runbooks/`, `docs/acceptance/`
- Mirror-feed development setup: run `. .\code\.local\feeds.ps1` before `ci.py check` and `verify_docs.py`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Clear the retired layout and settle the open decisions.

- [X] T001 Ratify in the constitution, through a separate `/speckit-constitution` update to [.specify/memory/constitution.md](../../.specify/memory/constitution.md), that Principle VIII governs infrastructure resources and that directory objects are administered by the reviewed, tested and idempotent identity script (FR-043), and record the maintainers' approval. The identity script tasks (T039 to T041) start only after this is recorded. The spec, plan and research already carry the other analysis decisions (own sign-in, required `identity.user_group_id`, open range for `int`, functional journey in the pipeline, FR-053).
- [X] T002 Delete the retired layout (FR-015, research R-21): `code/infra/terraform/modules/platform/`, `code/infra/terraform/environments/`, `code/infra/terraform/config/`, `code/infra/terraform/test-fixtures/`, `code/infra/terraform/bootstrap/main.tf`, `variables.tf`, `versions.tf`, `tests/state.tftest.hcl`, and `.github/workflows/terraform-infrastructure.yml`.
- [X] T003 [P] Choose and record the exact `azapi` and `tls` provider versions in [docs/library-decisions.md](../../docs/library-decisions.md) (use `azapi` only for vault secret writes and read-only data sources that `azurerm` lacks, such as model deployments, `tls` only for the permit key pair, `azuread` never), and note the `azurerm` 5.11.0 pin.
- [X] T004 [P] Prove that `code/.local/identity/` is ignored by git with a test in `code/tools/tests/test_static_checks.py`, and adjust `.gitignore` only if the test fails (FR-040, FR-046).

**Checkpoint**: Open items answered, retired files gone, provider versions fixed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Environment files, their validation, and the delivery tooling that every deployment uses. No user story can run without these.

**CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 Write failing tests for environment validation in `code/tools/tests/test_validate_environment.py` covering every rule in [environment-files.md](contracts/environment-files.md): file set exactly four files; strict JSON with no duplicate or unknown top-level keys; `environment.name` is one of `int`, `dev`, `tst`, `prd` and equals the directory and file prefix; `public_access` absent means disabled, needs a non-empty allow-list when enabled, and accepts the open ranges `0.0.0.0/0` and `::/0` only when listed explicitly, with a warning line and exit 0 (FR-016, FR-019); `identity.user_group_id` is a required GUID; image has exactly one of `tag` or `digest`, tag matches `sha-<40 hex>` or `v<semver>`, digest matches `sha256:<64 hex>`, `latest` rejected; `model.text_deployment` and `model.live_deployment` non-empty and different; `container_apps_subnet_cidr` is at least a /27 inside `address_space` and does not overlap `private_endpoint_subnet_cidr`; `signing_key.key_id` matches `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`; `storage_account_name` is "3 to 24 lowercase letters or digits"; `state_retention_days` "7 to 365"; secret-like key names (`secret`, `password`, `key`, `token`, `connection_string`, `sas`) rejected unless on the allow-list `secret_store`, `signing_key`, `key_id`, `soft_delete_days`, `max_input_tokens`, `max_output_tokens`; secret-like values rejected; cross-environment uniqueness of subscription IDs, state keys and global names; an incomplete other environment is reported for itself only; copying `int` to a temporary `dev` with only names changed validates (SC-004, SC-006); one-line JSON errors with `file`, `setting`, `problem` and never the offending value (FR-009 to FR-014, SC-008).
- [X] T006 Implement `code/tools/scripts/validate_environment.py` (`--environment NAME` optional) so the T005 tests pass; success prints `{"result": "environment_valid", "environments": [...]}`.
- [X] T007 Create the `int` configuration in `code/infra/environments/int/int.tfvars.json`, `state.azurerm.tfbackend.json`, `bootstrap.azurerm.tfbackend.json` and `app_environment.azurerm.tfbackend.json` from the example in [environment-files.md](contracts/environment-files.md), using zero GUIDs and `example.invalid` for values the owner supplies (open item 5), `public_access.enabled` true with `allowed_cidrs` `["0.0.0.0/0"]` (so GitHub-hosted runners can reach `int`), a zero-GUID `identity.user_group_id`, and `use_cli` false in every backend file; confirm `validate_environment.py` passes.
- [X] T008 Write failing tests in `code/tools/tests/test_terraform_delivery.py` for variable slicing and backend handling: each root receives only `environment` plus its own object through a temporary file outside the repository that is removed afterwards; the roots are `state`, `bootstrap`, `app_environment`; `<root>.azurerm.tfbackend.json` is found per environment; environment validation runs first and stops the run on failure; a missing or unreachable backend or local state is refused naming the missing prerequisite (FR-007); `check` runs `fmt`, `validate`, `test`.
- [X] T009 Update `code/tools/scripts/terraform_delivery.py` for three roots, per-root variable slicing, backend discovery, validation-first and the local-state refusal, so the T008 tests pass.
- [X] T010 Write failing tests in `code/tools/tests/test_terraform_delivery.py` for `guard`, `plan` and `apply`: protected GitHub environment and OIDC context verified; plan saved to the `tfplans` container with lock wait, hash and change count printed; apply only for a saved plan whose hash matches; `TF_VAR_management_signin_secret` passed through the environment for `app_environment` and never logged or printed (FR-046); no secret in any output.
- [X] T011 Update `code/tools/scripts/terraform_delivery.py` `guard`, `plan` and `apply` to pass the T010 tests.
- [X] T012 [P] Write failing tests in `code/tools/tests/test_verify_terraform.py` that `verify_terraform.py check` discovers the three roots and all thirteen modules, runs `fmt`, `validate` and `test` for each, and runs environment validation (FR-030).
- [X] T013 Update `code/tools/scripts/verify_terraform.py` to pass the T012 tests (the Trivy configuration scan is added in US4).
- [X] T014 [P] Write failing tests in `code/management/tests/unit/test_entra_login_settings.py` (spike S-4) that the Entra login flow takes its client secret from `CALLER_ENTRA_CLIENT_SECRET` and its redirect from `CALLER_MANAGEMENT_ORIGIN`, and record the callback path in [research.md](research.md) R-04 for `Initialize-Identity.ps1` (T041).

**Checkpoint**: `uv run --project code/tools --locked python code/tools/scripts/validate_environment.py` passes on `int`, and the delivery tests pass offline.

---

## Phase 3: User Story 1 - Stand up the `int` environment from configuration alone (Priority: P1) MVP

**Goal**: Three deployments (state, bootstrap, app environment), the identity script, the images and the workflows exist, so one variable file and three backend files describe `int`.

**Independent Test**: Offline, `verify_terraform.py check` passes for all roots and modules and each root's composition tests assert the wired services. The live apply is Phase 9.

### Reusable modules (write the module tests first, inside each task)

All thirteen modules have `README.md`, `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, extra files per resource and `tests/*.tftest.hcl` with mock providers. Every input and output has a description. Behavior follows [deployment-interfaces.md](contracts/deployment-interfaces.md). Modules start private; public access arrives in US3 (FR-002, FR-003, FR-023, FR-024).

- [X] T015 [P] [US1] Create module `code/infra/terraform/modules/state-backend/`: storage account, `tfstate` and `tfplans` containers, shared keys off, identity-only access, TLS 1.2, versioning, soft delete, delete lock, container-scoped writer roles for `writer_principal_ids`; validate `storage_account_name` "3 to 24 lowercase letters or digits" and `state_retention_days` "7 to 365"; tests assert every rule and that no shared key or SAS output exists (FR-031).
- [X] T016 [P] [US1] Create module `code/infra/terraform/modules/network/`: virtual network, a Container Apps subnet delegated to `Microsoft.App/environments` ("at least /27"), a private-endpoint subnet, one network security group with only the default rules and one route table with no routes, both associated to the Container Apps subnet; tests assert no custom security rule, no route, no NAT gateway and no firewall (FR-044).
- [X] T017 [P] [US1] Create module `code/infra/terraform/modules/private-dns/`: one private DNS zone per purpose (`cosmos`, `vault`, `openai`, `cognitive`, `ai_services`, `monitor`, `oms`, `ods`, `agentsvc`, `blob`) with virtual network links, output a map of zone IDs; tests assert no records are created.
- [X] T018 [P] [US1] Create module `code/infra/terraform/modules/private-endpoint/`: endpoint for a target ID and subresource in a given subnet with a zone group, outputs the endpoint ID and connection state; tests assert it never approves a cross-subscription connection.
- [X] T019 [P] [US1] Create module `code/infra/terraform/modules/container-app-environment/`: workload-profiles environment with the Consumption profile declared at creation, external, subnet from input, log destination set to the Log Analytics workspace, `public_network_access` `Disabled` for now; outputs ID and default domain; tests assert it is not internal-only and adds no outbound rule.
- [X] T020 [P] [US1] Create module `code/infra/terraform/modules/container-app/`: single-revision app with a user-assigned identity, image from repository plus exactly one of tag or digest (tag matches `sha-<40 hex>` or `v<semver>`, digest matches `sha256:<64 hex>`, `latest` rejected), `cpu` and `memory` defaulting to `0.5` and `1Gi` with a valid Consumption combination, name "2 to 32 lowercase letters, digits or hyphens", `min_replicas` and `max_replicas` both `1`, startup and readiness probe `/readyz`, liveness `/healthz`, plain settings and Key Vault secret references only (no plain secret value); ingress `external_enabled` false for now; tests assert each rule and the absence of any other setting.
- [X] T021 [P] [US1] Create module `code/infra/terraform/modules/data-store/`: Cosmos DB for NoSQL serverless, local authentication disabled, public access off, continuous backup (`backup_tier`), one database, containers `configuration` (`/organizationId`, no default TTL), `configuration-audit` (`/dayBucket`), `call-sessions`, `call-audit` (`/dayBucket`), `text-conversations` (`/ownerKey`, item TTL), data role assignments per principal scoped to the listed containers only (runtime: `call-sessions`, `text-conversations`; management: `configuration`, `configuration-audit`, `call-audit`, confirmed by T091), optional private endpoint inputs; tests assert the container list and partition keys exactly and that no role is wider than its container list (S-13 is checked against the repositories in T091).
- [X] T022 [P] [US1] Create module `code/infra/terraform/modules/secret-store/`: Key Vault with RBAC, public access disabled, purge protection, `soft_delete_days`, reader roles for given principals, private endpoint inputs; tests assert it creates no secret.
- [X] T023 [P] [US1] Create module `code/infra/terraform/modules/key-vault-secret/`: `azapi_resource` of type `Microsoft.KeyVault/vaults/secrets` with a sensitive value passed through the provider's sensitive body input, outputs the versionless secret ID only; tests assert the value is never an output, that the plan JSON contains no secret value (FR-046), and that no data-plane `azurerm_key_vault_secret` is used (research R-07); a second plan in the module test shows no change from the sensitive body (FR-026).
- [X] T024 [P] [US1] Create module `code/infra/terraform/modules/permit-key/`: `tls_private_key` RSA 2048, input `key_id` matching `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`, outputs the public key PEM and a sensitive private key for the vault module only; tests assert no file is written (FR-051).
- [X] T025 [P] [US1] Create module `code/infra/terraform/modules/workload-identity/`: user-assigned identity for the management app, role assignments it needs on the vault and the data store, outputs identity, client and principal IDs; tests assert it creates no directory object (FR-032, FR-042).
- [X] T026 [P] [US1] Create module `code/infra/terraform/modules/model-access/`: data source for the existing account and `azapi` data sources for its deployments, least-privilege role for the runtime principal, private endpoint to the account through the private-endpoint module inputs, preconditions that fail naming the setting when the account or a deployment is missing (FR-039; the private connection approval and the role are reported by the environment check, T051); tests assert it creates neither the account nor its deployments.
- [X] T027 [P] [US1] Create module `code/infra/terraform/modules/monitoring/`: Log Analytics workspace (`retention_days`, `daily_cap_gb`), workspace-based Application Insights with private link scope inputs, action group with email receivers, metric alerts for 5xx request count, restart count, replicas below one, CPU and memory, plus scheduled log-query alerts over the applications' structured events for dependency failures and latency (confirm the event fields first); validation fails an alert lacking an owner and runbook URL; tests assert each alert, the cap and the validation.

### Deployments (roots)

- [X] T028 [US1] Create the state root in `code/infra/terraform/state/` (`variables.tf` with `environment` and `state`, `providers.tf`, `main.tf`, `state_backend.tf`, `outputs.tf` with `state_backend`, `tests/state.tftest.hcl`): composes `state-backend`, no directory provider (FR-001, FR-006); tests assert the wiring and outputs.
- [X] T029 [US1] Create the bootstrap root in `code/infra/terraform/bootstrap/` (`variables.tf` with `environment` and `bootstrap`, `providers.tf` with the `azurerm` backend, `main.tf`, `network.tf`, `private_dns.tf`, `outputs.tf` with `network` and `private_dns_zone_ids`, `tests/composition.tftest.hcl`): composes `network` and `private-dns`; tests assert it is the only root that creates network resources and that it creates no state storage (FR-001, FR-005, FR-006, FR-008, FR-044).
- [X] T030 [US1] Create the app environment root skeleton in `code/infra/terraform/app_environment/` (`variables.tf` with `environment`, `app_environment` and sensitive `management_signin_secret`, `providers.tf` with the `azurerm` backend, `azurerm`, `azapi` and `tls`, `main.tf` with read-only bootstrap state, the existing model account and the script-created runtime identity as data sources with preconditions naming the setting on failure for the model account, its deployments and the runtime identity (FR-039); the sign-in registration and roles cannot be read without `azuread`, so the environment check reports them (FR-042), `outputs.tf` with `environment_manifest` and `workload_identity_client_ids`); no `azuread` anywhere.
- [X] T031 [P] [US1] Add `code/infra/terraform/app_environment/data_store.tf` and `secret_store.tf`: modules with private endpoints in the subnet and zone IDs from the bootstrap outputs only (FR-045).
- [X] T032 [P] [US1] Add `code/infra/terraform/app_environment/signing_key.tf` and `secrets.tf`: `permit-key` and two `key-vault-secret` calls for `management-signin-secret` and `permit-private-key`; the public key flows to the runtime setting `CALLER_PERMIT_VERIFICATION_KEYS` (FR-046, FR-051). <!-- pragma: allowlist secret -->
- [X] T033 [P] [US1] Add `code/infra/terraform/app_environment/monitoring.tf`: `monitoring` module and the log destination of the environment.
- [X] T034 [P] [US1] Add `code/infra/terraform/app_environment/workload_identity.tf` and `model_access.tf`: management identity, runtime identity attached from the data source, model access for the runtime principal.
- [X] T035 [US1] Add `code/infra/terraform/app_environment/container_apps.tf` after T031 to T034 (it consumes their outputs): Container Apps environment and runtime and management apps from the variable object, images per FR-037, one replica, derived origins `https://<app name>.<environment default domain>`, every setting in [application-settings.md](contracts/application-settings.md) on the right app (including the derived audiences, issuers, scope and approved hosts), the two secrets only as Key Vault references (FR-025, FR-047, FR-049).
- [X] T036 [US1] Write `code/infra/terraform/app_environment/tests/composition.tftest.hcl`: every service wired; no `azuread`, `azurerm_virtual_network`, subnet, network security group, route table or NAT gateway resource; no custom outbound rule; `management_signin_secret` sensitive and absent from every output and from the plan JSON; `environment_manifest` holds no secret value; images and settings match the variable object; fails naming the setting when the model account or identity is missing.

### One-time state creation

- [X] T037 [US1] Write failing tests in `code/tools/tests/test_terraform_delivery.py` for `--operation state-create`: `init -backend=false`, plan, confirmed apply (`--confirm-apply`), `init -migrate-state -force-copy` with `state.azurerm.tfbackend.json`, remote blob confirmed before the local state is removed, local state kept when migration fails, refuses when the backend blob already exists, the only operation allowed to use local state (FR-006, FR-007).
- [X] T038 [US1] Implement `state-create` in `code/tools/scripts/terraform_delivery.py` to pass the T037 tests, with a bounded retry for role assignment propagation before the migrate step (research R-08).

### Identity script (FR-040 to FR-043, FR-052)

- [X] T039 [P] [US1] Write failing Pester tests in `code/infra/scripts/identity/tests/Initialize-Identity.Tests.ps1` with a mocked `az`: `-WhatIf` changes nothing and exits 0; second run changes nothing; preflight failure (account, tenant, subscription or rights, including the right to consent to Graph application permissions through Privileged Role Administrator or Global Administrator) exits 1 before any change and names the missing right; invalid input exits 2; a failed step exits 3 and is named; the client secret is written only to `code/.local/identity/<env>/management-signin-secret` and never printed or logged; no other secret, certificate or key is created; the secret validity is at most 90 days; role names are read from the variable file; the tester role is assigned to the principal of `environment.client_id` (FR-053); `identity.user_group_id` is required and receives the editor and tester roles; `-Phase redirect` reads the management app's FQDN with `az containerapp show` (app name from `app_environment.container_apps.management.app_name`) and registers the callback address; the deployment identity receives the read-only Graph permission `Application.Read.All` (FR-053).
- [X] T040 [US1] Implement `code/infra/scripts/identity/Initialize-Identity.ps1` phase `registration`: reads `int.tfvars.json`, creates idempotently the resource group, the runtime's user-assigned identity, the management registration with service principal and the application roles named in the variable file (`identity.roles`), the runtime role assignment, the tester role assignment and the read-only Graph permission `Application.Read.All` for the deployment identity, the required `user_group_id` assignment and the client secret, and prints the values for `app_environment.identity`; passes the T039 registration tests.
- [X] T041 [US1] Implement phase `redirect` in `code/infra/scripts/identity/Initialize-Identity.ps1` (callback path confirmed by T014, address read with `az containerapp show`), passing the T039 redirect tests.

> Gate: the identity script tasks T039 to T041 are not merged before T001 (the constitution ratification) is recorded.

### Image publishing (FR-036 to FR-038)

- [X] T042 [P] [US1] Write failing tests in `code/tools/tests/test_verify_published_images.py` for operations `tag-free` (fails when the tag exists), `anonymous-pull` (manifest fetched without credentials) and `digest`, using a fake registry, and that `.github/workflows/images.yml` has no step that deletes or prunes package versions (FR-036).
- [X] T043 [US1] Implement `code/tools/scripts/verify_published_images.py` to pass the T042 tests.
- [X] T044 [US1] Create `.github/workflows/images.yml`: triggers push to `main`, `v*` tag, `workflow_dispatch`; builds `code/backend/Dockerfile` and `code/management/Dockerfile`; Trivy scan (digest-pinned image from `code/tools/scripts/audit_dependencies.py`) for secrets and HIGH or CRITICAL vulnerabilities blocks publishing; `tag-free`, push `ghcr.io/marvinbuss/caller-ai/runtime` and `/management` as `sha-<40 hex>` and `v<semver>`, `anonymous-pull`, digests in the summary; `contents: read`, `packages: write`; actions pinned by commit SHA; no delete or prune step.

### Delivery workflows

- [X] T045 [US1] Rewrite `.github/workflows/terraform-deploy.yml` as the reusable `workflow_call` for roots `state`, `bootstrap` and `app_environment`: `plan` job in environment `<env>-terraform-<root>-plan`, `apply` job in `<env>-terraform-<root>-apply` with required reviewers, OIDC only, concurrency group per environment and root, `MANAGEMENT_SIGNIN_SECRET` passed only for `app_environment`.
- [X] T046 [P] [US1] Create `.github/workflows/terraform-state.yml`, `.github/workflows/terraform-bootstrap.yml` and `.github/workflows/terraform-app-environment.yml` as `workflow_dispatch` callers (`environment`, `apply_changes`) of the reusable workflow.

**Checkpoint**: `uv run --project code/tools --locked python code/tools/scripts/verify_terraform.py check` passes for all roots and modules, the Pester suite passes, and the workflows lint cleanly.

---

## Phase 4: User Story 2 - Reproduce and trust the environment (Priority: P1)

**Goal**: A deployment proves itself. Invalid files stop before any resource is touched, a repeated deployment shows no changes, and an environment check reports each failing service.

**Independent Test**: Validation fails for a removed required value, `check_environment.py` passes on a deployed `int`, and a second plan has zero changes. Offline, the tool tests and the root variable-validation tests pass.

- [X] T047 [US2] Write failing tests in `code/tools/tests/test_check_environment.py` for the result format (`environment`, `result`, `checks[]` with `id`, `service`, `status`, `detail`), exit 0 only when no check failed, a skipped check failing the run unless allowed, and that no output contains a secret value or an input value.
- [X] T048 [US2] Implement the framework of `code/tools/scripts/check_environment.py` (arguments `--environment`, `--test-alert`, `--functional`, `--json PATH`, reading the `environment_manifest` output and Resource Manager read access through OIDC) to pass the T047 tests (FR-028).
- [X] T049 [US2] Write failing tests in `code/tools/tests/test_check_environment.py` for checks `services-provisioned`, `settings-complete` (every setting in [application-settings.md](contracts/application-settings.md) present, no secret as a plain value, start reason code reported), `revisions-healthy` (healthy within 5 minutes, SC-012) and `images-running` (the configured tag or digest, a failed pull named by application and version, FR-037), using fake Azure responses.
- [X] T050 [US2] Implement those four checks in `code/tools/scripts/check_environment.py`.
- [X] T051 [US2] Write failing tests in `code/tools/tests/test_check_environment.py` for `private-endpoints-approved` (pending is reported), `no-custom-outbound` (only default rules, no routes, no NAT gateway or firewall, FR-044), `model-access`, `runtime-role-assigned` (FR-052), `registration-and-roles-exist` (a Graph read with the deployment identity's token: the sign-in registration and the roles named in `identity.roles` exist, FR-042), `secrets-present` (names only), `state-isolated` (distinct keys, shared keys disabled) and `alert-test` (action group test notification accepted).
- [X] T052 [US2] Implement those checks and `--test-alert` in `code/tools/scripts/check_environment.py`.
- [X] T053 [US2] Write failing tests in `code/tools/tests/test_terraform_delivery.py` for `--expect-no-changes` (fails when the plan has changes, SC-002) and `--destroy` (destroy plan for `bootstrap` and `app_environment` only, refuses `state`, still needs the protected apply, SC-003).
- [X] T054 [US2] Implement `--expect-no-changes` and `--destroy` in `code/tools/scripts/terraform_delivery.py`.
- [X] T055 [US2] Update `.github/workflows/terraform-deploy.yml` so that after an `app_environment` apply it runs `check_environment.py`, uploads the JSON result and fails the job when the check fails (FR-029), and add the inputs `expect_no_changes` and `destroy` to `.github/workflows/terraform-app-environment.yml` and `.github/workflows/terraform-bootstrap.yml`.
- [X] T056 [P] [US2] Mirror the file rules as `validation` blocks whose messages name the setting (FR-013) in `code/infra/terraform/{state,bootstrap,app_environment}/variables.tf`, with failure-path tests in each root's `tests/` directory (a taken global name or missing value fails naming the setting).

**Checkpoint**: Tool and root tests pass offline. The live check and repeat plan run in Phase 9.

---

## Phase 5: User Story 3 - Control public access per environment (Priority: P2)

**Goal**: One flag and allow-list switch exposure of exactly the two designated entry points, and turning it off removes exposure without touching data.

**Independent Test**: Root tests show the expected ingress and rules for flag on and off, the environment check's `public-access-matches` passes, and unrelated resources are unchanged between the two plans.

- [X] T057 [P] [US3] Write failing module tests for public access in `code/infra/terraform/modules/container-app-environment/tests/` (`public_network_access` `Enabled` or `Disabled`) and `code/infra/terraform/modules/container-app/tests/` (`ingress.external_enabled` follows the flag, one `Allow`-only `ip_security_restriction` per range, no rule when disabled, enabled without a range rejected).
- [X] T058 [US3] Add the public access inputs to `code/infra/terraform/modules/container-app-environment/` (`main.tf`, `variables.tf`) and `code/infra/terraform/modules/container-app/` (`main.tf`, `variables.tf`) so the T057 tests pass.
- [X] T059 [US3] Wire `environment.public_access` through `code/infra/terraform/app_environment/container_apps.tf` and add the variable validation to `code/infra/terraform/{state,bootstrap,app_environment}/variables.tf`: "open ranges `0.0.0.0/0` and `::/0` allowed only when listed explicitly, with a warning", valid CIDRs, allow-list required when enabled (FR-019).
- [X] T060 [US3] Add root tests in `code/infra/terraform/app_environment/tests/public_access.tftest.hcl`: flag absent or off gives none, flag on gives external ingress and one rule per range for both apps (an explicit `0.0.0.0/0` included), data store, vault and monitoring stay private in both, only exposure settings differ between the runs (FR-017, FR-018, FR-020, FR-021), the container environment logs ingress to the workspace so refusals are recorded.
- [X] T061 [US3] Write failing tests in `code/tools/tests/test_check_environment.py` for `public-access-matches` (environment public network access, each app's external ingress and the rules equal the flag; when the ranges are not open, a request from the runner is refused, SC-005; with an open range the check reports that no source restriction applies), then implement it in `code/tools/scripts/check_environment.py`.

**Checkpoint**: Flag on and off verified offline. The live toggle is in Phase 9.

---

## Phase 6: User Story 4 - Navigate and extend the infrastructure code (Priority: P2)

**Goal**: The layout is enforced by a check that runs in CI, and the Terraform security scan runs with it.

**Independent Test**: `check_terraform_structure.py` passes on the repository and fails for a merged file or a module without inputs or outputs.

- [X] T062 [US4] Write failing tests in `code/tools/tests/test_check_terraform_structure.py` using good and bad fixture trees: missing `variables.tf`, `providers.tf`, `outputs.tf` or `main.tf` in a root; a service module call outside its service file; a module missing `README.md`, `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf` or `tests/`; a variable or output without a description; a nested module call outside `main.tf`; a network resource outside `bootstrap` and `modules/network`; any custom security rule, route, NAT gateway or firewall; any `azuread` resource, data source or provider; unpinned provider or Terraform versions (FR-004); leftover retired files.
- [X] T063 [US4] Implement `code/tools/scripts/check_terraform_structure.py` to pass the T062 tests; success prints `{"result": "structure_valid", "roots": 3, "modules": 13}`; the roots and modules keep the layout of FR-001 and FR-002 (SC-007).
- [X] T064 [P] [US4] Write failing tests in `code/tools/tests/test_verify_terraform.py` that the pinned Trivy `config` scan fails on a seeded misconfiguration in `code/tools/tests/fixtures/terraform-misconfig/main.tf` and passes on a clean tree (HIGH or CRITICAL findings fail, FR-030).
- [X] T065 [US4] Add the Trivy `config` scan over `code/infra/terraform` to `code/tools/scripts/verify_terraform.py check`, using the digest-pinned image from `code/tools/scripts/audit_dependencies.py`, to pass the T064 tests.
- [X] T066 [US4] Add `validate_environment.py`, `check_terraform_structure.py` and the Terraform security scan to `code/tools/scripts/ci.py` so `ci.py check` runs them.
- [X] T067 [US4] Update `.github/workflows/ci.yml`: environment and structure checks, `terraform test` for every root and module, the Terraform security scan, new tooling tests, Pester 5 for `code/infra/scripts/identity/tests/`, the application and browser tests, and the in-process end-to-end test in `code/e2e/` (that job is enabled once T094 creates the project).
- [X] T068 [P] [US4] Generate and commit `.terraform.lock.hcl` for each root and module in `code/infra/terraform/` (`terraform providers lock` for `linux_amd64` and `windows_amd64`) so provider selections are reproducible (FR-004).
- [X] T069 [US4] Run the structure check against `code/infra/terraform/` and fix every reported gap in the roots and modules (file split, descriptions, README, tests).

**Checkpoint**: `ci.py check` runs structure and security checks and passes.

---

## Phase 7: User Story 5 - Use the configuration editor and test console in `int` (Priority: P2)

**Goal**: Both images run in a live mode composed only from settings and managed identities, and the editor and test console work against the runtime and the existing model.

**Independent Test**: Application tests with fake ports compose each app from a complete settings set, fail with the right reason code for each missing group, and the existing browser tests still pass. The conversation journey runs in the pipeline's environment check (T097, T098) and the editor walk in Phase 9.

### Runtime live mode (research R-10)

- [X] T070 [US5] Write failing tests in `code/backend/tests/unit/test_live_settings.py` for every runtime setting in [application-settings.md](contracts/application-settings.md): `CALLER_MODE` `live`; `CALLER_LIVE_REGION` one of `canadacentral`, `centralus`, `eastus2`, `swedencentral`; `CALLER_APPROVED_HOSTS` at most 8; `CALLER_AVAILABLE_LIVE_SESSIONS` "from 5 to 10000"; `CALLER_MAX_CLOCK_SKEW_SECONDS` "0 to 1"; `CALLER_PERMIT_VERIFICATION_KEYS` one to four entries; each fixed reason code (`settings_missing`, `settings_invalid`, `review_missing`, `identity_unavailable`, `store_unavailable`, `key_invalid`); no input value in any message; `local` or unset mode needs no other setting; test sessions stay within the admission limit (FR-050).
- [X] T071 [US5] Update `code/backend/src/caller_runtime/settings.py`: allow `live` when the live settings are valid (replacing the `live_mode_unavailable` refusal) and add a frozen `LiveSettings` reader with the fixed reason codes, to pass the T070 tests.
- [X] T072 [P] [US5] Write failing tests in `code/backend/tests/unit/test_azure_clients.py` (spike S-10) that a credential factory built on the managed identity named by `AZURE_CLIENT_ID` satisfies `CredentialPort`, Cosmos container factories for `call-sessions` and `text-conversations` satisfy `ContainerPort`, and readiness stays under its 0.25 s limit with fake clients.
- [X] T073 [US5] Implement `code/backend/src/caller_runtime/adapters/azure_clients.py` to pass the T072 tests, using no shared key or connection string for any Azure service.
- [X] T074 [US5] Write failing tests in `code/backend/tests/integration/test_live_composition.py` that `live.py` builds `RuntimeOptions`, the workload identity verifier, the permit verification keys, the configuration client and the readiness port from a complete settings set with fake ports, fails with `review_missing` for an empty `CALLER_REVIEW_REFERENCE`, fails with the matching reason code for each missing group, and, with the model or data store unavailable, the text endpoint returns a clear failure, the session is cleaned up and `/readyz` reports not ready.
- [X] T075 [US5] Implement `code/backend/src/caller_runtime/live.py` to pass the T074 tests, setting the five `*_reviewed` flags only from a non-empty review reference.
- [X] T076 [US5] Write failing tests in `code/backend/tests/contract/test_entrypoint.py` and implement `code/backend/src/caller_runtime/entrypoint.py` with `create_app_from_environment()` (local mode returns the existing health-only app, live mode composes through `live.py`), then change `code/backend/Dockerfile` to run it with `uvicorn --factory`.

### Management live mode (research R-11, R-12)

- [X] T077 [US5] Write failing tests in `code/management/tests/unit/test_live_settings.py` for every management setting in [application-settings.md](contracts/application-settings.md), the same reason codes plus `fingerprint_mismatch`, no secret or input value in any message, and unchanged local mode.
- [X] T078 [US5] Update `code/management/management_app/settings.py` to read the management live settings with fixed reason codes, to pass the T077 tests.
- [X] T079 [P] [US5] Implement `code/management/management_app/adapters/azure_clients.py` (credential from `AZURE_CLIENT_ID`, Cosmos container factories for `configuration`, `configuration-audit` and `call-audit`, secret settings read from the environment and never logged) with tests in `code/management/tests/unit/test_azure_clients.py`.
- [X] T080 [US5] Spike S-11: decide which settings enter the composition fingerprint (values derived from deployment outputs are either fixed by names in the variable file or excluded), record the owner-approved rule in [research.md](research.md) R-11, and write failing tests in `code/management/tests/unit/test_reviewed_fingerprint_authority.py` that a grant is issued only when the computed fingerprint equals `CALLER_REVIEWED_FINGERPRINT`.
- [X] T081 [US5] Go/no-go checkpoint: the owner approves the S-11 fingerprint rule recorded in [research.md](research.md) R-11. T082 and the tasks after it start only after the approval.
- [X] T082 [US5] Implement `code/management/management_app/live.py` part 1: `ManagementRequest`, the `ManagementResources` factory for the configuration and audit repositories with the empty approved capabilities, and `ReviewedFingerprintAuthority`, with tests in `code/management/tests/integration/test_live_composition.py`.
- [X] T083 [US5] Extend `code/management/management_app/live.py` with the token verifier (signing keys from the tenant's discovery document), the identity service, the session store and the runtime identity, with tests in `code/management/tests/integration/test_live_identity.py`; with the data store unavailable the management app returns a clear failure and `/readyz` reports not ready.
- [X] T084 [US5] Extend `code/management/management_app/live.py` with the test permit signer using `CALLER_SIGNING_KEY_PEM` and the test session client, with tests in `code/management/tests/integration/test_live_test_console.py`; with the runtime or model unavailable the test console shows a clear failure and the session is cleaned up.
- [X] T085 [US5] Add `create_app_from_environment()` to `code/management/management_app/management_app.py` (local mode unchanged) with tests in `code/management/tests/contract/test_entrypoint.py`.
- [X] T086 [US5] Write the fingerprint tool `code/management/management_app/fingerprint.py` that prints the fingerprint of the non-secret settings a deployment would give, per the rule from T080, with tests in `code/management/tests/unit/test_fingerprint.py`.
- [X] T087 [US5] Spike S-12: extend `code/tools/tests/test_images.py` with a container test that starts the management image with two different `API_URL` values and asserts the frontend follows the start-time value and that the exported frontend contains the editor and test console pages (the test must fail before the change).
- [X] T088 [US5] Change `code/management/Dockerfile` and `code/management/rxconfig.py` (and a small start script if needed) so the export registers the editor pages through a build-only stub composition and the runtime address is read at start, passing the T087 test; Reflex stays the frontend (Principle V, FR-038).

### Cross-application contracts

- [X] T089 [P] [US5] Add permit-key contract tests with a committed throwaway test key and token vector in `code/management/tests/contract/test_permit_vector.py` (signs RS256 with the key ID and issuer) and `code/backend/tests/contract/test_permit_vector.py` (verifies the same vector with one to four public keys, accepts an old and a new key during overlap, FR-051).
- [X] T090 [P] [US5] Extend `code/management/tests/contract/test_runtime_configuration.py` and `code/backend/tests/unit/test_connection_contract.py` so the runtime's identity token carries the configured audience and the `CALLER_RUNTIME_ROLE` role, and a call without the role is refused with one fixed reason (FR-052).
- [X] T091 [P] [US5] Add `code/tools/tests/test_cosmos_contract.py` (spike S-13) that compares the container names, partition keys and TTL defaults the repositories use with those declared in `code/infra/terraform/modules/data-store/main.tf`, and fix the module if they differ.
- [X] T092 [P] [US5] Add `code/tools/tests/test_application_settings_contract.py` that compares the `CALLER_*` names and `AZURE_CLIENT_ID` the two applications read with the names set in `code/infra/terraform/app_environment/container_apps.tf` and listed in [application-settings.md](contracts/application-settings.md), and that both secret settings are Key Vault references.
- [X] T093 [US5] Run the existing editor and test console browser tests in `code/management/tests/browser/` and update `code/management/tests/editor_app.py` only if the new composition requires it (FR-048).
- [X] T094 [US5] Create the test-only project `code/e2e/` (`pyproject.toml` depending on both applications by path, `tests/test_live_journey.py`) that composes the runtime and the management app in-process with fake Cosmos, credential and model ports and a fake in-process OIDC provider (discovery document, signing keys and token endpoint issuing tokens with the editor and tester roles), and drives sign-in, publishing a configuration, a test ticket and a text reply through the real HTTP and permit paths (Principle IX); it runs in CI through the job added in T067.
- [X] T095 [US5] Add tests that no secret, token, key or input value appears in any start error, log event or health response of either application, in `code/backend/tests/integration/test_content_privacy.py` and `code/management/tests/integration/test_audit_privacy.py` (FR-033, FR-047). Also add a correlation test that one conversation's events carry the same correlation identifier in the runtime and management logs (Principle VII), and a test in `code/management/tests/unit/test_security.py` that an expired or invalid sign-in secret fails closed.
- [X] T096 [US5] Spike S-14: confirm that the management API accepts a bearer token with the tester role issued to the deployment identity through its federated credential, and add a small application change with tests if it only accepts the browser sign-in session.
- [X] T097 [US5] Write failing tests in `code/tools/tests/test_check_environment.py` for `signin-redirect-matches` (on the first deployment it is expected to fail until the redirect phase has run) and `--functional` (runtime health probe, unauthenticated management request redirected to sign-in, an authenticated configuration read, a text conversation with a model reply within 15 seconds, a speech session with a generated audio sample started and stop confirmed, FR-028, SC-012, SC-015), and implement both in `code/tools/scripts/check_environment.py`; `--functional` runs from the pipeline's runner as the deployment identity holding the tester role (FR-053), an operator can pass their own tester token, and the check is skipped with the reason `public_access_disabled`, which is the only skip allowed, when public access is off.
- [X] T098 [US5] Update `.github/workflows/terraform-deploy.yml` so that the post-apply check step for `app_environment` passes `--functional` (depends on T097 and T055).

**Checkpoint**: Application tests, the contract tests and the browser tests pass; images are rebuilt and published with `images.yml`.

---

## Phase 8: User Story 6 - Operate the environment with clear guidance (Priority: P3)

**Goal**: A reviewer who did not build `int` can deploy, toggle public access, recover, roll back and tear down from the documentation alone.

**Independent Test**: `verify_docs.py` passes (links resolve, Mermaid diagrams render), and a reviewer follows the runbooks without an undocumented step.

Every document links to the relevant official Terraform and Azure guidance (FR-034) and uses only links to files, never to directories.

- [X] T099 [P] [US6] Write `docs/environments.md` (D-01): overview, the three deployment diagrams from [plan.md](plan.md), the environment convention, and the limitations (one replica, public exposure, default network rules, open tenant sign-in, no capacity target), plus a list of every self-operated component (identity script, GitHub's registry, the one-time local-state creation) with its justification (FR-022, SC-009).
- [X] T100 [P] [US6] Write `docs/runbooks/environment-bring-up.md` (D-02): prerequisites (including the directory role that the identity script needs), state, bootstrap, identity, images, app environment, check, redirect phase, teardown, recovery, rollback, secret renewal and key rotation, following [quickstart.md](quickstart.md), including the expected single failure of the first deployment's check at `signin-redirect-matches` and the rerun after the redirect phase.
- [X] T101 [P] [US6] Write `docs/runbooks/public-access.md` (D-03): the flag, the allow-list, enabling, disabling and verifying.
- [X] T102 [P] [US6] Write `docs/runbooks/image-publishing.md` (D-04): tags, immutability, scans, making the package public, rolling back, and the retention rule (published tags are never deleted or overwritten, and the workflow has no delete step, FR-036, SC-011).
- [X] T103 [US6] Update `docs/security.md` (D-05, FR-041): threats and controls, the Principle I assessment and owner approval for GitHub's registry, the Principle VIII rationale and approval for the identity script, the two secret exceptions with expiry and renewal, remote state sensitivity, open tenant sign-in, the explicit open allow-list range of `int` (sign-in, roles and permits are then the only access control, and the health endpoints and the sign-in page are reachable by anyone), and the tester role of the deployment identity (FR-053).
- [X] T104 [P] [US6] Rewrite `docs/runbooks/terraform-state.md`, `docs/runbooks/terraform-delivery.md`, `docs/runbooks/platform.md` and `docs/runbooks/platform-security.md` (D-06) for the three deployments and the managed services, removing every AKS, firewall and App Service passage.
- [X] T105 [P] [US6] Update `docs/runbooks/monitoring.md`, `docs/runbooks/scaling.md` and `docs/capacity.md` (D-08): alerts with owners and runbooks (platform metrics and the log-query alerts), the `int` capacity statement, the one-replica reason, and the rationale that the test console is not a call-handling feature (Principle VI).
- [X] T106 [P] [US6] Write `docs/runbooks/application-live-mode.md` (D-09): the settings contract, reviewed composition, fixed reason codes, how to change a setting and what each failure means.
- [X] T107 [US6] Review every module README under `code/infra/terraform/modules/` against D-07 (purpose, inputs, outputs, failure behavior, security assumptions) and fix gaps.
- [X] T108 [US6] Update `docs/setup.md`, `docs/repository-layout.md`, `docs/containers.md`, `docs/runbooks/README.md` and `docs/library-decisions.md` for the new layout, images, tools and providers, and create `docs/acceptance/int-environment.md` as the record for the live release gates.
- [X] T109 [US6] Run `. .\code\.local\feeds.ps1; code\tools\.venv\Scripts\python.exe code\tools\scripts\verify_docs.py` and fix every link or diagram error.

**Checkpoint**: Documentation verified.

---

## Phase 9: Live acceptance (release gates)

**Purpose**: Prove the requirements that need the owner's subscription and model account. These are release gates, recorded in `docs/acceptance/int-environment.md`, not merge gates. Requires the owner's values (plan open item 3), the ratification (T001) and US5.

- [ ] T110 Replace the placeholder values in `code/infra/environments/int/int.tfvars.json` and the three backend files with the owner's real values, then run `validate_environment.py` (plan open item 3), including `identity.user_group_id`, the model deployments and the region.
- [X] T111 Create the state storage with `terraform_delivery.py --operation state-create --environment int --confirm-apply`, and record that the state migrated and the local copy was removed (FR-006, FR-007).
- [ ] T112 Run the `terraform-bootstrap` workflow and approve the apply; record the outputs and confirm only default NSG rules and no routes (SC-014).
- [ ] T113 After the owner's confirmation that application code becomes public (plan open item 2) is recorded in `docs/acceptance/int-environment.md`, merge to `main`, let `images.yml` publish both images, make both packages public, rerun until `anonymous-pull` passes, and copy the `sha-<40 hex>` tags into `code/infra/environments/int/int.tfvars.json` (FR-036 to FR-038).
- [ ] T114 Run `Initialize-Identity.ps1 -Environment int -WhatIf` and then without it as a directory administrator, copy the printed values into `app_environment.identity`, store the secret as `MANAGEMENT_SIGNIN_SECRET` in both `app_environment` GitHub environments and delete `code/.local/identity/int/` (FR-046).
- [ ] T115 Compute the settings fingerprint with the T086 tool, review the non-secret settings and record `CALLER_REVIEWED_FINGERPRINT` and the review reference in `code/infra/environments/int/int.tfvars.json`.
- [ ] T116 Run the `terraform-app-environment` workflow and approve the apply; on this first run the job's environment check is expected to fail only at `signin-redirect-matches`, because the callback address exists only after the apps do. Confirm the verify items R-01, R-02, R-07 and R-14 (subnet delegation, in-place public access toggle, management-plane secret write, model role names) and record the results in `docs/acceptance/int-environment.md`.
- [ ] T117 Run `Initialize-Identity.ps1 -Environment int -Phase redirect`, then rerun the environment check (workflow or `check_environment.py --environment int --test-alert`) and confirm every check passes (SC-012).
- [ ] T118 Run `terraform-app-environment` and `terraform-bootstrap` with `expect_no_changes: true` and record zero planned changes (SC-002), including the secret resources (research R-07).
- [ ] T119 Toggle `environment.public_access.enabled` to false in `code/infra/environments/int/int.tfvars.json`, apply, confirm the apps refuse the allowed source and only access settings changed, then set it back and confirm (SC-005, FR-020).
- [ ] T120 Walk the editor in a browser as a signed-in group member (sign-in, publish a configuration, audit entries, a user without the role refused) and confirm the pipeline's `--functional` result for the text and speech journey (FR-048, SC-015); restart both apps and move to a second image version and confirm configuration, audit and text history persist (SC-016).
- [ ] T121 Destroy `app_environment` and `bootstrap` with the `destroy` input, confirm state storage and identity objects remain, recreate from the same files and re-run the check; confirm the identity script creates nothing new (FR-027, SC-003, SC-013).
- [ ] T122 A second engineer who did not build `int` follows `docs/runbooks/environment-bring-up.md` and the identity guide to a verified environment (or a documented dry run), and records the elapsed time and every gap in `docs/acceptance/int-environment.md` (SC-001, SC-010).
- [ ] T123 Record the date, version tags, results and any deviation for T110 to T122 in `docs/acceptance/int-environment.md`.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [X] T124 [P] Search the repository for retired terms (`aks`, `acr`, `firewall`, `App Service`, `platform` as a deployment name, `dev`, `test`, `prod` stage names, `terraform-infrastructure`) in `code/`, `docs/`, `.github/` and `README.md`, and remove or rewrite every leftover (FR-015).
- [X] T125 [P] Run `code/tools/scripts/check_secrets.py` and `code/tools/scripts/check_whitespace.py` and confirm 0 secrets, state or plan files in the repository, environment files and logs (FR-012, SC-008).
- [X] T126 Run `. .\code\.local\feeds.ps1; uv run --project code/tools --locked python code/tools/scripts/ci.py check` and fix any failure.
- [ ] T127 Walk through [quickstart.md](quickstart.md) sections 1 to 9 against the finished tooling and correct any command or expected result that no longer matches.
- [X] T128 [P] Confirm the Mermaid diagrams in `docs/environments.md`, `docs/security.md` and the runbooks match the delivered topology, and run `verify_docs.py` once more.

---

## Phase 11: Foundry account and model deployments (owner change 2026-11-09)

**Purpose**: The owner reversed the "existing model account" decision. The app environment deployment now creates a Microsoft Foundry account and both model deployments (FR-039, research R-14). Supersedes the read-only parts of T026 and T036.

- [X] T129 [P] Rewrite `code/infra/terraform/modules/model-access/tests/model_access.tftest.hcl` first: the account is `AIServices`, `S0`, project management on, key access off, public network access off, custom subdomain equals the name, system-assigned identity; two deployments with the configured model, version, type and capacity and `NoAutoUpgrade`; the runtime holds `Cognitive Services OpenAI User` on the created account; invalid name, equal deployments, a realtime deployment or model other than `gpt-live-1`, an unknown deployment type and a capacity below 1 are rejected naming the variable.
- [X] T130 Rewrite `code/infra/terraform/modules/model-access/` (`main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, `README.md`) to create the account and the two deployments (text first, then live), drop the `azapi` deployment lookup, and refresh its `.terraform.lock.hcl`.
- [X] T131 Update `code/infra/terraform/app_environment/` (`variables.tf` model object and validations, `main.tf` endpoints from the account name, `model_access.tf` inputs and the private endpoint target, `container_apps.tf` approved hosts, `outputs.tf` manifest account ID) and `tests/composition.tftest.hcl` (no model data source overrides; the manifest and runtime settings follow the created account).
- [X] T132 Update `code/infra/environments/int/int.tfvars.json`, `code/tools/scripts/validate_environment.py` and `code/tools/tests/test_validate_environment.py` for the new `app_environment.model` shape and rules.
- [X] T133 [P] Update `docs/environments.md`, `docs/runbooks/platform.md`, `docs/runbooks/platform-security.md`, `docs/runbooks/environment-bring-up.md` and any other page that says the model account is supplied by the owner; run `verify_docs.py`.
- [X] T134 Run the Terraform format, validate and test for `model-access` and `app_environment`, the tools tests and `ci.py check`; fix any failure.

---

## Phase 12: One GitHub environment per deployment environment (owner change 2026-11-09)

**Purpose**: One federated credential per environment. Plan and apply jobs of every root run in the GitHub environment named like the input environment (`int`), replacing a plan and an apply environment per root. Supersedes the environment names in T045.

- [X] T135 Update `code/tools/tests/test_terraform_delivery.py` first: the delivery context accepts only `TF_DEPLOYMENT_ENVIRONMENT` equal to the input environment and rejects per-root names; both jobs of `terraform-deploy.yml` use `${{ inputs.environment }}`; callers pass no `slug`.
- [X] T136 Update `code/tools/scripts/terraform_delivery.py` (`require_context`), `.github/workflows/terraform-deploy.yml` (job environments, drop `slug`) and the three caller workflows.
- [X] T137 [P] Update `docs/runbooks/terraform-delivery.md`, `docs/runbooks/environment-bring-up.md`, `docs/security.md`, [plan.md](plan.md), [quickstart.md](quickstart.md) and [tooling-and-workflows.md](contracts/tooling-and-workflows.md) with the single environment and the federated credential subject `repo:marvinbuss/caller-ai:environment:<env>`.
- [X] T138 Run the tools tests, `verify_docs.py` and `ci.py check`; fix any failure.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies. T001 (constitution ratification) blocks the identity script tasks T039 to T041.
- **Foundational (Phase 2)**: depends on Phase 1 and blocks every story.
- **US1 (Phase 3)**: depends on Phase 2. Modules (T015 to T027) are independent of each other. Roots depend on their modules. `state-create` (T037, T038) depends on the state root.
- **US2 (Phase 4)**: depends on US1 (it checks and drives those roots and workflows). The workflow step that passes `--functional` is added later by T098, after the check is implemented in US5.
- **US3 (Phase 5)**: depends on US1 (container modules and root). `public-access-matches` (T061) depends on T048.
- **US4 (Phase 6)**: depends on US1 for the layout to check. T069 depends on T063.
- **US5 (Phase 7)**: independent of the Terraform stories for its application code. T091 depends on T021 and T092 on T035. T096 and T097 depend on T048. T094 depends on T071 to T088 and enables the end-to-end job in T067. T098 depends on T097 and T055.
- **US6 (Phase 8)**: depends on the behavior it documents, so it runs after US1 to US5.
- **Live acceptance (Phase 9)**: depends on US1 to US5 and on open item 5.
- **Polish (Phase 10)**: after everything else.

### User story dependencies

- **US1 (P1)**: starts after Phase 2. No dependency on other stories.
- **US2 (P1)**: needs the US1 deployments to check.
- **US3 (P2)**: needs the US1 modules and root.
- **US4 (P2)**: needs the US1 layout.
- **US5 (P2)**: can start right after Phase 2, in parallel with US1 to US4. It is required for the first live apply because the published images refuse `live` mode today.
- **US6 (P3)**: needs the other stories to document accurately.

### Within each story

Write the failing tests first, then implement. Modules before roots, roots before workflows, tools before the workflow steps that call them.

---

## Parallel Opportunities

- Setup: T003 and T004.
- Foundational: T012 alongside the T008 to T011 chain (different files).
- US1: all thirteen module tasks T015 to T027 in parallel; T031 to T034 in parallel after T030, then T035; T039 (Pester tests) and T042 (image tool tests) alongside the Terraform work.
- US5 runs as a separate track: runtime (T070 to T076) and management (T077 to T088, after the spike T014) in parallel, with T089 to T092 in parallel after both.
- US6: T099 to T102 and T104 to T106 in parallel.

### Parallel example: User Story 1 modules

```text
Task: "Create module code/infra/terraform/modules/state-backend/ ..." (T015)
Task: "Create module code/infra/terraform/modules/network/ ..." (T016)
Task: "Create module code/infra/terraform/modules/container-app/ ..." (T020)
Task: "Create module code/infra/terraform/modules/data-store/ ..." (T021)
```

### Parallel example: application live mode

```text
Track A (runtime): T070 -> T071 -> T072 -> T073 -> T074 -> T075 -> T076
Track B (management): T014 -> T077 -> T078 -> T079 -> T080 -> T081 -> T082 -> T083 -> T084 -> T085 -> T086 -> T087 -> T088
Join: T089, T090, T091, T092 in parallel, then T093 to T098
```

---

## Implementation Strategy

### MVP first (US1 and US2)

1. Complete Phases 1 and 2.
2. Complete US1 (modules, roots, identity script, images, workflows). Stop and run the offline checks.
3. Add US2 so a deployment verifies itself.
4. Complete US5 (application live mode) in parallel, because the live apply needs it.
5. Run Phase 9 tasks T110 to T120 for the first verified `int`.

### Incremental delivery

1. Foundation, then US1: environment files, three roots, tooling, workflows.
2. US2: environment check, repeat-plan and destroy.
3. US3: public access toggle.
4. US4: structure and security checks in CI.
5. US5: working editor and console.
6. US6: documentation, then Phase 9 and Phase 10.

### Notes

- Do not commit anything created under `code/.local/` (the hand-over secret and the mirror feeds).
- Commit after each task or logical group, following the `speckit-git-commit` hook.
- The live release-gate tasks T116 to T120 need the owner's subscription and model account.
