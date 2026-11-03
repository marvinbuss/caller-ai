"""Offline contracts for protected, exact-plan Terraform delivery."""

import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def delivery_case(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import terraform_delivery

    backend = {
        "resource_group_name": "rg-state-fixture",
        "storage_account_name": "statefixture123",
        "container_name": "tfstate",
        "key": "dev/platform.tfstate",
        "use_azuread_auth": True,
        "use_oidc": True,
        "use_cli": False,
    }
    variables = {
        "environment": "dev",
        "subscription_id": "00000000-0000-4000-8000-000000000001",
        "tenant_id": "00000000-0000-4000-8000-000000000002",
        "client_id": "00000000-0000-4000-8000-000000000003",
    }
    (tmp_path / "dev.platform.azurerm.tfbackend.json").write_text(json.dumps(backend))
    (tmp_path / "dev.platform.tfvars.json").write_text(json.dumps(variables))
    return terraform_delivery, tmp_path, backend, variables


def test_delivery_inputs_bind_environment_backend_and_plan_storage(delivery_case):
    module, directory, backend, variables = delivery_case
    inputs = module.load_inputs("dev", "platform", "tfplans", directory=directory)
    assert inputs.root == module.BASE / "environments"
    assert inputs.backend == backend
    assert inputs.variables == variables
    assert inputs.plan_container == "tfplans"
    assert len(inputs.fingerprint) == 64


@pytest.mark.parametrize("environment,stack", [("../prod", "platform"), ("dev", "destroy")])
def test_delivery_rejects_untrusted_environment_or_root(delivery_case, environment, stack):
    module, directory, _backend, _variables = delivery_case
    with pytest.raises(ValueError, match="terraform_delivery_inputs_invalid"):
        module.load_inputs(environment, stack, "tfplans", directory=directory)


def test_delivery_rejects_backend_credentials_and_wrong_state_binding(delivery_case):
    module, directory, backend, _variables = delivery_case
    path = directory / "dev.platform.azurerm.tfbackend.json"
    backend["access_" + "key"] = "synthetic-not-a-credential"
    path.write_text(json.dumps(backend))
    with pytest.raises(ValueError, match="terraform_delivery_inputs_invalid"):
        module.load_inputs("dev", "platform", "tfplans", directory=directory)
    del backend["access_" + "key"]
    backend["key"] = "prod/platform.tfstate"
    path.write_text(json.dumps(backend))
    with pytest.raises(ValueError, match="terraform_delivery_inputs_invalid"):
        module.load_inputs("dev", "platform", "tfplans", directory=directory)


def test_backend_subscription_can_differ_from_workload_without_identity_fallback(delivery_case):
    module, directory, backend, _variables = delivery_case
    backend["subscription_id"] = "00000000-0000-4000-8000-000000000099"
    (directory / "dev.platform.azurerm.tfbackend.json").write_text(json.dumps(backend))
    inputs = module.load_inputs("dev", "platform", "tfplans", directory=directory)
    assert inputs.backend["subscription_id"] != inputs.variables["subscription_id"]
    backend["tenant_id"] = "00000000-0000-4000-8000-000000000099"
    (directory / "dev.platform.azurerm.tfbackend.json").write_text(json.dumps(backend))
    with pytest.raises(ValueError, match="terraform_delivery_inputs_invalid"):
        module.load_inputs("dev", "platform", "tfplans", directory=directory)


def test_saved_plan_is_bound_to_commit_run_inputs_and_bytes(delivery_case):
    import hashlib

    module, directory, _backend, _variables = delivery_case
    inputs = module.load_inputs("dev", "platform", "tfplans", directory=directory)
    content = b"synthetic-private-plan"
    digest = hashlib.sha256(content).hexdigest()
    binding = {"source_sha": "a" * 40, "run_id": "123", "run_attempt": "1"}
    manifest = module.plan_manifest(inputs, binding, digest, now=1000)
    module.verify_plan(inputs, binding, manifest, content, digest, now=1001)
    with pytest.raises(ValueError, match="terraform_plan_binding_invalid"):
        module.verify_plan(inputs, binding, manifest, content + b"changed", digest, now=1001)
    with pytest.raises(ValueError, match="terraform_plan_binding_invalid"):
        module.verify_plan(
            inputs, {**binding, "source_sha": "b" * 40}, manifest, content, digest, now=1001
        )
    with pytest.raises(ValueError, match="terraform_plan_binding_invalid"):
        module.verify_plan(inputs, binding, manifest, content, digest, now=1000 + 86401)


def test_delivery_refuses_local_pr_and_missing_oidc_context(delivery_case):
    module, directory, _backend, _variables = delivery_case
    inputs = module.load_inputs("dev", "platform", "tfplans", directory=directory)
    for values in ({}, {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request"}):
        with pytest.raises(ValueError, match="terraform_delivery_context_invalid"):
            module.require_context(inputs, "apply", values)


def test_delivery_blocks_destructive_plans_without_weakening_checks(delivery_case):
    module, _directory, _backend, _variables = delivery_case
    assert module.plan_changes({"resource_changes": [{"change": {"actions": ["create"]}}]}) == 1
    assert module.plan_changes({"resource_changes": [{"change": {"actions": ["no-op"]}}]}) == 0
    with pytest.raises(ValueError, match="terraform_destructive_plan_blocked"):
        module.plan_changes({"resource_changes": [{"change": {"actions": ["delete", "create"]}}]})


@pytest.fixture
def execution_case(delivery_case, monkeypatch):
    import subprocess

    module, directory, _backend, variables = delivery_case
    inputs = module.load_inputs("dev", "platform", "tfplans", directory=directory)
    values = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "marvinbuss/caller-ai",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "TF_DEPLOYMENT_ENVIRONMENT": "dev-terraform-platform-plan",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.githubusercontent.com/fixture",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-request-token",
        "GITHUB_TOKEN": "synthetic-github-token",
        **{
            "ARM_" + key.upper(): variables[key]
            for key in ("subscription_id", "tenant_id", "client_id")
        },
    }
    commands, blobs = [], {}
    monkeypatch.setattr(module, "require_toolchain", lambda: Path("terraform"))

    def execute(command, *, directory, values, timeout):
        commands.append(command)
        output = ""
        code = 0
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            output = "a" * 40
        elif command[:4] == ["az", "storage", "account", "show"]:
            output = json.dumps(
                {
                    "publicNetworkAccess": "Disabled",
                    "allowSharedKeyAccess": False,
                    "allowBlobPublicAccess": False,
                    "enableHttpsTrafficOnly": True,
                    "minimumTlsVersion": "TLS1_2",
                    "encryption": {"services": {"blob": {"enabled": True}}},
                }
            )
        elif command[:4] == ["az", "storage", "container", "show"]:
            output = json.dumps({"properties": {"publicAccess": None}})
        elif command[0] == "terraform" and command[1] == "plan":
            path = next(
                value.removeprefix("-out=") for value in command if value.startswith("-out=")
            )
            Path(path).write_bytes(b"synthetic-private-plan")
            code = 2
        elif command[0] == "terraform" and command[1] == "show":
            output = (
                json.dumps({"resource_changes": [{"change": {"actions": ["create"]}}]})
                if "-json" in command
                else "synthetic-private-review"
            )
        elif command[:4] == ["az", "storage", "blob", "upload"]:
            assert command[command.index("--auth-mode") + 1] == "login"
            assert command[command.index("--overwrite") + 1] == "false"
            name = command[command.index("--name") + 1]
            assert name not in blobs
            blobs[name] = Path(command[command.index("--file") + 1]).read_bytes()
        elif command[:4] == ["az", "storage", "blob", "download"]:
            name = command[command.index("--name") + 1]
            Path(command[command.index("--file") + 1]).write_bytes(blobs[name])
        return subprocess.CompletedProcess(command, code, stdout=output, stderr="")

    return module, inputs, values, commands, blobs, execute


def test_delivery_applies_only_the_saved_checksum_bound_plan(execution_case):
    module, inputs, values, commands, blobs, execute = execution_case
    plan = module.deliver(
        inputs, "plan", values=values, runner=execute, protection_checker=lambda *args: None
    )
    assert plan["changes"] == 1 and len(plan["plan_sha256"]) == 64
    values["TF_DEPLOYMENT_ENVIRONMENT"] = "dev-terraform-platform-apply"
    result = module.deliver(
        inputs,
        "apply",
        expected_digest=plan["plan_sha256"],
        values=values,
        runner=execute,
        protection_checker=lambda *args: None,
    )
    assert result["result"] == "terraform_plan_applied"
    apply = next(command for command in commands if command[:2] == ["terraform", "apply"])
    assert apply[-1].endswith("plan.tfplan")
    assert not any(value.startswith("-var") for value in apply)
    assert len(blobs) == 3


def test_delivery_rejects_tampered_blob_before_apply(execution_case):
    module, inputs, values, commands, blobs, execute = execution_case
    plan = module.deliver(
        inputs, "plan", values=values, runner=execute, protection_checker=lambda *args: None
    )
    for name in blobs:
        if name.endswith("plan.tfplan"):
            blobs[name] += b"changed"
    values["TF_DEPLOYMENT_ENVIRONMENT"] = "dev-terraform-platform-apply"
    with pytest.raises(ValueError, match="terraform_plan_binding_invalid"):
        module.deliver(
            inputs,
            "apply",
            expected_digest=plan["plan_sha256"],
            values=values,
            runner=execute,
            protection_checker=lambda *args: None,
        )
    assert not any(command[:2] == ["terraform", "apply"] for command in commands)


def test_delivery_requires_environment_protection_before_any_command(execution_case):
    module, inputs, values, commands, _blobs, execute = execution_case

    def denied(*arguments):
        raise ValueError("terraform_environment_unprotected")

    with pytest.raises(ValueError, match="terraform_environment_unprotected"):
        module.deliver(inputs, "plan", values=values, runner=execute, protection_checker=denied)
    assert commands == []


def test_delivery_rejects_public_or_shared_key_plan_storage(execution_case):
    import subprocess

    module, inputs, values, commands, _blobs, execute = execution_case

    def public_storage(command, **keywords):
        if command[:4] == ["az", "storage", "account", "show"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"publicNetworkAccess": "Enabled"}), stderr=""
            )
        return execute(command, **keywords)

    with pytest.raises(ValueError, match="terraform_storage_policy_invalid"):
        module.deliver(
            inputs,
            "plan",
            values=values,
            runner=public_storage,
            protection_checker=lambda *args: None,
        )
    assert not any(command[:2] == ["terraform", "init"] for command in commands)


