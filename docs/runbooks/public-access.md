# Public access

Whether the two container apps are reachable from the internet is one reviewed setting per environment. This
guide covers the flag, the allow-list, enabling, disabling and verifying. Concepts and limits are in
[Environments](../environments.md).

## The setting

In the environment's `<env>.tfvars.json`, the `environment.public_access` object holds:

| Key | Meaning |
|-----|---------|
| `enabled` | `true` gives both apps external ingress and sets the Container Apps environment's public network access to enabled. `false` makes both apps internal and disables public network access. |
| `allowed_cidrs` | The source ranges the apps accept when public access is enabled. It must not be empty when `enabled` is true, and every entry must be a valid CIDR range. |

Public access is off by default. The change is made in place: no network resource, app or data store is
replaced when the flag flips, only the access settings change.

## What the ranges mean

The allow-list is applied as IP security rules on each app. A request from outside every range is refused by
the platform before it reaches the application.

The `int` environment uses the explicit open range `0.0.0.0/0`. The validator reports a warning for it
(`open_range`) but allows it, because testers connect from changing addresses. With an open range:

- the platform accepts every source, so **sign-in, roles and start permits are the only access control**;
- the runtime and management health endpoints and the management sign-in page are reachable by anyone;
- the environment check reports that no source restriction applies instead of probing a refusal.

Use a narrow list for any environment that is not a throwaway test.

## Enable

1. Set `environment.public_access.enabled` to `true` and list the ranges in `allowed_cidrs`.
2. Run `uv run --project code/tools --locked python code/tools/scripts/validate_environment.py`.
3. Run the `terraform-app-environment` workflow, review the plan (only access settings change) and approve
   the apply.
4. Verify as described below.

## Disable

1. Set `enabled` to `false` (the ranges may stay).
2. Validate and run `terraform-app-environment` the same way.
3. Verify: both apps refuse requests from every source. The environment check passes and skips the functional
   journey with the single allowed reason `public_access_disabled`.

## Verify

The environment check's `public-access-matches` result compares the environment's public network access, each
app's external ingress and the rules with the flag. With a closed range list it also sends a request from the
pipeline runner to the runtime health endpoint and expects a refusal (HTTP 403 or a connection error). Run it
yourself with:

```powershell
uv run --project code/tools --locked python code/tools/scripts/check_environment.py --environment int --manifest <manifest.json>
```

A failed result names the first mismatch: the environment setting, an app whose ingress differs, or a rule set
that differs from `allowed_cidrs`.
