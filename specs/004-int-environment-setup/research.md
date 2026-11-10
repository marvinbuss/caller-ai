# Research: First Internal Test Environment

All unknowns from the plan's Technical Context are resolved here. Sources are the repository on
2026-11-05, a code survey of both applications, and Microsoft Learn and Terraform provider
documentation read during planning. Items marked **Spike** need a short proof during implementation,
and each has a task. Items marked **Verify** are provider or platform behaviors to confirm with a
real `terraform validate` or first apply.

## Findings about the current repository

| Finding | Evidence | Consequence |
|---|---|---|
| Both images start only in `local` mode. `live` is refused at startup. | `Settings.validate_startup` in both applications raises `live_mode_unavailable`. | The plan adds a live mode (R-10, R-11). |
| Without injected resources the runtime serves only health probes, and the management app serves a status shell. | `caller_runtime/main.py` (`app = create_app()`), `management_app.py` (`app = create_app()`). | Both module-level apps need a composition path that reads settings. |
| The runtime is built from `RuntimeOptions` plus a container factory, a credential factory, a workload identity verifier and permit verification keys. Five `*_reviewed` flags must be `True`. | `caller_runtime/composition.py`. | A live composition supplies each, from settings. |
| The management app composes through `compose_management(settings, resources, authority, request)` and needs a short-lived `CompositionGrant` for a fingerprint of the settings. | `management_app/composition.py`. | An authority for deployments is needed (R-11). |
| There is no Cosmos client factory, no credential factory and no reading of Azure settings in either app. | Code survey. | New `azure_clients.py` in each project. |
| `azure-cosmos`, `azure-identity` and `msal` are already dependencies. | `pyproject.toml` of both projects. | No new application dependency for the stores or sign-in. |
| The management app has its own Entra authorization-code sign-in (MSAL), server-side sessions, CSRF and role checks. It does not read platform sign-in headers. | `security.py`, `adapters/entra_oidc.py`. | Platform sign-in would conflict (R-04). |
| The runtime fetches configuration from the management app with an identity token whose audience and role are checked, and the management app calls the runtime. Start permits are RS256 JWTs signed by management and verified by the runtime with 1 to 4 public keys. | `configuration_client.py`, `test_session_client.py`, `identity.py`, `security.py`. | Settings for audiences, roles and keys; the key pair (R-05); the application role (R-06). |
| The management image bakes `API_URL` at build time and its exported frontend contains only the default page. | `code/management/Dockerfile`, `rxconfig.py`. | Editor pages and the runtime address must come out of the build or start (R-12). |
| `azurerm_container_app` has no authentication settings block. `ingress.ip_security_restriction` needs all rules to use the same action. Secrets can be Key Vault references with a user-assigned identity. | Provider documentation. | The application's own sign-in avoids a second provider. Allow-list uses `Allow` rules only. |
| `azurerm_container_app_environment` can be created external and its `public_network_access` can then change in place. `internal_load_balancer_enabled` cannot change. | Provider and Learn networking documentation. | Create external, toggle the setting (R-02). |
| The repository is private and `code/.local/` is ignored by git. | `gh repo view`, `.gitignore`. | Public images publish code. The secret hand-over file goes under `code/.local/`. |

## R-01 Hosting platform

- **Decision**: Azure Container Apps, workload profiles environment with the Consumption profile, one
  container app per application, `revision_mode = Single`, exactly one replica each.
- **Rationale**: The owner's choice. It is a managed service with native HTTP, WebSocket, health
  probes, Key Vault secret references, user-assigned identities and ingress allow-lists in the pinned
  provider (FR-022, FR-025).
- **Alternatives considered**: App Service (earlier plan, now superseded by the owner), AKS (node
  management, rejected by FR-022).
- **Verify**: the environment subnet needs a delegation to `Microsoft.App/environments` and at least a /27
  for the workload profiles type; the Consumption profile must be declared at creation time.

## R-02 Public access mechanics

