"""Verifies a deployed environment against its description (FR-028, FR-029, FR-037, FR-042).

Reads the `environment_manifest` output (a JSON file) and the reviewed environment files, asks
Azure Resource Manager and Microsoft Graph for read-only state through the Azure CLI sign-in of the
caller, and reports one result per check. Output holds names, identifiers and fixed reason codes;
never a secret, a secret reference value or an input value. Exit code 0 only when nothing failed.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
from ci import ROOT

ENVIRONMENTS = ROOT / "code/infra/environments"
ALLOWED_SKIP = "public_access_disabled"
GRAPH = "https://graph.microsoft.com/v1.0"
ARM = "https://management.azure.com"
API_VERSIONS = {
    "microsoft.app/managedenvironments": "2024-03-01",
    "microsoft.app/containerapps": "2024-03-01",
    "microsoft.documentdb/databaseaccounts": "2024-05-15",
    "microsoft.keyvault/vaults": "2023-07-01",
    "microsoft.operationalinsights/workspaces": "2023-09-01",
    "microsoft.insights/actiongroups": "2023-01-01",
    "microsoft.cognitiveservices/accounts": "2024-10-01",
    "microsoft.network/privateendpoints": "2024-05-01",
    "microsoft.network/networksecuritygroups": "2024-05-01",
    "microsoft.network/routetables": "2024-05-01",
    "microsoft.storage/storageaccounts": "2023-05-01",
}
RUNTIME_SETTINGS = (
    "CALLER_MODE AZURE_CLIENT_ID CALLER_REVIEW_REFERENCE CALLER_COSMOS_ENDPOINT "
    "CALLER_COSMOS_DATABASE CALLER_TEXT_HISTORY_TTL_SECONDS CALLER_MANAGEMENT_ENDPOINT "
    "CALLER_MANAGEMENT_AUDIENCE CALLER_TEXT_ENDPOINT CALLER_TEXT_DEPLOYMENT CALLER_LIVE_ENDPOINT "
    "CALLER_LIVE_REGION CALLER_LIVE_VOICE CALLER_APPROVED_HOSTS CALLER_MAX_INPUT_TOKENS "
    "CALLER_MAX_OUTPUT_TOKENS CALLER_AVAILABLE_LIVE_SESSIONS CALLER_MANAGER_ISSUER "
    "CALLER_MANAGER_AUDIENCE CALLER_PERMIT_VERIFICATION_KEYS CALLER_MAX_CLOCK_SKEW_SECONDS"
).split()
MANAGEMENT_SETTINGS = (
    "CALLER_MODE AZURE_CLIENT_ID CALLER_REVIEW_REFERENCE CALLER_REVIEWED_FINGERPRINT "
    "CALLER_COSMOS_ENDPOINT CALLER_COSMOS_DATABASE CALLER_AUDIT_RETENTION_SECONDS "
    "CALLER_TENANT_ID CALLER_ENTRA_CLIENT_ID CALLER_ENTRA_CLIENT_SECRET CALLER_API_AUDIENCE "
    "CALLER_MANAGER_ROLE CALLER_MANAGER_SCOPE CALLER_TEST_ROLE CALLER_RUNTIME_ROLE "
    "CALLER_MANAGEMENT_ORIGIN CALLER_RUNTIME_ENDPOINT CALLER_RUNTIME_AUDIENCE "
    "CALLER_PERMIT_ISSUER CALLER_PERMIT_AUDIENCE CALLER_SIGNING_KEY_ID CALLER_SIGNING_KEY_PEM "
    "CALLER_PUBLIC_ORIGIN"
).split()
SECRET_SETTINGS = {"CALLER_ENTRA_CLIENT_SECRET", "CALLER_SIGNING_KEY_PEM"}
APP_SETTINGS = {"runtime": RUNTIME_SETTINGS, "management": MANAGEMENT_SETTINGS}


class Access(Protocol):
    """Read-only access to Resource Manager and Graph; the real one wraps the Azure CLI."""

    def arm(self, path: str, api_version: str) -> dict[str, Any]: ...

    def arm_post(self, path: str, api_version: str, body: dict[str, Any]) -> int: ...

    def graph(self, path: str) -> dict[str, Any]: ...


class CliAccess:
    def _rest(self, method: str, url: str, body: dict[str, Any] | None = None) -> str:
        command = ["az", "rest", "--method", method, "--url", url, "--only-show-errors"]
        if body is not None:
            command += ["--body", json.dumps(body), "--headers", "Content-Type=application/json"]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise LookupError("request_failed")
        return result.stdout

    def arm(self, path: str, api_version: str) -> dict[str, Any]:
        separator = "&" if "?" in path else "?"
        output = self._rest("get", f"{ARM}{path}{separator}api-version={api_version}")
        return json.loads(output or "{}")

    def arm_post(self, path: str, api_version: str, body: dict[str, Any]) -> int:
        self._rest("post", f"{ARM}{path}?api-version={api_version}", body)
        return 202

    def graph(self, path: str) -> dict[str, Any]:
        return json.loads(self._rest("get", f"{GRAPH}{path}") or "{}")


@dataclass
class Check:
    id: str
    service: str
    status: str
    detail: str


@dataclass
class Context:
    environment: str
    manifest: dict[str, Any]
    files: dict[str, Any]
    access: Access
    http: httpx.Client | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    wait_seconds: int = 300
    functional: bool = False
    test_alert: bool = False
    results: list[Check] = field(default_factory=list)


def api_version(resource_id: str) -> str:
    match = re.search(r"/providers/([^/]+/[^/]+)/", resource_id, re.IGNORECASE)
    if match is None or match.group(1).lower() not in API_VERSIONS:
        raise ValueError("unknown_resource_type")
    return API_VERSIONS[match.group(1).lower()]


def fetch(context: Context, resource_id: str) -> dict[str, Any] | None:
    try:
        return context.access.arm(resource_id, api_version(resource_id))
    except LookupError:
        return None


def apps(context: Context) -> dict[str, dict[str, Any]]:
    return context.manifest["container_apps"]


def passed(check_id: str, service: str, detail: str) -> Check:
    return Check(check_id, service, "pass", detail)


def failed(check_id: str, service: str, detail: str) -> Check:
    return Check(check_id, service, "fail", detail)


def services_provisioned(context: Context) -> Check:
    names = {
        "container_environment": context.manifest["services"]["container_environment"],
        "data_store": context.manifest["services"]["data_store_id"],
        "secret_store": context.manifest["services"]["secret_store_id"],
        "workspace": context.manifest["services"]["workspace_id"],
        "action_group": context.manifest["services"]["action_group_id"],
        "model_account": context.manifest["services"]["model_account_id"],
        **{f"app_{name}": app["id"] for name, app in apps(context).items()},
    }
    problems = []
    for name, resource_id in names.items():
        resource = fetch(context, resource_id)
        properties = (resource or {}).get("properties", {})
        if resource is None:
            problems.append(f"{name}: missing")
        elif properties.get("provisioningState") not in {"Succeeded", None}:
            problems.append(f"{name}: provisioning state {properties.get('provisioningState')}")
        elif name in {"data_store", "secret_store"} and (
            properties.get("publicNetworkAccess") != "Disabled"
        ):
            problems.append(f"{name}: public access is not disabled")
    if problems:
        return failed("services-provisioned", "all", "; ".join(problems))
    return passed("services-provisioned", "all", f"{len(names)} services present")


def settings_complete(context: Context) -> Check:
    problems = []
    for name, app in apps(context).items():
        resource = fetch(context, app["id"])
        if resource is None:
            problems.append(f"{name}: missing")
            continue
        containers = resource["properties"]["template"]["containers"]
        env = {item["name"]: item for item in containers[0].get("env", [])}
        missing = sorted(set(APP_SETTINGS[name]) - set(env))
        if missing:
            problems.append(f"{name}: missing settings {', '.join(missing)}")
        plain = sorted(
            key
            for key in SECRET_SETTINGS & set(env)
            if "value" in env[key] or not env[key].get("secretRef")
        )
        if plain:
            problems.append(f"{name}: secret settings not by reference {', '.join(plain)}")
        secrets = resource["properties"].get("configuration", {}).get("secrets") or []
        if any("value" in secret for secret in secrets):
            problems.append(f"{name}: a secret is stored as a plain value")
    if problems:
        return failed("settings-complete", "container-apps", "; ".join(problems))
    return passed(
        "settings-complete", "container-apps", "all settings present, secrets by reference"
    )


def revision_state(context: Context, app: dict[str, Any]) -> tuple[bool, str]:
    resource = fetch(context, app["id"]) or {}
    properties = resource.get("properties", {})
    latest = properties.get("latestRevisionName")
    if not latest:
        return False, "no revision"
    revision = fetch_revision(context, app["id"], latest)
    state = (revision or {}).get("properties", {})
    healthy = state.get("healthState") == "Healthy" and state.get("runningState") in {
        "Running",
        "Processing",
        None,
    }
    return healthy, str(state.get("runningStateDetails") or state.get("healthState") or "unknown")


def fetch_revision(context: Context, app_id: str, revision: str) -> dict[str, Any] | None:
    try:
        return context.access.arm(
            f"{app_id}/revisions/{revision}", API_VERSIONS["microsoft.app/containerapps"]
        )
    except LookupError:
        return None


def revisions_healthy(context: Context) -> Check:
    deadline = context.clock() + context.wait_seconds
    pending = dict(apps(context))
    detail = {}
    while pending:
        for name, app in list(pending.items()):
            healthy, reason = revision_state(context, app)
            detail[name] = reason
            if healthy:
                del pending[name]
        if not pending or context.clock() >= deadline:
            break
        context.sleep(10)
    if pending:
        reasons = "; ".join(f"{name}: {detail[name]}" for name in pending)
        return failed("revisions-healthy", "container-apps", reasons)
    return passed("revisions-healthy", "container-apps", "latest revisions healthy")


def images_running(context: Context) -> Check:
    problems = []
    for app in apps(context).values():
        resource = fetch(context, app["id"]) or {}
        containers = resource.get("properties", {}).get("template", {}).get("containers", [])
        image = containers[0].get("image", "") if containers else ""
        expected = app["image"]
        if not (image.endswith(":" + expected) or image.endswith("@" + expected)):
            healthy, reason = revision_state(context, app)
            suffix = "" if healthy else f" ({reason})"
            problems.append(f"{app['name']}: not running {expected}{suffix}")
    if problems:
        return failed("images-running", "container-apps", "; ".join(problems))
    return passed("images-running", "container-apps", "configured images are running")


def public_access_matches(context: Context) -> Check:
    access = context.manifest["public_access"]
    enabled = access["enabled"]
    problems = []
    environment = fetch(context, context.manifest["services"]["container_environment"]) or {}
    state = environment.get("properties", {}).get("publicNetworkAccess")
    if state != ("Enabled" if enabled else "Disabled"):
        problems.append("environment public network access does not match")
    for name, app in apps(context).items():
        resource = fetch(context, app["id"]) or {}
        ingress = resource.get("properties", {}).get("configuration", {}).get("ingress") or {}
        if bool(ingress.get("external")) != enabled:
            problems.append(f"{name}: external ingress does not match")
        rules = {rule.get("ipAddressRange") for rule in ingress.get("ipSecurityRestrictions") or []}
        if enabled and rules != set(access["allowed_cidrs"]):
            problems.append(f"{name}: allow-list rules do not match")
    if problems:
        return failed("public-access-matches", "network", "; ".join(problems))
    if not enabled:
        return passed("public-access-matches", "network", "public access is off")
    if "0.0.0.0/0" in access["allowed_cidrs"]:
        return passed("public-access-matches", "network", "no source restriction applies")
    if context.http is None:
        return failed("public-access-matches", "network", "no HTTP client for the refusal probe")
    try:
        response = context.http.get(f"https://{apps(context)['runtime']['host']}/healthz")
    except httpx.HTTPError:
        return passed("public-access-matches", "network", "request from the runner refused")
    if response.status_code == 403:
        return passed("public-access-matches", "network", "request from the runner refused")
    return failed("public-access-matches", "network", "a request outside the ranges was served")


def no_custom_outbound(context: Context) -> Check:
    network = context.manifest["network"]
    problems = []
    group = fetch(context, network["network_security_group_id"])
    if group is None:
        problems.append("the network security group cannot be read")
    elif group.get("properties", {}).get("securityRules"):
        problems.append("custom security rules exist")
    table = fetch(context, network["route_table_id"])
    if table is None:
        problems.append("the route table cannot be read")
    elif table.get("properties", {}).get("routes"):
        problems.append("custom routes exist")
    match = re.match(r"(/subscriptions/[^/]+/resourceGroups/[^/]+)/", network["route_table_id"])
    if match:
        try:
            listing = context.access.arm(f"{match.group(1)}/resources", "2021-04-01")
        except LookupError:
            listing = {}
            problems.append("the resource list cannot be read")
        kinds = {item.get("type", "").lower() for item in listing.get("value", [])}
        for kind in ("microsoft.network/natgateways", "microsoft.network/azurefirewalls"):
            if kind in kinds:
                problems.append(f"{kind.split('/')[1]} exists")
    if problems:
        return failed("no-custom-outbound", "network", "; ".join(problems))
    return passed("no-custom-outbound", "network", "default rules only")


def private_endpoints_approved(context: Context) -> Check:
    problems = []
    for resource_id in context.manifest["private_endpoints"]:
        endpoint = fetch(context, resource_id) or {}
        properties = endpoint.get("properties", {})
        connections = [
            *properties.get("privateLinkServiceConnections", []),
            *properties.get("manualPrivateLinkServiceConnections", []),
        ]
        statuses = {
            item.get("properties", {}).get("privateLinkServiceConnectionState", {}).get("status")
            for item in connections
        }
        name = resource_id.rsplit("/", 1)[-1]
        if not connections or statuses != {"Approved"}:
            state = ", ".join(sorted(str(status) for status in statuses)) or "none"
            problems.append(f"{name}: {state}")
    if problems:
        return failed("private-endpoints-approved", "network", "; ".join(problems))
    return passed("private-endpoints-approved", "network", "all connections approved")


def model_access(context: Context) -> Check:
    account_id = context.manifest["services"]["model_account_id"]
    try:
        deployments = context.access.arm(
            f"{account_id}/deployments", API_VERSIONS["microsoft.cognitiveservices/accounts"]
        ).get("value", [])
        assignments = context.access.arm(
            f"{account_id}/providers/Microsoft.Authorization/roleAssignments", "2022-04-01"
        ).get("value", [])
    except LookupError:
        return failed("model-access", "model", "the model account cannot be read")
    principal = context.manifest["identity"]["runtime_principal_id"].lower()
    holds = any(
        item.get("properties", {}).get("principalId", "").lower() == principal
        for item in assignments
    )
    if len(deployments) < 2:
        return failed("model-access", "model", "fewer than two deployments")
    if not holds:
        return failed("model-access", "model", "the runtime identity holds no role on the account")
    return passed("model-access", "model", "deployments present and role held")


def service_principal(context: Context) -> dict[str, Any] | None:
    client_id = context.manifest["identity"]["management_app_client_id"]
    try:
        found = context.access.graph(
            f"/servicePrincipals?$filter=appId eq '{client_id}'&$select=id,appRoles"
        ).get("value", [])
    except LookupError:
        return None
    return found[0] if found else None


def registration_and_roles_exist(context: Context) -> Check:
    principal = service_principal(context)
    if principal is None:
        return failed(
            "registration-and-roles-exist",
            "identity",
            "identity.management_app_client_id: no sign-in registration found",
        )
    values = {role.get("value") for role in principal.get("appRoles", [])}
    missing = sorted(
        f"identity.roles.{key}"
        for key, name in context.manifest["identity"]["roles"].items()
        if name not in values
    )
    if missing:
        return failed("registration-and-roles-exist", "identity", "missing: " + ", ".join(missing))
    return passed("registration-and-roles-exist", "identity", "registration and roles exist")


def runtime_role_assigned(context: Context) -> Check:
    principal = service_principal(context)
    if principal is None:
        return failed("runtime-role-assigned", "identity", "no sign-in registration found")
    role = next(
        (
            item
            for item in principal.get("appRoles", [])
            if item.get("value") == context.manifest["identity"]["roles"]["runtime"]
        ),
        None,
    )
    if role is None:
        return failed("runtime-role-assigned", "identity", "the runtime role is not defined")
    try:
        assigned = context.access.graph(f"/servicePrincipals/{principal['id']}/appRoleAssignedTo")
    except LookupError:
        return failed("runtime-role-assigned", "identity", "assignments cannot be read")
    runtime = context.manifest["identity"]["runtime_principal_id"].lower()
    for item in assigned.get("value", []):
        if item.get("principalId", "").lower() == runtime and item.get("appRoleId") == role["id"]:
            return passed("runtime-role-assigned", "identity", "runtime role assigned")
    return failed("runtime-role-assigned", "identity", "the runtime identity lacks the role")


def secrets_present(context: Context) -> Check:
    vault = context.manifest["services"]["secret_store_id"]
    problems = []
    for name in context.manifest["vault_secret_names"]:
        try:
            secret = context.access.arm(
                f"{vault}/secrets/{name}", API_VERSIONS["microsoft.keyvault/vaults"]
            )
        except LookupError:
            problems.append(f"{name}: missing")
            continue
        attributes = secret.get("properties", {}).get("attributes", {})
        expires = attributes.get("exp")
        if attributes.get("enabled") is False:
            problems.append(f"{name}: disabled")
        elif isinstance(expires, int) and expires < time.time():
            problems.append(f"{name}: expired")
    if problems:
        return failed("secrets-present", "secret-store", "; ".join(problems))
    return passed("secrets-present", "secret-store", "secret names present")


def signin_redirect_matches(context: Context) -> Check:
    management = apps(context)["management"]
    resource = fetch(context, management["id"]) or {}
    fqdn = resource.get("properties", {}).get("configuration", {}).get("ingress", {}).get("fqdn")
    expected = f"https://{fqdn}/auth/callback" if fqdn else ""
    client_id = context.manifest["identity"]["management_app_client_id"]
    try:
        registered = context.access.graph(
            f"/applications?$filter=appId eq '{client_id}'&$select=web"
        ).get("value", [])
    except LookupError:
        return failed("signin-redirect-matches", "identity", "the registration cannot be read")
    uris = registered[0].get("web", {}).get("redirectUris", []) if registered else []
    if not expected or expected != context.manifest["redirect_uri"] or expected not in uris:
        return failed(
            "signin-redirect-matches",
            "identity",
            "the registered redirect address does not match the management app address",
        )
    return passed("signin-redirect-matches", "identity", "redirect address registered")


def state_isolated(context: Context) -> Check:
    folder = ENVIRONMENTS / context.environment
    keys = []
    for stack in ("state", "bootstrap", "app_environment"):
        try:
            keys.append(json.loads((folder / f"{stack}.azurerm.tfbackend.json").read_text())["key"])
        except (OSError, ValueError, KeyError):
            return failed("state-isolated", "state", f"{stack}: backend file unreadable")
    if len(set(keys)) != len(keys):
        return failed("state-isolated", "state", "state keys are not distinct")
    state = context.files["state"]
    account_id = (
        f"/subscriptions/{context.files['environment']['subscription_id']}"
        f"/resourceGroups/{state['resource_group_name']}/providers/Microsoft.Storage"
        f"/storageAccounts/{state['storage_account_name']}"
    )
    account = fetch(context, account_id) or {}
    if account.get("properties", {}).get("allowSharedKeyAccess") is not False:
        return failed(
            "state-isolated", "state", "shared keys are not disabled on the state account"
        )
    return passed("state-isolated", "state", "distinct keys, shared keys disabled")


def alert_test(context: Context) -> Check:
    if not context.test_alert:
        return Check("alert-test", "monitoring", "skipped", "not requested")
    action_group = context.manifest["services"]["action_group_id"]
    try:
        code = context.access.arm_post(
            f"{action_group}/createNotifications",
            "2021-09-01",
            {"alertType": "servicehealth"},
        )
    except LookupError:
        return failed("alert-test", "monitoring", "the test notification was rejected")
    if code in {200, 202}:
        return passed("alert-test", "monitoring", "test notification accepted")
    return failed("alert-test", "monitoring", "the test notification was rejected")


CHECKS: tuple[Callable[[Context], Check], ...] = (
    services_provisioned,
    settings_complete,
    revisions_healthy,
    images_running,
    public_access_matches,
    no_custom_outbound,
    private_endpoints_approved,
    model_access,
    runtime_role_assigned,
    registration_and_roles_exist,
    secrets_present,
    signin_redirect_matches,
    state_isolated,
    alert_test,
)


def run_checks(
    context: Context, extra: tuple[Callable[[Context], Check], ...] = ()
) -> dict[str, Any]:
    results = []
    for check in (*CHECKS, *extra):
        try:
            outcome = check(context)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            outcome = failed(check.__name__.replace("_", "-"), "unknown", "the check could not run")
        results.append(outcome)
    # A skipped check fails the run unless it is the only allowed skip or an unrequested option.
    bad = [
        item
        for item in results
        if item.status == "fail"
        or (item.status == "skipped" and item.detail not in {ALLOWED_SKIP, "not requested"})
    ]
    return {
        "environment": context.environment,
        "result": "fail" if bad else "pass",
        "checks": [item.__dict__ for item in results],
    }


def load_files(environment: str, base: Path = ENVIRONMENTS) -> dict[str, Any]:
    return json.loads((base / environment / f"{environment}.tfvars.json").read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--test-alert", action="store_true")
    parser.add_argument("--functional", action="store_true")
    parser.add_argument("--json", type=Path)
    arguments = parser.parse_args(argv)
    try:
        context = Context(
            environment=arguments.environment,
            manifest=json.loads(arguments.manifest.read_text()),
            files=load_files(arguments.environment),
            access=CliAccess(),
            http=httpx.Client(timeout=30, follow_redirects=False),
            functional=arguments.functional,
            test_alert=arguments.test_alert,
        )
    except (OSError, ValueError):
        print(json.dumps({"result": "fail", "reason": "inputs_unreadable"}))
        return 2
    extra: tuple[Callable[[Context], Check], ...] = ()
    report = run_checks(context, extra)
    text = json.dumps(report, indent=2)
    print(text)
    if arguments.json:
        arguments.json.write_text(text, encoding="utf-8")
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
