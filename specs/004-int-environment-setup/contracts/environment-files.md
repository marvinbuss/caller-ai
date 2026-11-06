# Contract: Environment Files

One directory per environment under `code/infra/environments/`. For this feature only `int/` exists.
No other file belongs in an environment directory (FR-009, FR-014). The sign-in secret is not an
environment file: it travels as a sensitive environment variable from a protected GitHub environment
secret (FR-046). Placeholder values below use reserved example names and zero GUIDs, and the owner
supplies the real values.

```text
code/infra/environments/int/
├── int.tfvars.json
├── state.azurerm.tfbackend.json
├── bootstrap.azurerm.tfbackend.json
└── app_environment.azurerm.tfbackend.json
```

## Variable file: `<env>.tfvars.json`

Strict JSON with unique keys. Top-level objects are `environment`, `state`, `bootstrap`,
`app_environment`. The delivery tool gives each deployment only `environment` plus its own object. A
deployment declares exactly those two variables, plus the sensitive input in the table below when it needs one.

```json
{
  "environment": {
    "name": "int",
    "tenant_id": "00000000-0000-4000-8000-000000000001",
    "subscription_id": "00000000-0000-4000-8000-000000000002",
    "client_id": "00000000-0000-4000-8000-000000000003",
    "location": "swedencentral",
    "tags": { "owner": "replace-with-accountable-owner" },
    "public_access": { "enabled": true, "allowed_cidrs": ["0.0.0.0/0"] }
  },
  "state": {
    "resource_group_name": "rg-caller-int-state",
    "storage_account_name": "callerintstate0001",
    "state_retention_days": 30,
    "writer_principal_ids": ["00000000-0000-4000-8000-000000000004"]
  },
  "bootstrap": {
    "resource_group_name": "rg-caller-int-network",
    "vnet_name": "vnet-caller-int",
    "address_space": "10.60.0.0/22",
    "container_apps_subnet_cidr": "10.60.0.0/26",
    "private_endpoint_subnet_cidr": "10.60.1.0/26"
  },
  "app_environment": {
    "resource_group_name": "rg-caller-int-apps",
    "container_apps": {
      "environment_name": "cae-caller-int",
      "runtime": {
        "app_name": "ca-caller-int-runtime",
        "image": { "repository": "ghcr.io/marvinbuss/caller-ai/runtime", "tag": "sha-0000000000000000000000000000000000000000" },
        "cpu": 0.5,
        "memory": "1Gi"
      },
      "management": {
        "app_name": "ca-caller-int-management",
        "image": { "repository": "ghcr.io/marvinbuss/caller-ai/management", "tag": "sha-0000000000000000000000000000000000000000" },
        "cpu": 0.5,
        "memory": "1Gi"
      }
    },
    "identity": {
      "resource_group_name": "rg-caller-int-identity",
      "management_app_client_id": "00000000-0000-4000-8000-000000000005",
      "runtime_identity_name": "id-caller-int-runtime",
      "roles": { "editor": "Configuration.Manager", "tester": "Configuration.Tester", "runtime": "Runtime.Caller" },
      "user_group_id": "00000000-0000-4000-8000-000000000007"
    },
    "model": {
      "account_id": "/subscriptions/00000000-0000-4000-8000-000000000006/resourceGroups/rg-model/providers/Microsoft.CognitiveServices/accounts/replace-model",
      "text_deployment": "replace-text-deployment",
      "live_deployment": "replace-live-deployment"
    },
    "data_store": { "name": "cosmos-caller-int", "backup_tier": "Continuous7Days", "configuration_audit_ttl": 86400, "call_session_ttl": 86400, "call_audit_ttl": 86400 },
    "secret_store": { "name": "kv-caller-int", "soft_delete_days": 30 },
    "signing_key": { "key_id": "int-1" },
    "application": { "review_reference": "int-live-review-1", "reviewed_fingerprint": "replace-after-review", "available_live_sessions": 5, "max_input_tokens": 8000, "max_output_tokens": 1024, "voice": "replace-voice", "live_region": "swedencentral" },
    "monitoring": {
      "workspace_name": "log-caller-int",
      "retention_days": 30,
      "daily_cap_gb": 1,
      "action_group": { "name": "ag-caller-int", "short_name": "callerint", "email_receivers": { "operations": "operations@example.invalid" } },
      "alerts": {
        "owner": "replace-with-oncall-owner",
        "runbook_url": "https://example.invalid/docs/runbooks/monitoring.md",
        "http_5xx_count": 5,
        "restart_count": 3,
        "cpu_percent": 80,
        "memory_percent": 80
      }
    }
  }
}
```

