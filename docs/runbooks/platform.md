# Platform

What the `bootstrap` and `app_environment` deployments build for one environment, which service each module
owns, and which inputs are reviewed. The picture of the whole environment is in
[Environments](../environments.md); the security view is in [Platform security](platform-security.md).

## Services

| Service | Module | Root | Notes |
|---------|--------|------|-------|
| Virtual network, Container Apps subnet, private endpoint subnet, default network security group, route table | `network` | `bootstrap` | The only network resources in the repository (FR-005) |
| Private DNS zones for Cosmos, Key Vault, the Foundry account and their links | `private-dns` | `bootstrap` | Bootstrap owns the zones so a customer network can supply its own |
| Container Apps environment | `container-app-environment` | `app_environment` | Workload profiles, Consumption plan, in the delegated subnet, logging to the workspace; external so the public access flag flips in place |
| Runtime and management apps | `container-app` | `app_environment` | Single revision, exactly one replica, user-assigned identity, Key Vault secret references, health probes (runtime port 8000, management port 3000) |
| Data store | `data-store` | `app_environment` | Serverless Cosmos DB for NoSQL, one database, five containers, identity-only roles scoped per container |
| Secret store | `secret-store` | `app_environment` | Key Vault with RBAC authorization, purge protection and no public access |
| Secrets | `key-vault-secret` | `app_environment` | Written through the management API so a GitHub-hosted runner needs no network path to the vault |
| Permit signing key | `permit-key` | `app_environment` | RSA 2048 key pair for test-conversation start permits |
| Private endpoints | `private-endpoint` | `app_environment` | One each for the data store, the vault and the Foundry account |
| Management identity | `workload-identity` | `app_environment` | User-assigned identity with its role assignments |
| Foundry account and models | `model-access` | `app_environment` | `AIServices` account in `application.live_region` with key access and public network access off, the text deployment and the `gpt-live-1` realtime deployment (versions pinned), and the runtime's least model role |
| Monitoring | `monitoring` | `app_environment` | Log Analytics workspace, Application Insights, action group and alerts |
| State storage | `state-backend` | `state` | See [Terraform state](terraform-state.md) |

The data store's containers are `configuration`, `configuration-audit`, `call-sessions`, `call-audit` and
`text-conversations`. Time to live comes from the environment file: audit and session containers expire after the
configured period, text history is governed per item by the runtime, and configuration never expires.

## What is not built here

The sign-in registration, its roles and the runtime identity are not created by Terraform. The identity script
(or, until it is delivered, a directory administrator)
creates the directory objects. Terraform reads the runtime identity and fails with a message that names the
setting when it is missing. Model quota is not created either: it belongs to the subscription, and a missing
quota fails the apply of a model deployment with the provider's reason.

## Reviewed inputs

The inputs are the `environment` object (name, location, subscription, tenant, deployment identity, tags and
public access) and the four deployment objects in `<env>.tfvars.json`. The validator checks their shape and the
rules in the [environment files contract](../../specs/004-int-environment-setup/contracts/environment-files.md),
and rejects any secret. Every module's inputs have descriptions; read a module's README for its inputs, outputs,
failure behavior and security assumptions.

## Cost boundaries

An idle environment costs little: one replica per app, a serverless data store, a pay-as-you-go workspace and
private endpoints. There is no firewall, NAT gateway, registry or cluster. The log workspace has a daily cap and
a retention period in the environment file. Review the plan's change list before approving anything that creates
a paid resource.

## Verification

`verify_terraform.py check` runs the native tests of every module and root with mocked providers; the structure
check keeps one service module call per service file. Neither needs an Azure credential.
