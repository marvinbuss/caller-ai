# Contract: Application Settings

The interface between the app environment deployment and the two container images (FR-047). The
deployment gives each container a fixed set of settings at start. Names below are the planned contract
and are confirmed against the application code by spikes S-4, S-10, S-11 and S-12 in
[research.md](../research.md). Both applications read only `CALLER_*` names (and `AZURE_CLIENT_ID`), freeze
them at start and never echo a value in an error.

## Rules

| Rule | Detail |
|---|---|
| One image everywhere | Nothing environment-specific is built into an image. Only these settings differ (FR-038, FR-047). |
| Fixed reason codes | A missing or invalid setting stops the start with exactly one of: `settings_missing`, `settings_invalid`, `review_missing`, `fingerprint_mismatch`, `identity_unavailable`, `store_unavailable`, `key_invalid`. The message holds no value. |
| Secrets by reference | A secret setting is a Key Vault reference resolved by the platform with the app's user-assigned identity. The deployment never sets a secret value in clear text. |
| Local mode unchanged | With `CALLER_MODE` unset or `local`, both images behave exactly as today (health and status only) and need no other setting. |
| Identity | `AZURE_CLIENT_ID` names the user-assigned identity the credential factory uses. No client secret, key or connection string for any Azure service. |

## Runtime