### Sensitive inputs (never in a file)

| Input | Source | Used by |
|---|---|---|
| `management_signin_secret` (Terraform variable, sensitive) | `TF_VAR_management_signin_secret` from the GitHub environment secret `MANAGEMENT_SIGNIN_SECRET` | App environment deployment, which writes it to the vault through the management API |

### Rules

| Rule | Requirement |
|---|---|
| Valid JSON, no duplicate keys, no unknown top-level keys. | FR-013 |
| `environment.name` equals the directory name and the file prefix. | FR-010 |
| `public_access` follows the data model. Enabled needs a non-empty allow-list. The open ranges `0.0.0.0/0` and `::/0` are accepted only when listed explicitly and give a warning (`int` lists `0.0.0.0/0` so GitHub-hosted runners can reach it). | FR-019 |
| Image has exactly one of `tag` or `digest`. `latest` is rejected. | FR-037 |
| `model.text_deployment` and `model.live_deployment` are non-empty and differ. | FR-039 |
| `identity` values are present and non-empty, including `user_group_id` (a GUID). Role names are read by the identity script and the app environment deployment. | FR-019, FR-042, FR-052 |
| `container_apps_subnet_cidr` is at least a /27 inside the address space. | FR-005 |
| `signing_key.key_id` matches `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`. | FR-051 |
| No key name matches `secret`, `password`, `key`, `token`, `connection_string`, `sas` (case-insensitive) unless it is on the allow-list `secret_store`, `signing_key`, `key_id`, `soft_delete_days`, `max_input_tokens`, `max_output_tokens`. No value looks like a secret. | FR-012, SC-008 |
| Resource names follow Azure naming rules for their type. | FR-013 |
| Subscription IDs, state keys and global names are unique across all environment directories. | FR-011 |
| A file for another environment that is incomplete is ignored by this environment's validation and reported only for its own. | Edge Cases |

## Backend files: `<deployment>.azurerm.tfbackend.json`

One per deployment. Names: `state`, `bootstrap`, `app_environment`.

```json
{
  "resource_group_name": "rg-caller-int-state",
  "storage_account_name": "callerintstate0001",
  "container_name": "tfstate",
  "key": "int/app_environment.tfstate",
  "use_azuread_auth": true,
  "use_oidc": true,
  "use_cli": false
}
```

| Rule | Requirement |
|---|---|
| `resource_group_name` and `storage_account_name` equal the variable file's `state` object. | FR-011 |
| `container_name` is `tfstate`. `key` is `<env>/<deployment>.tfstate`. | FR-011 |
| No `access_key`, `sas_token`, `client_secret` or `use_cli: true`. The one-time state creation sets CLI authentication by environment variable at run time, not in the file. | FR-012, FR-031 |

## Error format

Every validation failure is one line of JSON on standard error and a non-zero exit:

```json
{"file": "code/infra/environments/int/int.tfvars.json", "setting": "environment.public_access.allowed_cidrs", "problem": "required_when_enabled"}
```

`problem` codes are stable machine names such as `missing`, `invalid_format`, `duplicate_across_environments`,
`secret_like_key`, `secret_like_value`, `required_when_enabled`, `invalid_cidr`, `unknown_key`. An explicit open range
in the allow-list gives a warning line `{"warning": "open_range", "file": "...", "setting": "..."}` and exit code 0. Messages never echo the
offending value (FR-013, SC-006, SC-008).
