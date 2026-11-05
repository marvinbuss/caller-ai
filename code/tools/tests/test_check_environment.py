"""Contracts for the environment check, using fake Azure and Graph responses."""

import copy
import json
from pathlib import Path

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SUB = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-app"
RUNTIME_ID = f"{SUB}/providers/Microsoft.App/containerApps/ca-runtime"
MANAGEMENT_ID = f"{SUB}/providers/Microsoft.App/containerApps/ca-management"
ENV_ID = f"{SUB}/providers/Microsoft.App/managedEnvironments/cae"
COSMOS_ID = f"{SUB}/providers/Microsoft.DocumentDB/databaseAccounts/cosmos"
VAULT_ID = f"{SUB}/providers/Microsoft.KeyVault/vaults/kv"
WORKSPACE_ID = f"{SUB}/providers/Microsoft.OperationalInsights/workspaces/log"
ACTION_ID = f"{SUB}/providers/microsoft.insights/actionGroups/ag"
MODEL_ID = f"{SUB}/providers/Microsoft.CognitiveServices/accounts/model"
ENDPOINT_IDS = [f"{SUB}/providers/Microsoft.Network/privateEndpoints/pe-{n}" for n in "abc"]
NSG_ID = f"{SUB}/providers/Microsoft.Network/networkSecurityGroups/nsg"
ROUTE_ID = f"{SUB}/providers/Microsoft.Network/routeTables/rt"
RUNTIME_PRINCIPAL = "00000000-0000-4000-8000-0000000000aa"
STATE_ACCOUNT_ID = (
    "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-state"
    "/providers/Microsoft.Storage/storageAccounts/statestore"
)
CLIENT_ID = "00000000-0000-4000-8000-000000000005"
SENTINEL = "synthetic-secret-value-never-printed"


def manifest(public=False, cidrs=None):
    return {
        "environment": "int",
        "resource_group_name": "rg-app",
        "container_apps": {
            "runtime": {
                "name": "ca-runtime",
                "host": "runtime.example.test",
                "image": "sha-" + "a" * 40,
                "id": RUNTIME_ID,
            },
            "management": {
                "name": "ca-management",
                "host": "management.example.test",
                "image": "sha-" + "a" * 40,
                "id": MANAGEMENT_ID,
            },
        },
        "public_access": {"enabled": public, "allowed_cidrs": cidrs or []},
        "services": {
            "data_store_id": COSMOS_ID,
            "secret_store_id": VAULT_ID,
            "workspace_id": WORKSPACE_ID,
            "action_group_id": ACTION_ID,
            "model_account_id": MODEL_ID,
            "container_environment": ENV_ID,
        },
        "private_endpoints": ENDPOINT_IDS,
        "identity": {
            "tenant_id": "t",
            "management_app_client_id": CLIENT_ID,
            "runtime_identity_name": "id-runtime",
            "runtime_principal_id": RUNTIME_PRINCIPAL,
            "roles": {"editor": "Editor", "tester": "Tester", "runtime": "Runtime"},
        },
        "redirect_uri": "https://management.example.test/auth/callback",
        "vault_secret_names": ["management-signin-secret", "permit-private-key"],
        "network": {"network_security_group_id": NSG_ID, "route_table_id": ROUTE_ID},
    }


def app(name, image, settings, secrets=("CALLER_ENTRA_CLIENT_SECRET", "CALLER_SIGNING_KEY_PEM")):
    env = [
        {"name": item, "secretRef": "s"} if item in secrets else {"name": item, "value": "x"}
        for item in settings
    ]
    return {
        "properties": {
            "provisioningState": "Succeeded",
            "latestRevisionName": f"{name}--1",
            "template": {"containers": [{"image": f"ghcr.io/x/{name}:{image}", "env": env}]},
            "configuration": {
                "ingress": {"external": False, "fqdn": "management.example.test"},
                "secrets": [{"name": "s", "keyVaultUrl": "https://kv.test/secrets/x"}],
            },
        }
    }


class Fake:
    def __init__(self, world, graph):
        self.world, self.graph_world, self.posts = world, graph, []

    def arm(self, path, api_version):
        if path not in self.world:
            raise LookupError("missing")
        return copy.deepcopy(self.world[path])

    def arm_post(self, path, api_version, body):
        self.posts.append(path)
        return 202

    def graph(self, path):
        for prefix, value in self.graph_world.items():
            if path.startswith(prefix):
                return copy.deepcopy(value)
        raise LookupError("missing")


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import check_environment

    return check_environment


