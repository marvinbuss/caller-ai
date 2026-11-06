# Application live mode

How the two applications start in a deployed environment. In local mode (`CALLER_MODE` unset or `local`) both
apps behave as before: health and status only, no other setting, no credential. In live mode the deployment
gives each container a fixed set of settings and the app refuses to start unless all of them are valid.

## Status

| Part | Status |
|------|--------|
| Settings contract | Delivered: [application settings](../../specs/004-int-environment-setup/contracts/application-settings.md) |
| Settings validation with fixed reason codes in both apps | Delivered and tested |
| Terraform gives each app exactly the contract's settings | Delivered; a test compares the names in the contract, the deployment and the applications |
| Live composition of both apps (managed identity and data store clients, reviewed runtime options, workload and sign-in identity, permit keys, management fingerprint authority) | Delivered and tested with fake clients; not yet run against Azure |
| Frontend address at start (the exported frontend follows `CALLER_PUBLIC_ORIGIN` or `API_URL`) | Delivered; container test in `code/tools/tests/test_management_image.py` |

## The settings

The runtime reads the `CALLER_*` names and `AZURE_CLIENT_ID` listed under *Runtime* in the contract. The
management app reads those under *Management*. Both freeze their settings at start and never echo a value in
an error. Two management settings are secrets, `CALLER_ENTRA_CLIENT_SECRET` and `CALLER_SIGNING_KEY_PEM`; the <!-- pragma: allowlist secret -->
deployment gives them only as Key Vault references that the platform resolves with the app's identity, never as
plain values.

Most values are derived by the deployment so that nothing is copied by hand: the app origins from the app names
and the environment's default domain, the data store and model endpoints from the module outputs, the public
permit key from the generated key pair, and the roles from `app_environment.identity.roles`.

## Reviewed composition

A live start also needs `CALLER_REVIEW_REFERENCE`, the recorded approval reference of the live composition. An
empty value stops the start with `review_missing`. The management app additionally reads
`CALLER_REVIEWED_FINGERPRINT`, the fingerprint of the nonsecret settings that were reviewed; a different
computed fingerprint stops the start with `fingerprint_mismatch`. Review and record both in
`app_environment.application` before an apply.

### Compute the management fingerprint

The fingerprint covers every nonsecret management setting except the review reference, the reviewed
fingerprint and what exists only after the apply: the two app origins and what derives from them
(`CALLER_MANAGEMENT_ORIGIN`, `CALLER_PUBLIC_ORIGIN`, `CALLER_RUNTIME_ENDPOINT`, `CALLER_RUNTIME_AUDIENCE`,
`CALLER_PERMIT_ISSUER`, `CALLER_PERMIT_AUDIENCE`) and the management identity's `AZURE_CLIENT_ID`. Those are covered
through the names that fix them. Secrets never enter it. Put the nonsecret `CALLER_*` values of the planned
deployment into a JSON file (names to values) and run:

```powershell
uv run --directory code/management --locked python -m management_app.fingerprint --settings settings.json
```

The tool prints one 64-character value or one reason code and exits 2. Record the value as
`app_environment.application.reviewed_fingerprint`. Without `--settings` it reads the environment.

Two more checks run at start. `CALLER_PUBLIC_ORIGIN` must equal `CALLER_MANAGEMENT_ORIGIN` (the browser reaches the
management app on its own origin, and the image points the frontend there at start), and the runtime's identity
(`CALLER_RUNTIME_CLIENT_ID`) is the only application the management app accepts on the runtime-configuration
endpoint.

## Reason codes

A refused start reports exactly one code and no value.

| Code | Meaning | What to check |
|------|---------|---------------|
| `settings_missing` | A required setting is absent or empty | The container's settings against the contract; the Terraform plan for the app |
| `settings_invalid` | A setting has the wrong format or range | The named category: origins are HTTPS origins, GUIDs are GUIDs, the live region is one of `canadacentral`, `centralus`, `eastus2`, `swedencentral`, live sessions are 5 to 10000, clock skew is 0 or 1 |
| `review_missing` | `CALLER_REVIEW_REFERENCE` is empty | Record the approval reference in `app_environment.application.review_reference` |
| `fingerprint_mismatch` | The management fingerprint differs from the reviewed one | Review the changed nonsecret settings and record the new fingerprint |
| `identity_unavailable` | The managed identity cannot produce a credential | The identity is attached to the app and `AZURE_CLIENT_ID` is its client ID |
| `store_unavailable` | The data store cannot be reached or refuses the identity | The private endpoint is approved and the identity holds its container roles |
| `key_invalid` | The permit signing key or a verification key is unusable | The private key secret in the vault and the public key setting match one generated pair |

The environment check reports the start reason code of a failed revision next to the failing app.

## Change a setting

Settings change only through the environment files: edit `<env>.tfvars.json`, validate, run the
`terraform-app-environment` plan, review it and approve the apply. A changed setting starts a new revision. Do
not edit a container's settings in the portal; the next apply would revert it.