| Setting | Secret | Meaning |
|---|---|---|
| `CALLER_MODE` | no | `live`. |
| `AZURE_CLIENT_ID` | no | Client ID of the runtime's user-assigned identity (created by the identity script, attached by the deployment). |
| `CALLER_REVIEW_REFERENCE` | no | Recorded approval reference for the live composition. An empty value gives `review_missing`. |
| `CALLER_COSMOS_ENDPOINT`, `CALLER_COSMOS_DATABASE` | no | Data store account endpoint and database name. |
| `CALLER_TEXT_HISTORY_TTL_SECONDS` | no | Item time-to-live for text history, from the variable file. |
| `CALLER_MANAGEMENT_ENDPOINT` | no | HTTPS origin of the management app. |
| `CALLER_MANAGEMENT_AUDIENCE` | no | Audience GUID for the identity token sent to management (the management registration's client ID). |
| `CALLER_TEXT_ENDPOINT`, `CALLER_TEXT_DEPLOYMENT` | no | Foundry account text endpoint and the text deployment name. |
| `CALLER_LIVE_ENDPOINT`, `CALLER_LIVE_REGION`, `CALLER_LIVE_VOICE` | no | Realtime endpoint, region (one of `canadacentral`, `centralus`, `eastus2`, `swedencentral`) and voice. |
| `CALLER_APPROVED_HOSTS` | no | Comma-separated host names the transports may call (at most 8): the management app, the text and realtime endpoints. |
| `CALLER_MAX_INPUT_TOKENS`, `CALLER_MAX_OUTPUT_TOKENS` | no | Token limits. |
| `CALLER_AVAILABLE_LIVE_SESSIONS` | no | Admission limit for live sessions, from 5 to 10000. |
| `CALLER_MANAGER_ISSUER`, `CALLER_MANAGER_AUDIENCE` | no | Issuer and audience of the start permits. |
| `CALLER_PERMIT_VERIFICATION_KEYS` | no | JSON map from key ID to public key PEM, one to four entries (the public half of the generated pair). |
| `CALLER_MAX_CLOCK_SKEW_SECONDS` | no | Permit clock skew bound, 0 to 1. |
| `CALLER_TENANT_ID` | no | Tenant that issues the workload tokens the runtime accepts. |
| `CALLER_WORKLOAD_AUDIENCE` | no | Client ID of the runtime's own app registration: the required audience of a workload token (FR-052). |
| `CALLER_WORKLOAD_ROLE` | no | Application role on the runtime registration that a caller must hold (`identity.roles.management`). |
| `CALLER_WORKLOAD_PRINCIPAL_ID` | no | Object ID of the management app's managed identity: the only caller accepted (FR-052). |

Runtime probes: startup and readiness `GET /readyz`, liveness `GET /healthz`, port 8000.

## Management

| Setting | Secret | Meaning |
|---|---|---|
| `CALLER_MODE` | no | `live`. |
| `AZURE_CLIENT_ID` | no | Client ID of the management app's user-assigned identity (created by Terraform) used for the vault and data store. |
| `CALLER_REVIEW_REFERENCE`, `CALLER_REVIEWED_FINGERPRINT` | no | Approval reference and the reviewed settings fingerprint that the composition authority compares (spike S-11). Mismatch gives `fingerprint_mismatch`. |
| `CALLER_COSMOS_ENDPOINT`, `CALLER_COSMOS_DATABASE` | no | Data store account endpoint and database name. |
| `CALLER_AUDIT_RETENTION_SECONDS` | no | Audit time-to-live, from the variable file. |
| `CALLER_TENANT_ID`, `CALLER_ENTRA_CLIENT_ID` | no | Tenant and the management registration's client ID. |
| `CALLER_ENTRA_CLIENT_SECRET` | **yes** | Key Vault reference to `management-signin-secret` (FR-041, FR-046). |
| `CALLER_API_AUDIENCE`, `CALLER_MANAGER_ROLE`, `CALLER_MANAGER_SCOPE`, `CALLER_TEST_ROLE`, `CALLER_RUNTIME_ROLE` | no | Audience, editor role, scope, tester role and the runtime's role name. |
| `CALLER_MANAGEMENT_ORIGIN` | no | The management app's own HTTPS origin, for redirects and origin checks. |
| `CALLER_RUNTIME_ENDPOINT`, `CALLER_RUNTIME_AUDIENCE` | no | HTTPS origin of the runtime, which the browser also uses for the console, and the client ID of the runtime's app registration (the audience of the token the management app presents to the runtime). |
| `CALLER_RUNTIME_CLIENT_ID` | no | Client ID of the runtime's managed identity: the only application accepted as caller of the management runtime-configuration endpoint (the `azp` of its token). |
| `CALLER_PERMIT_ISSUER`, `CALLER_PERMIT_AUDIENCE`, `CALLER_SIGNING_KEY_ID` | no | Permit issuer, audience and key ID (equals `signing_key.key_id`). |
| `CALLER_SIGNING_KEY_PEM` | **yes** | Key Vault reference to `permit-private-key` (FR-051). |
| `CALLER_PUBLIC_ORIGIN` | no | The address the browser reaches the management app on (its own origin). The image rewrites the frontend's baked backend address to this value at start, or to `API_URL` when that is set (spike S-12). |

Management probes: startup and readiness `GET /readyz`, liveness `GET /healthz`, port 3000.

## What the deployment derives

| Setting | Derived from |
|---|---|
| Both apps' origins | `https://<app name>.<environment default domain>`, computed inside the deployment so no value is copied by hand. |
| `AZURE_CLIENT_ID` | Output of the identity module (management) and the data source of the script-created runtime identity. |
| Cosmos and model endpoints | The data store module output and the Foundry account that the `model-access` module creates. |
| Public key | The `permit-key` module output. |
| Key Vault references | The `key-vault-secret` module outputs (versionless secret IDs). |
| `CALLER_TENANT_ID`, `CALLER_ENTRA_CLIENT_ID` | `environment.tenant_id` and `identity.management_app_client_id`. |
| `CALLER_API_AUDIENCE`, `CALLER_MANAGEMENT_AUDIENCE` | The management registration's client ID (`identity.management_app_client_id`). |
| `CALLER_MANAGER_SCOPE` | `api://<management registration client ID>/.default`. |
| `CALLER_MANAGER_ROLE`, `CALLER_TEST_ROLE`, `CALLER_RUNTIME_ROLE` | `identity.roles.editor`, `identity.roles.tester`, `identity.roles.runtime`. |
| `CALLER_PERMIT_ISSUER`, `CALLER_MANAGER_ISSUER` | The management app's origin. |
| `CALLER_PERMIT_AUDIENCE`, `CALLER_MANAGER_AUDIENCE` | The runtime app's origin. |
| `CALLER_TENANT_ID` (runtime), `CALLER_WORKLOAD_*` | `environment.tenant_id`, `identity.runtime_app_client_id`, `identity.roles.management` and the principal ID of the management identity. |
| `CALLER_RUNTIME_AUDIENCE` | `identity.runtime_app_client_id`. |
| `CALLER_RUNTIME_CLIENT_ID` | The client ID of the script-created runtime identity (the same data source that gives the runtime its `AZURE_CLIENT_ID`). |
| `CALLER_APPROVED_HOSTS` | The management host, the text endpoint host and the live endpoint host. |

The derivation rules are confirmed by spikes S-4, S-11 and S-14 in [research.md](../research.md). A rule that a spike
changes is changed here and in the tests below.

## Tests

| Test | Asserts |
|---|---|
| Settings unit tests (both apps) | Each rule above, each reason code, that no value appears in an error, that local mode needs nothing. |
| Composition tests with fake ports | Composition succeeds from a complete settings set, with fake credential and container ports, and fails with the right reason code for each missing group. |
| Terraform tests | Every setting in the tables above is present on the right app, and no secret is a plain value. |
| Contract test | The names in this file equal the names the applications read, so a rename in code fails the test. |