def good_world(module):
    image = "sha-" + "a" * 40
    world = {
        ENV_ID: {
            "properties": {"provisioningState": "Succeeded", "publicNetworkAccess": "Disabled"}
        },
        COSMOS_ID: {
            "properties": {"provisioningState": "Succeeded", "publicNetworkAccess": "Disabled"}
        },
        VAULT_ID: {
            "properties": {"provisioningState": "Succeeded", "publicNetworkAccess": "Disabled"}
        },
        WORKSPACE_ID: {"properties": {"provisioningState": "Succeeded"}},
        ACTION_ID: {"properties": {}},
        MODEL_ID: {"properties": {"provisioningState": "Succeeded"}},
        RUNTIME_ID: app("runtime", image, module.RUNTIME_SETTINGS),
        MANAGEMENT_ID: app("management", image, module.MANAGEMENT_SETTINGS),
        f"{RUNTIME_ID}/revisions/runtime--1": {
            "properties": {"healthState": "Healthy", "runningState": "Running"}
        },
        f"{MANAGEMENT_ID}/revisions/management--1": {
            "properties": {"healthState": "Healthy", "runningState": "Running"}
        },
        NSG_ID: {"properties": {"securityRules": []}},
        ROUTE_ID: {"properties": {"routes": []}},
        SUB: {},
        f"{SUB}/resources": {"value": [{"type": "Microsoft.Network/virtualNetworks"}]},
        f"{MODEL_ID}/deployments": {"value": [{"name": "text"}, {"name": "live"}]},
        f"{MODEL_ID}/providers/Microsoft.Authorization/roleAssignments": {
            "value": [{"properties": {"principalId": RUNTIME_PRINCIPAL}}]
        },
        f"{VAULT_ID}/secrets/management-signin-secret": {
            "properties": {"attributes": {"enabled": True}}
        },
        f"{VAULT_ID}/secrets/permit-private-key": {"properties": {"attributes": {"enabled": True}}},
        STATE_ACCOUNT_ID: {"properties": {"allowSharedKeyAccess": False}},
    }
    for endpoint in ENDPOINT_IDS:
        world[endpoint] = {
            "properties": {
                "privateLinkServiceConnections": [
                    {"properties": {"privateLinkServiceConnectionState": {"status": "Approved"}}}
                ]
            }
        }
    graph = {
        "/servicePrincipals?": {
            "value": [
                {
                    "id": "sp-id",
                    "appRoles": [
                        {"id": "r1", "value": "Editor"},
                        {"id": "r2", "value": "Tester"},
                        {"id": "r3", "value": "Runtime"},
                    ],
                }
            ]
        },
        "/servicePrincipals/sp-id/appRoleAssignedTo": {
            "value": [{"principalId": RUNTIME_PRINCIPAL, "appRoleId": "r3"}]
        },
        "/applications?": {
            "value": [{"web": {"redirectUris": ["https://management.example.test/auth/callback"]}}]
        },
    }
    return world, graph


@pytest.fixture
def case(module):
    world, graph = good_world(module)
    files = {
        "environment": {"subscription_id": "00000000-0000-4000-8000-000000000001"},
        "state": {"resource_group_name": "rg-state", "storage_account_name": "statestore"},
    }

    def build(**keywords):
        keywords.setdefault("manifest", manifest())
        return module.Context(
            environment="int",
            files=files,
            access=Fake(world, graph),
            sleep=lambda seconds: None,
            **keywords,
        )

    return module, world, graph, build


def status(report, check_id):
    return next(item for item in report["checks"] if item["id"] == check_id)["status"]


def test_the_result_has_the_documented_format_and_exits_zero_only_when_nothing_failed(case):
    module, _world, _graph, build = case
    report = module.run_checks(build())
    assert set(report) == {"environment", "result", "checks"}
    assert all(set(item) == {"id", "service", "status", "detail"} for item in report["checks"])
    assert report["environment"] == "int"
    failing = [item for item in report["checks"] if item["status"] == "fail"]
    assert failing == [] or report["result"] == "fail"
    assert report["result"] == "pass", failing


def test_a_failed_check_fails_the_run(case):
    module, world, _graph, build = case
    del world[NSG_ID]
    report = module.run_checks(build())
    assert report["result"] == "fail"