def test_delivery_environment_cannot_inherit_keys_or_terraform_argument_injection(delivery_case):
    module, _directory, _backend, _variables = delivery_case
    synthetic_value = "-".join(("synthetic", "untrusted", "value"))
    values = module.delivery_environment(
        {
            "ARM_CLIENT_SECRET": synthetic_value,
            "ARM_ACCESS_KEY": synthetic_value,
            "AZURE_STORAGE_KEY": synthetic_value,
            "TF_VAR_environment": "prod",
            "TF_CLI_ARGS_apply": "-lock=false",
            "ARM_CLIENT_ID": "kept",
        }
    )
    assert values["ARM_CLIENT_ID"] == "kept"
    assert not any(
        key in values
        for key in (
            "ARM_CLIENT_SECRET",
            "ARM_ACCESS_KEY",
            "AZURE_STORAGE_KEY",
            "TF_VAR_environment",
            "TF_CLI_ARGS_apply",
        )
    )
    assert values["ARM_USE_OIDC"] == values["ARM_USE_AZUREAD"] == "true"
    assert values["ARM_USE_CLI"] == "false" and values["TF_WORKSPACE"] == "default"


@pytest.mark.parametrize("protected", [True, False])
def test_delivery_verifies_actual_main_branch_protection(delivery_case, monkeypatch, protected):
    import httpx

    module, directory, _backend, _variables = delivery_case
    inputs = module.load_inputs("dev", "platform", "tfplans", directory=directory)
    original = httpx.Client

    def respond(request):
        if request.url.path.endswith("/branches/main"):
            return httpx.Response(200, json={"protected": protected})
        return httpx.Response(
            200,
            json={
                "deployment_branch_policy": {"protected_branches": True},
                "protection_rules": [
                    {
                        "type": "required_reviewers",
                        "reviewers": [{"type": "Team"}],
                        "prevent_self_review": True,
                    }
                ],
            },
        )

    monkeypatch.setattr(
        module.httpx, "Client", lambda **keywords: original(transport=httpx.MockTransport(respond))
    )
    if protected:
        module.verify_environment_protection(inputs, "apply", {"GITHUB_TOKEN": "synthetic-token"})
    else:
        with pytest.raises(ValueError, match="terraform_environment_unprotected"):
            module.verify_environment_protection(
                inputs, "apply", {"GITHUB_TOKEN": "synthetic-token"}
            )


