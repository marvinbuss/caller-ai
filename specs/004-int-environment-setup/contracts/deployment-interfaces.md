# Contract: Deployment and Module Interfaces

## Deployments (roots)

Every root has `variables.tf`, `providers.tf`, `outputs.tf` and `main.tf`, and one `<service>.tf` file
per service holding that service's module call (FR-001, FR-002). Each declares exactly two variables,
`environment` and its own object, with validation blocks that mirror the file rules.

### State deployment

| Direction | Name | Meaning |
|---|---|---|
| In | `environment`, `state` | See [environment-files.md](environment-files.md). |
| Out | `state_backend` | Non-secret coordinates: `resource_group_name`, `storage_account_name`, `container_name`, `plan_container_name`. |
| Providers | `azurerm` | No directory provider. |
| State | local during creation, then `<env>/state.tfstate` | FR-006, FR-007 |

### Bootstrap deployment

| Direction | Name | Meaning |
|---|---|---|
| In | `environment`, `bootstrap` | Network layout. |
| Out | `network` | Object: `resource_group_name`, `vnet_id`, `container_apps_subnet_id`, `private_endpoint_subnet_id`, `network_security_group_id`, `route_table_id`. |
| Out | `private_dns_zone_ids` | Map from purpose (`cosmos`, `vault`, `openai`, `cognitive`, `ai_services`, `monitor`, `oms`, `ods`, `agentsvc`, `blob`) to zone ID. |
| Providers | `azurerm` | |
| State | `<env>/bootstrap.tfstate` | FR-008 |

Bootstrap is the only deployment that declares a virtual network, subnet, network security group,
network security rule, route table or route (FR-005, FR-044). The structure check enforces it.

### App environment deployment

| Direction | Name | Meaning |
|---|---|---|
| In | `environment`, `app_environment` | Service sizing, images, references. |
| In (sensitive) | `management_signin_secret` | From `TF_VAR_management_signin_secret`. Never printed. |
| In (read) | Bootstrap state | `terraform_remote_state` on `<env>/bootstrap.tfstate`, read-only, identity-based. |
| Creates | Foundry account and model deployments | `model-access` module from `app_environment.model`, in `application.live_region`, private endpoint only (FR-039). |
| In (read) | Script-created identities | Data sources for the runtime identity by name. Preconditions name the setting on failure (FR-042). |
| Out | `environment_manifest` | Non-secret object for the check: app names and default domains, image references as deployed, replica settings, service IDs, private endpoint IDs, public access setting and rules, model account ID, redirect URI, vault secret names (never values). |
| Out | `workload_identity_client_ids` | Map runtime and management to client IDs. |
| Providers | `azurerm`, `azapi` (vault secrets), `tls` (key pair only) | No directory provider (FR-042). |
| State | `<env>/app_environment.tfstate` | Holds the two approved secrets under FR-031. |

## Reusable modules

All modules have `README.md`, `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, extra files per
resource, and `tests/*.tftest.hcl`. Every input and output has a description. Modules do not read state
files and do not call each other except through the root. A module's nested calls, where unavoidable, are
only in its `main.tf`.

| Module | Key inputs | Key outputs | Must not |
|---|---|---|---|
| `state-backend` | resource group, account name, retention, writer IDs | backend coordinates | enable shared keys or any rule other than the documented public endpoint |
| `network` | address space, subnets | VNet ID, subnet IDs, NSG and route table IDs | add a custom rule or route, create a firewall or NAT gateway |
| `private-dns` | VNet ID, zone purposes | zone IDs | create records |
| `private-endpoint` | target ID, subresource, subnet, zone ID | endpoint ID, connection state | approve cross-subscription connections |
| `container-app-environment` | name, subnet ID, workspace ID, public access flag | environment ID, default domain | be internal-only, add an outbound rule |
| `container-app` | environment ID, image, port, probes, identities, settings, secret references, ingress and allow-list | app ID, default domain | set a plain-text secret value, exceed one replica in `int`, set a non-listed setting |
| `data-store` | name, backup tier, TTLs, endpoint inputs, data role assignments (principal and container list) | account and database IDs | enable local authentication or public access, grant a role wider than the listed containers |
| `secret-store` | name, soft delete days, endpoint inputs, reader principals | vault ID, URI | create a secret |
| `key-vault-secret` | vault ID, name, sensitive value | secret ID (versionless) | write through the data plane, output the value, show the value in plan output (uses the sensitive body input) |
| `permit-key` | key ID | public key PEM (output), private key (sensitive output for `key-vault-secret` only) | write a file |
| `monitoring` | names, retention, cap, thresholds, action group, app IDs | workspace ID, alert IDs (platform metric alerts and log-query alerts for dependency failures and latency) | accept an alert without owner and runbook URL |
| `workload-identity` | names, resource group | identity IDs, client IDs, principal IDs | create a directory object |
| `model-access` | name, resource group, location, text and live deployment settings, runtime principal ID | account ID, name, endpoint, deployment names, role assignment ID | enable key access or public network access, auto-upgrade a model version (FR-039) |

## Tests required per module

Each module's `terraform test` asserts at least: the planned resources for defaults; every validation
failure; each "must not" above; and each output. Root tests assert the wiring above, that public access on
and off produce the expected rule sets for both apps, and that bootstrap is the only network owner.