- **Decision**: The environment is created external with `public_network_access` taken from the flag
  (`Enabled` or `Disabled`). Each app sets `ingress.external_enabled` to the flag and, when enabled, one
  `Allow` `ip_security_restriction` per allowed range. Enabling without a range fails validation. An explicit open range (`0.0.0.0/0`, `::/0`) is allowed and gives no source restriction. `int` uses it so GitHub-hosted runners, whose addresses change, can reach the apps. Validation warns and D-05 records the exposure. No
  environment private endpoint is created in this feature.
- **Rationale**: The setting changes in place for an external environment, so switching the flag does not
  remove data or touch unrelated services (FR-020). A private environment has no consumer yet, and the
  platform team that owns the customer network adds one when needed.
- **Alternatives considered**: an internal environment (cannot become public later, rejected); a front door
  or application gateway (extra service).
- **Verify**: that the toggle is an in-place update; that an app with `external_enabled = false` is
  unreachable from outside while the environment stays external.
- **Forwarded scheme**: the ingress terminates TLS, accepts HTTPS only and sets `X-Forwarded-Proto`. Both
  servers trust that header from any address (runtime: Uvicorn `--forwarded-allow-ips "*"`; management:
  Granian's proxy-header wrapper), because the ingress is the only way into a container and the routes'
  `https`/`wss` checks need the original scheme. Neither app decides anything on the client address.

## R-03 Network and outbound traffic

- **Decision**: Bootstrap creates the virtual network, the Container Apps subnet (delegated), the
  private-endpoint subnet, one network security group with only the default rules, one route table with no
  routes (both associated), and the private DNS zones with links. No outbound rule, route, NAT gateway or
  firewall exists anywhere (FR-044, SC-014). The app environment deployment reads identifiers from bootstrap
  outputs only (FR-045).
- **Rationale**: The owner's decision: real hosting lands in a network that a platform team provides and owns,
  including outbound policy. Private endpoints are created with the service they expose, in the subnet from
  bootstrap, and are not network foundation.
- **Alternatives considered**: an allow-list by service tag (earlier plan, dropped by the owner); a firewall.
- **Note**: because outbound is open by default, the image pull from ghcr.io and the model calls need nothing
  extra. The documentation states that outbound policy belongs to the network owner.

## R-04 Management sign-in

- **Decision**: Use the management application's own Entra authorization-code sign-in and its server
  sessions, with the client secret read from the vault. Do not enable the platform's built-in sign-in.
- **Rationale**: The application already authenticates users, issues its own session cookie and CSRF token and
  checks roles. A second sign-in in front would double-authenticate and the app does not read the platform
  headers. It also removes the need for a provider for authentication settings.
- **Spec interpretation**: the spec now names the application's own sign-in (FR-019, FR-041, a clarification). The approved
  secret exception is unchanged, because the application's flow is a confidential client.
- **Possible later removal of the secret**: MSAL for Python supports a client assertion callback. The
  application could present a token from a managed identity as a federated credential, which would remove the
  exception. That is an application change, so it is left as a follow-up.
- **Spike S-4**: confirm `entra_oidc.LoginFlow` can take the secret from a setting and that the redirect
  address derived from the app's default domain matches the registered redirect.
- **Decision FR-052 (owner, 2026-11-05): separate runtime registration.** The runtime has its own app registration, whose
  client ID is the audience of the management app's workload token. The identity script creates it with the app role
  `management`; once the app environment exists, the script's second phase assigns that role to the management
  identity. The runtime verifies tenant, audience, caller principal and role (settings `CALLER_TENANT_ID`,
  `CALLER_WORKLOAD_AUDIENCE`, `CALLER_WORKLOAD_ROLE`, `CALLER_WORKLOAD_PRINCIPAL_ID`); `CALLER_RUNTIME_AUDIENCE` of the
  management app is that client ID.
- **S-4 result (from the existing code)**:
  - The callback path is `/auth/callback` (`MsalCodeFlow.begin` builds `policy.origin + "/auth/callback"`;
    `reflex_security.py` and `metrics.py` list the same path). The registered redirect is therefore
    `https://<management default domain>/auth/callback`. The first plan text said `/v1/security/callback`; the
    manifest output, the environment check, the data model and this document now use `/auth/callback`.
  - The confidential client is built by a factory (`application_factory`), so the client secret can come from
    `CALLER_ENTRA_CLIENT_SECRET` and the origin from `CALLER_MANAGEMENT_ORIGIN` without changing the flow.
  - **Two differences need a decision before T083** (live composition): `EntraPolicy` rejects an API audience equal to
    the sign-in client (`distinct_api_audience_required`), while the plan uses one registration for both
    (`CALLER_API_AUDIENCE` equals `CALLER_ENTRA_CLIENT_ID`); and `MsalCodeFlow.begin` builds the scope as
    `api://<api_audience>/<manager_scope>`, while the contract names `CALLER_MANAGER_SCOPE` as the full
    `api://<client id>/.default`. Either the policy and flow accept a single registration, or a second
    registration is created. **Decision (owner, 2026-11-05): one registration.** T083 changes the policy and the
    sign-in flow to accept `api_audience == client_id` and the full `CALLER_MANAGER_SCOPE`; the contract and tests follow.
  - **Live finding (2026-11-10)**: with one registration the sign-in requests a token for the app itself, and Entra
    refuses `api://<client id>/.default` for that (`AADSTS90009`: only the GUID-based identifier is supported). Entra
    then posts an error without a code to `/auth/callback`, which answers 401. The GUID form `<client id>/.default`
    then failed with `AADSTS650057` (the app does not list itself as a required permission). The callback uses only
    the ID token (audience: the app, roles included), so the sign-in now requests no API scope, only `openid profile`;
    Entra then returns a code. `CALLER_MANAGER_SCOPE` keeps the `api://` form, which the settings check still validates.

## R-05 Permit signing key pair

- **Decision**: A `permit-key` module generates an RSA 2048 key pair with the `tls` provider. The private key
  is written to the vault by the `key-vault-secret` module. The public key is passed to the runtime as the
  setting for its verification keys (key id `int-1`, up to four allowed for rotation). The management app reads
  the private key from the vault through its identity.
- **Rationale**: FR-051 and the clarification. Rotation adds a second pair and keeps both public keys in the
  runtime setting during the overlap, which the runtime verifier already allows (1 to 4 keys).
- **Risks**: the private key is in the remote state (accepted, FR-031).
- **Alternatives considered**: key generation by the identity script (more manual steps); a vault-held
  non-exportable key (needs a signer change in the management app).

## R-06 Roles and who may use the editor

- **Decision**: The identity script creates the application roles on the management registration: an editor
  role, a tester role, and a runtime role used for calls from the runtime. It assigns the runtime role to the
  runtime's identity (FR-052). For people, it assigns the editor and tester roles to the group named in the
  variable file (`app_environment.identity.user_group_id`, required). For the pipeline's functional journey it also
  assigns the tester role to the deployment identity named by `environment.client_id` (FR-053). Role names come
  from the variable file.
- **Problem**: the application checks a role claim (`manager_role`, `test_role`) for editing and testing, so a
  user with no role can sign in but cannot use the editor or console.
- **Resolution**: any tenant user may sign in (FR-019). The owner names the group that holds the roles, which
  can be as broad as the owner wants. The guard is not relaxed.

## R-07 Writing secrets to the vault

- **Decision**: The `key-vault-secret` module uses `azapi_resource` of type `Microsoft.KeyVault/vaults/secrets`.
  The vault has public access disabled and a private endpoint. The deployment identity needs a role that
  allows the management-plane write.
- **Rationale**: `azurerm_key_vault_secret` writes through the vault's data plane. A GitHub-hosted runner cannot
  reach a private vault, so the data-plane write would fail. The management-plane write does not need a network
  path to the vault.
- **Verify**: the management-plane secret write succeeds with public access disabled; Container Apps resolve
  vault references through the private endpoint with the app's user-assigned identity; a second plan shows zero
  changes for the secret resources (the sensitive body must not cause a perpetual diff, FR-026).
- **Provider note**: `azapi` is pinned exactly and used for this module and for read-only data sources that
  `azurerm` lacks (model deployments). The module sets the value through the provider's sensitive body input, and
  a test inspects the plan JSON for it (FR-046). Recorded in `docs/library-decisions.md`.

## R-08 Remote state creation

- **Decision**: A separate `state` root creates a storage account, `tfstate` and `tfplans` containers, a delete
  lock and container-scoped writer roles. The one-time creation is the delivery tool's `state-create`
  operation: `init -backend=false`, plan, a confirmed apply, `init -migrate-state -force-copy`, confirm the
  blob, delete local state. Later changes use the normal workflow.
- **Rationale**: Keeps state in Terraform and outside bootstrap (FR-006). The storage has shared keys off,
  identity-only access and a public endpoint so runners can reach it (FR-031).
- **Verify**: role assignment propagation before the migrate step (bounded retry).

## R-09 Environment files and variable slicing

- **Decision**: `code/infra/environments/<env>/` holds `<env>.tfvars.json` and three backend files. The
  variable file has top-level objects `environment`, `state`, `bootstrap`, `app_environment`. The delivery tool
  gives each root only `environment` plus its own object through a temporary file outside the repository.
- **Rationale**: One variable file (FR-009), no undeclared-variable warnings. Adding an environment adds a
  directory (FR-014). The sign-in secret arrives as a sensitive environment variable, never in a file.

## R-10 Runtime live mode

- **Decision**: Add `caller_runtime/entrypoint.py` with `create_app_from_environment()` used as the container
  command with `uvicorn --factory`. In `local` mode it returns the existing health-only app. In `live` mode it
  reads a frozen `LiveSettings` and builds, through `caller_runtime/live.py`: `RuntimeOptions`, a credential
  factory based on the managed identity named by `AZURE_CLIENT_ID`, Cosmos container factories for
  `call-sessions` and `text-conversations`, the workload identity verifier, the permit verification keys, the
  configuration client and activation, and the readiness port. Missing or invalid settings stop the start with
  one fixed reason code and no value in the message.
- **Rationale**: Matches the existing "construction never discovers resources" rule. All values arrive as
  settings (FR-047). The `*_reviewed` flags and `review_reference` are set from a recorded approval reference
  setting, and the live module refuses an empty reference.
- **Settings**: see [application-settings.md](contracts/application-settings.md).
- **Spike S-10**: confirm the async Cosmos client and `ManagedIdentityCredential` satisfy `ContainerPort` and
  `CredentialPort` without wrapper surprises, and that readiness stays under its 0.25 s limit.

## R-11 Management live mode and composition authority

- **Decision**: Add `management_app/live.py` that builds the `ManagementRequest`, a `ManagementResources`
  factory (Cosmos configuration and audit repositories, token verifier with signing keys from the tenant's
  discovery document, identity service and session store, runtime identity, test permit signer with the private
  key from the vault, test session client, empty approved capabilities for `int`) and an authority for
  deployments.
- **Authority for deployments**: a small `ReviewedFingerprintAuthority` grants a composition when the computed
  settings fingerprint equals `CALLER_REVIEWED_FINGERPRINT`, which the owner records in the variable file after
  reviewing the non-secret settings. A tool prints the fingerprint for the settings that a deployment would give.
- **Spike S-11**: the fingerprint covers the settings, some of which come from deployment outputs (for example
  the other app's address). Decide whether those values are derived from names that the variable file fixes, or
  excluded from the fingerprint. The owner approves the final rule.
- **S-11 rule (owner chose "all nonsecret live settings" on 2026-11-05; refinement awaiting confirmation)**: the
  management fingerprint covers every nonsecret management setting except `CALLER_REVIEWED_FINGERPRINT` and
  `CALLER_REVIEW_REFERENCE`, and never a secret. A reviewer has to approve the fingerprint before the apply, so a
  value that exists only after the apply cannot be part of it. These values are therefore excluded and covered
  through the names that determine them, which the variable file fixes: the two app origins and everything derived
  from them (`CALLER_MANAGEMENT_ORIGIN`, `CALLER_PUBLIC_ORIGIN`, `CALLER_RUNTIME_ENDPOINT`,
  `CALLER_RUNTIME_AUDIENCE`, `CALLER_PERMIT_ISSUER`, `CALLER_PERMIT_AUDIENCE`) by the app names and the environment
  name; the management identity's `AZURE_CLIENT_ID` by its name; and the generated public key by
  `CALLER_SIGNING_KEY_ID`. All other nonsecret settings (tenant, client IDs, roles, scope, data store endpoint and
  database, retention, signing key ID) enter the fingerprint as values.
- **Outcome (T080-T086, owner approved the rule)**: implemented in `management_app/live.py`. The runtime's identity
  client ID is a further nonsecret value that enters the fingerprint (`CALLER_RUNTIME_CLIENT_ID`, the data source of
  the script-created identity, known before the apply). The management registration is also its API audience
  (R-04), so the runtime role is distinct from the editor role instead of the audience being distinct, and the
  scope setting `api://<client ID>/.default` is checked against the API audience. The fingerprint tool is
  `python -m management_app.fingerprint`.
- **Token version**: both registrations are set to issue version 2 access tokens (`api.requestedAccessTokenVersion`
  by the identity script) because the management app verifies the version 2 issuer and `ver` claim.
- **App-only claim**: the management registration requests the optional access token claim `idtyp`, because the
  management app accepts the runtime's and the tester's app-only tokens only with `idtyp=app`, and Entra adds
  the claim only on request.
- **Capabilities**: tools and MCP are not part of the journey, so the approved capability set is empty (FR-048).

## R-12 Management frontend build and runtime address

- **Decision**: The deployed image must contain the editor and test console pages, and must learn the address
  that the browser uses at start, not at build.
- **Why this is a risk**: the Dockerfile exports the frontend from the default app, which registers only the
  index page, and bakes `API_URL` at build time. FR-047 and FR-038 forbid an image per environment.
- **Spike S-12**: (a) build the export with the editor pages registered through a build-only stub composition
  (they register states and pages, not live services); (b) start the image with two different `API_URL`
  values and see whether the frontend follows it. If it does not, write the value into the frontend's
  environment file in a small start script, with a container test that fails before the change.
- **Outcome (T087-T088)**: the frontend did not follow `API_URL` at start (the bundle held `ws://localhost:3000/_event`
  and `routes.json` listed only `index`). The image now exports with `CALLER_FRONTEND_EXPORT=1`, which registers every
  page through `management_app/frontend_export.py`, bakes the placeholder `http://caller-api-origin.invalid`, and starts
  through `python -m management_app.start`, which rewrites the placeholder to `API_URL` (or `CALLER_PUBLIC_ORIGIN`),
  which Terraform sets to the management app's own origin. The container test failed before the change and passes
  after it.
- **Live finding (2026-11-10)**: the image itself sets `API_URL` to the placeholder, so on `int` the start script
  took the placeholder as the target, rewrote nothing, and the browser tried `ws://caller-api-origin.invalid`. An
  `API_URL` equal to the baked placeholder is now ignored, and `CALLER_PUBLIC_ORIGIN` applies.
- **Constraint**: Reflex must stay the frontend framework (Principle V).

## R-13 Data store and credentials

- **Decision**: Cosmos DB for NoSQL, serverless, local authentication disabled, public access off, a private
  endpoint, continuous backup, one database, and the containers the repositories expect: `configuration`
  (`/organizationId`, no default TTL), `configuration-audit` (`/dayBucket`), `call-sessions`, `call-audit`
  (`/dayBucket`) and `text-conversations` (`/ownerKey`, item TTL). Each app gets a data role scoped to its own
  containers only (the container lists are confirmed by S-13). Credentials are managed identities (`ManagedIdentityCredential` with the identity's client
  ID).
- **Spike S-13**: confirm `call-sessions` needs the partition key `/id` (a single authority item) and that the
  container list and key paths match the repositories exactly, with a test that reads the repositories' names.

## R-14 Model account and deployments

- **Decision** (2026-11-09, replacing "an existing account"): the `model-access` module creates a new
  Microsoft Foundry account (`azurerm_cognitive_account`, kind `AIServices`, SKU `S0`, project management
  on, system-assigned identity) in `application.live_region`, with a custom subdomain equal to its name, key
  access off and public network access off. It creates two `azurerm_cognitive_deployment` resources from
  `app_environment.model.text` and `app_environment.model.live` (model, version, deployment type, capacity),
  pinned with `NoAutoUpgrade` so the reviewed version does not change underneath the runtime. The realtime
  deployment is named `gpt-live-1` because the runtime's `/openai/v1` Live session requests that name. The
  deployments are created one after the other, because the account accepts one deployment change at a time.
  The text endpoint and the live endpoint differ (`*.openai.azure.com` and `*.services.ai.azure.com`), so the
  root creates one private endpoint for the account with the three DNS zones. The runtime's identity gets
  `Cognitive Services OpenAI User` on the account.
- **Alternatives**: keep an owner-supplied account (rejected by the owner: the environment must be complete
  from its files); `azapi` for the account (rejected: `azurerm` 5.11.0 has every needed argument, including
  `project_management_enabled`).
- **Verify**: the role names for text and realtime use, the subscription's quota for both models under the
  chosen deployment type, and the region rule in `RuntimeOptions.live_region` (one of four regions). A
  successful apply is the evidence of regional support and quota (Principle I).

## R-15 Environment check and the functional journey

- **Decision**: `check_environment.py` runs from the pipeline with read access only: services and settings
  match, each revision is healthy (revision `healthState` over the management API), image versions match,
  public access matches the flag, no custom outbound rules, private endpoints approved, role assignments and
  secrets exist (names, never values), the runtime role assignment exists, the named sign-in registration and roles exist (a Graph read with the deployment identity's `Application.Read.All` permission, FR-042), and the state is isolated. The
  functional journey runs in the pipeline too (`--functional`): the runner reaches the apps because `int` sets an
  open allow-list range, and it signs in as the deployment identity, which the identity script assigns the tester
  role (FR-053). It checks the unauthenticated redirect, the health probes, a text conversation and a speech
  session start and stop. A person walks the editor in a browser as a release gate.
- **Rationale**: the spec requires the check to include the conversation journey (FR-028, FR-029). The existing
  browser tests and an in-process end-to-end test (both applications with fake Azure ports) give Principle IX
  evidence in CI.
- **Spike S-14**: confirm that the management API accepts a bearer token with the tester role from the
  deployment identity (federated credential), not only the browser sign-in session. If not, add a small
  application change.
- **Outcome (T096-T098, owner chose the narrow path)**: it did not (the bearer path needed a delegated scope and the
  editor role, and tickets needed a cookie). `require_reader` and `require_test_principal` now also accept an app-only
  token that holds the tester role, for `GET /v2/configuration` and the console ticket and socket only. The check
  signs in through `az account get-access-token --resource api://<management client ID>`, an operator may pass a
  token in the environment variable named by `--tester-token-env`, and the pipeline passes `--functional`. The journey
  covers both probes, the sign-in redirect, an anonymous 401, the configuration read, a text reply within 15 seconds and
  a speech session that accepts a generated sample and confirms stop.
- **Alternatives considered**: a Container Apps job inside the environment that runs the journey (new service
  and code, deferred); a temporary runner rule outside Terraform (rejected, the open range for `int` makes it
  unnecessary).

## R-16 Image publishing

- **Decision**: `images.yml` builds both Dockerfiles, scans with Trivy for secrets and HIGH or CRITICAL
  vulnerabilities, refuses to overwrite a tag, pushes `ghcr.io/marvinbuss/caller-ai/runtime` and `/management`
  as `sha-<full commit>` and `v<semver>` on a `v*` tag push, then checks an anonymous pull. A one-time manual
  step makes each package public; the anonymous-pull check fails until then.
- **Rationale**: FR-036 to FR-038. GHCR does not enforce immutability, so it is a workflow rule plus digests.

## R-17 Identity script

- **Decision**: `code/infra/scripts/identity/Initialize-Identity.ps1`, PowerShell 7 with Azure CLI, `-WhatIf`
  preview. It creates, idempotently: the management registration with redirect URI
  `https://<management app default domain>/auth/callback` (path confirmed in S-4), the service
  principal, the application roles from R-06, the runtime's user-assigned identity and its role assignment, the
  tester role assignment of the deployment identity, a
  client secret valid at most 90 days, and writes only that secret to
  `code/.local/identity/<env>/management-signin-secret` (ignored by git, file mode restricted). It prints
  identifiers and never the secret.
- **Redirect address**: derived from the app's default domain, which the platform generates. The script cannot
  know it before the app exists. **Resolution**: the script registers the redirect after the first app
  environment deployment (a second `-Phase redirect` run reads the management app's FQDN with `az containerapp show`, using the app name from the variable file). The check
  `signin-redirect-matches` reports a mismatch.
- **Permissions**: a directory role that can create app registrations and consent to Graph application permissions (Privileged Role Administrator or Global Administrator), because the same script run grants the deployment identity `Application.Read.All`, and Contributor on the identity resource group. The preflight names the missing right.
- **Tests**: Pester with mocked `az`.

## R-18 Monitoring and alerts

- **Decision**: Log Analytics workspace (daily cap, retention from the file), workspace-based Application
  Insights with a private link scope, the environment's log destination set to the workspace, and metric alerts
  on platform signals (5xx request count, restart count, replica count below one, CPU and memory). An action
  group emails the receivers in the file. Each alert carries an owner and a runbook link.
- **Application signals**: scheduled log-query alerts over the applications' structured events cover dependency
  failures and latency. The monitoring task confirms the event fields. A correlation test and a sensitive-data
  exclusion test run in CI (Principle VII).

## R-19 One replica and capacity

- **Decision**: `min_replicas = 1`, `max_replicas = 1` for both apps. No autoscale. `docs/capacity.md` states
  that `int` has no capacity target and why.
- **Rationale**: the management app holds sessions in memory, the runtime holds live sessions in memory.

## R-20 Terraform security scan (FR-030)

- **Decision**: `verify_terraform.py check` also runs a pinned Trivy `config` scan over `code/infra/terraform`
  and fails on HIGH or CRITICAL findings, with tests that a seeded misconfiguration fails.
- **Rationale**: Principle VIII and FR-030 require security checks, and Trivy is already used for images.
- **Where it runs**: the scanner is a Linux container image. The pre-commit hook, which `ci.py check` also runs on
  the Windows runner, calls `verify_terraform.py check --no-scan`. The Linux `terraform` CI job always runs the full
  check with the scan, so every change is still scanned.

## R-21 What gets removed

- **Decision**: Delete the AKS-era platform module and fixtures, the old shared root, the old config examples,
  the old infrastructure workflow, and every document passage describing them. Rename the third deployment to
  `app_environment` everywhere (directory, backend file, variable object, workflow, docs). Search for `aks`,
  `acr`, `firewall`, `platform` as a deployment name, stage names `dev`, `test`, `prod` and App Service.
- **Rationale**: FR-015, no backward compatibility.

## Open items for the owner

| Item | Needed by |
|---|---|
| Ratify in a constitution update that Principle VIII governs infrastructure resources and that the identity script administers directory objects (FR-043). | Before the script is built |
| Confirm that code becomes public through the images. | Before the first publish |
| Provide region, subscription, deployment identity, model account, deployment names and the group for `identity.user_group_id`. | First bring-up |