def test_output_only_plan_changes_are_not_skipped(delivery_case):
    module, _directory, _backend, _variables = delivery_case
    assert (
        module.plan_changes(
            {
                "resource_changes": [],
                "output_changes": {"environment": {"actions": ["update"]}},
            }
        )
        == 1
    )


def test_reusable_workflows_keep_plan_apply_and_bootstrap_protected():
    import re

    import yaml

    directory = SCRIPTS.parents[2] / ".github/workflows"
    reusable_path = directory / "terraform-deploy.yml"
    source = reusable_path.read_text(encoding="utf-8")
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_call"}
    assert workflow["on"]["workflow_call"]["inputs"]["apply_changes"]["default"] == "false"
    assert set(workflow["jobs"]) == {"plan", "apply"}
    assert "needs.plan.outputs.plan_sha256" in source
    assert "--expected-plan-sha256" in source
    assert "refs/heads/main" in source and "workflow_dispatch" in source
    assert "upload-artifact" not in source and "download-artifact" not in source
    assert "terraform apply" not in source and "-auto-approve" not in source
    for operation in ("plan", "apply"):
        job = workflow["jobs"][operation]
        assert job["environment"].endswith("-" + operation)
        assert job["permissions"]["contents"] == "read"
        assert job["permissions"]["id-token"] == "write"
        assert any("--operation guard" in step.get("run", "") for step in job["steps"])
        assert all(
            "@" not in step.get("uses", "") or re.search(r"@[a-f0-9]{40}$", step["uses"])
            for step in job["steps"]
        )
    for name, stack in (
        ("terraform-bootstrap.yml", "bootstrap"),
        ("terraform-infrastructure.yml", "platform"),
    ):
        caller = yaml.load((directory / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert set(caller["on"]) == {"workflow_dispatch"}
        assert caller["jobs"]["deploy"]["uses"] == "./.github/workflows/terraform-deploy.yml"
        assert caller["jobs"]["deploy"]["with"]["root"] == stack