def test_a_skipped_check_fails_the_run_unless_it_is_the_allowed_skip(case):
    module, _world, _graph, build = case

    def skipped(reason):
        return lambda context: module.Check("x", "y", "skipped", reason)

    assert module.run_checks(build(), (skipped("other"),))["result"] == "fail"
    assert module.run_checks(build(), (skipped("public_access_disabled"),))["result"] == "pass"


def test_no_output_contains_a_secret_or_an_input_value(case):
    module, world, _graph, build = case
    world[f"{VAULT_ID}/secrets/management-signin-secret"]["properties"]["value"] = SENTINEL
    world[RUNTIME_ID]["properties"]["template"]["containers"][0]["env"].append(
        {"name": "OTHER", "value": SENTINEL}
    )
    text = json.dumps(module.run_checks(build()))
    assert SENTINEL not in text and "keyVaultUrl" not in text


def test_services_provisioned_reports_missing_and_public_stores(case):
    module, world, _graph, build = case
    world[VAULT_ID]["properties"]["publicNetworkAccess"] = "Enabled"
    del world[WORKSPACE_ID]
    detail = module.services_provisioned(build()).detail
    assert "secret_store: public access" in detail and "workspace: missing" in detail


def test_settings_complete_names_missing_settings_and_plain_secrets(case):
    module, world, _graph, build = case
    env = world[RUNTIME_ID]["properties"]["template"]["containers"][0]["env"]
    world[RUNTIME_ID]["properties"]["template"]["containers"][0]["env"] = env[:-1]
    management = world[MANAGEMENT_ID]["properties"]["template"]["containers"][0]["env"]
    for item in management:
        if item["name"] == "CALLER_SIGNING_KEY_PEM":
            item.pop("secretRef")
            item["value"] = SENTINEL
    result = module.settings_complete(build())
    assert result.status == "fail" and "CALLER_MAX_CLOCK_SKEW_SECONDS" in result.detail
    assert "CALLER_SIGNING_KEY_PEM" in result.detail and SENTINEL not in result.detail


def test_revisions_must_be_healthy_within_five_minutes(case):
    module, world, _graph, build = case
    world[f"{RUNTIME_ID}/revisions/runtime--1"]["properties"] = {
        "healthState": "Unhealthy",
        "runningStateDetails": "start_reason_settings_missing",
    }
    clock = iter(range(0, 10000, 100))
    context = build(clock=lambda: next(clock))
    result = module.revisions_healthy(context)
    assert result.status == "fail" and "start_reason_settings_missing" in result.detail
    world[f"{RUNTIME_ID}/revisions/runtime--1"]["properties"] = {
        "healthState": "Healthy",
        "runningState": "Running",
    }
    assert module.revisions_healthy(build()).status == "pass"


def test_images_running_names_the_application_and_version_on_a_failed_pull(case):
    module, world, _graph, build = case
    world[MANAGEMENT_ID]["properties"]["template"]["containers"][0]["image"] = "ghcr.io/x/m:sha-old"
    world[f"{MANAGEMENT_ID}/revisions/management--1"]["properties"] = {
        "healthState": "Unhealthy",
        "runningStateDetails": "ImagePullBackOff",
    }
    result = module.images_running(build())
    assert result.status == "fail" and "ca-management" in result.detail
    assert "sha-" + "a" * 40 in result.detail and "ImagePullBackOff" in result.detail


def test_private_endpoints_report_pending_connections(case):
    module, world, _graph, build = case
    world[ENDPOINT_IDS[2]]["properties"]["privateLinkServiceConnections"][0]["properties"][
        "privateLinkServiceConnectionState"
    ]["status"] = "Pending"
    result = module.private_endpoints_approved(build())
    assert result.status == "fail" and "pe-c: Pending" in result.detail


def test_no_custom_outbound_rejects_rules_routes_nat_and_firewall(case):
    module, world, _graph, build = case
    assert module.no_custom_outbound(build()).status == "pass"
    world[NSG_ID]["properties"]["securityRules"] = [{"name": "custom"}]
    world[ROUTE_ID]["properties"]["routes"] = [{"name": "custom"}]
    world[f"{SUB}/resources"]["value"] = [
        {"type": "Microsoft.Network/natGateways"},
        {"type": "Microsoft.Network/azureFirewalls"},
    ]
    detail = module.no_custom_outbound(build()).detail
    assert "security rules" in detail and "routes" in detail
    assert "natgateways" in detail and "azurefirewalls" in detail


def test_model_access_needs_both_deployments_and_the_runtime_role(case):
    module, world, _graph, build = case
    assert module.model_access(build()).status == "pass"
    world[f"{MODEL_ID}/providers/Microsoft.Authorization/roleAssignments"]["value"] = []
    assert "holds no role" in module.model_access(build()).detail
    world[f"{MODEL_ID}/deployments"]["value"] = [{"name": "text"}]
    assert "fewer than two" in module.model_access(build()).detail


def test_registration_roles_and_the_runtime_assignment_are_read_through_graph(case):
    module, _world, graph, build = case
    assert module.registration_and_roles_exist(build()).status == "pass"
    assert module.runtime_role_assigned(build()).status == "pass"
    graph["/servicePrincipals?"]["value"][0]["appRoles"].pop()
    result = module.registration_and_roles_exist(build())
    assert result.status == "fail" and "identity.roles.runtime" in result.detail
    graph["/servicePrincipals?"]["value"] = []
    result = module.registration_and_roles_exist(build())
    assert "identity.management_app_client_id" in result.detail


def test_secrets_present_checks_names_and_expiry_without_values(case):
    module, world, _graph, build = case
    assert module.secrets_present(build()).status == "pass"
    del world[f"{VAULT_ID}/secrets/permit-private-key"]
    world[f"{VAULT_ID}/secrets/management-signin-secret"]["properties"]["attributes"]["exp"] = 1
    detail = module.secrets_present(build()).detail
    assert "permit-private-key: missing" in detail and "management-signin-secret: expired" in detail


def test_the_sign_in_redirect_fails_until_it_is_registered(case):
    module, _world, graph, build = case
    assert module.signin_redirect_matches(build()).status == "pass"
    graph["/applications?"]["value"][0]["web"]["redirectUris"] = []
    assert module.signin_redirect_matches(build()).status == "fail"


def test_state_isolation_needs_distinct_keys_and_disabled_shared_keys(case):
    module, world, _graph, build = case
    assert module.state_isolated(build()).status == "pass"
    key = next(path for path in world if path.endswith("statestore"))
    world[key]["properties"]["allowSharedKeyAccess"] = True
    assert module.state_isolated(build()).status == "fail"


def test_alert_test_runs_only_when_requested(case):
    module, _world, _graph, build = case
    context = build()
    assert module.alert_test(context).status == "skipped"
    requested = build(test_alert=True)
    assert module.alert_test(requested).status == "pass"
    assert requested.access.posts == [f"{ACTION_ID}/createNotifications"]


def test_public_access_must_match_the_flag_and_a_closed_range_refuses_the_runner(case):
    module, world, _graph, build = case
    assert module.public_access_matches(build()).status == "pass"
    context = build(manifest=manifest(public=True, cidrs=["203.0.113.0/24"]))
    result = module.public_access_matches(context)
    assert result.status == "fail" and "does not match" in result.detail
    for item in (ENV_ID,):
        world[item]["properties"]["publicNetworkAccess"] = "Enabled"
    for item in (RUNTIME_ID, MANAGEMENT_ID):
        world[item]["properties"]["configuration"]["ingress"] = {
            "external": True,
            "ipSecurityRestrictions": [{"ipAddressRange": "203.0.113.0/24"}],
            "fqdn": "management.example.test",
        }
    refused = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
    served = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    ranges = manifest(public=True, cidrs=["203.0.113.0/24"])
    assert module.public_access_matches(build(manifest=ranges, http=refused)).status == "pass"
    assert module.public_access_matches(build(manifest=ranges, http=served)).status == "fail"


def test_an_open_range_reports_that_no_source_restriction_applies(case):
    module, world, _graph, build = case
    world[ENV_ID]["properties"]["publicNetworkAccess"] = "Enabled"
    for item in (RUNTIME_ID, MANAGEMENT_ID):
        world[item]["properties"]["configuration"]["ingress"] = {
            "external": True,
            "ipSecurityRestrictions": [{"ipAddressRange": "0.0.0.0/0"}],
        }
    result = module.public_access_matches(
        build(manifest=manifest(public=True, cidrs=["0.0.0.0/0"]))
    )
    assert result.status == "pass" and "no source restriction" in result.detail
