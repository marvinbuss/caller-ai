"""Offline contracts for protected, exact-plan Terraform delivery."""

import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def delivery_case(monkeypatch, tmp_path):
    import shutil

    monkeypatch.syspath_prepend(str(SCRIPTS))
    import terraform_delivery

    source = terraform_delivery.ENVIRONMENTS / "int"
    folder = tmp_path / "dev"
    shutil.copytree(source, folder)
    (folder / "int.tfvars.json").rename(folder / "dev.tfvars.json")
    document = json.loads((folder / "dev.tfvars.json").read_text())
    document["environment"]["name"] = "dev"
    (folder / "dev.tfvars.json").write_text(json.dumps(document))
    for stack in ("state", "bootstrap", "app_environment"):
        path = folder / f"{stack}.azurerm.tfbackend.json"
        content = json.loads(path.read_text())
        content["key"] = f"dev/{stack}.tfstate"
        path.write_text(json.dumps(content))
    backend = json.loads((folder / "app_environment.azurerm.tfbackend.json").read_text())
    return terraform_delivery, tmp_path, backend, document["environment"]


def test_delivery_inputs_bind_environment_backend_and_plan_storage(delivery_case):
    module, directory, backend, variables = delivery_case
    inputs = module.load_inputs("dev", "app_environment", "tfplans", directory=directory)
    assert inputs.root == module.BASE / "app_environment"
    assert inputs.backend == backend
    assert inputs.variables["environment"] == variables
    assert inputs.plan_container == "tfplans"
    assert len(inputs.fingerprint) == 64


@pytest.mark.parametrize("environment,stack", [("../prod", "app_environment"), ("dev", "destroy")])
def test_delivery_rejects_untrusted_environment_or_root(delivery_case, environment, stack):
    module, directory, _backend, _variables = delivery_case
    with pytest.raises(ValueError, match="terraform_delivery_inputs_invalid"):
        module.load_inputs(environment, stack, "tfplans", directory=directory)


def test_delivery_rejects_backend_credentials_and_wrong_state_binding(delivery_case):
    module, directory, backend, _variables = delivery_case
    path = directory / "dev" / "app_environment.azurerm.tfbackend.json"
    backend["access_" + "key"] = "synthetic-not-a-credential"
    path.write_text(json.dumps(backend))
    with pytest.raises(ValueError, match="terraform_delivery_inputs_invalid"):
        module.load_inputs("dev", "app_environment", "tfplans", directory=directory)
    del backend["access_" + "key"]
    for malformed in (
        "../prod/app_environment.tfstate",
        "platform.txt",
        "a b/app_environment.tfstate",
        7,
    ):
        backend["key"] = malformed
        path.write_text(json.dumps(backend))
        with pytest.raises(ValueError, match="terraform_delivery_inputs_invalid"):
            module.load_inputs("dev", "app_environment", "tfplans", directory=directory)


@pytest.mark.parametrize("stage", ["dev", "tst", "prd"])
def test_any_environment_name_works_and_the_state_key_comes_from_the_backend_file(
    delivery_case, stage
):
    module, directory, backend, _variables = delivery_case
    if stage != "dev":
        (directory / "dev").rename(directory / stage)
        (directory / stage / "dev.tfvars.json").rename(directory / stage / f"{stage}.tfvars.json")
        document = json.loads((directory / stage / f"{stage}.tfvars.json").read_text())
        document["environment"]["name"] = stage
        (directory / stage / f"{stage}.tfvars.json").write_text(json.dumps(document))
    backend["key"] = f"{stage}/app_environment.tfstate"
    (directory / stage / "app_environment.azurerm.tfbackend.json").write_text(json.dumps(backend))
    for other in ("state", "bootstrap"):
        other_path = directory / stage / f"{other}.azurerm.tfbackend.json"
        content = json.loads(other_path.read_text())
        content["key"] = f"{stage}/{other}.tfstate"
        other_path.write_text(json.dumps(content))
    inputs = module.load_inputs(stage, "app_environment", "tfplans", directory=directory)
    assert inputs.environment == stage and inputs.backend["key"] == backend["key"]


def test_each_root_receives_only_the_environment_and_its_own_object(delivery_case):
    module, directory, _backend, _variables = delivery_case
    expected = {
        "state": {"environment", "state"},
        "bootstrap": {"environment", "bootstrap"},
        "app_environment": {"environment", "state", "app_environment"},
    }
    for stack, keys in expected.items():
        inputs = module.load_inputs("dev", stack, "tfplans", directory=directory)
        assert set(inputs.variables) == keys
    app = module.load_inputs("dev", "app_environment", "tfplans", directory=directory)
    assert set(app.variables["state"]) == {"resource_group_name", "storage_account_name"}


def test_invalid_environment_files_stop_the_delivery_before_any_command(delivery_case):
    module, directory, _backend, _variables = delivery_case
    path = directory / "dev" / "dev.tfvars.json"
    document = json.loads(path.read_text())
    del document["state"]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="terraform_delivery_inputs_invalid"):
        module.load_inputs("dev", "bootstrap", "tfplans", directory=directory)


def test_a_missing_backend_file_refuses_to_fall_back_to_local_state(delivery_case):
    module, directory, _backend, _variables = delivery_case
    (directory / "dev" / "bootstrap.azurerm.tfbackend.json").unlink()
    with pytest.raises(ValueError, match="terraform_delivery_inputs_invalid"):
        module.load_inputs("dev", "bootstrap", "tfplans", directory=directory)


def test_the_sign_in_secret_reaches_only_the_app_environment_root(delivery_case):
    module, _directory, _backend, _variables = delivery_case
    values = {
        "TF_VAR_management_signin_secret": "synthetic-value",  # pragma: allowlist secret
        "PATH": "x",
    }
    assert "TF_VAR_management_signin_secret" in module.delivery_environment(
        values, "app_environment"
    )
    for stack in ("state", "bootstrap", ""):
        assert "TF_VAR_management_signin_secret" not in module.delivery_environment(values, stack)


def test_saved_plan_is_bound_to_commit_run_inputs_and_bytes(delivery_case):
    import hashlib

    module, directory, _backend, _variables = delivery_case
    inputs = module.load_inputs("dev", "app_environment", "tfplans", directory=directory)
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
    inputs = module.load_inputs("dev", "app_environment", "tfplans", directory=directory)
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
    inputs = module.load_inputs("dev", "app_environment", "tfplans", directory=directory)
    values = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "marvinbuss/caller-ai",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "TF_DEPLOYMENT_ENVIRONMENT": "dev-terraform-app-environment-plan",
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
                    "publicNetworkAccess": "Enabled",
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
        elif command[0] == "terraform" and command[1] == "output":
            output = json.dumps({"environment": "dev"})
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
    values["TF_DEPLOYMENT_ENVIRONMENT"] = "dev-terraform-app-environment-apply"
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


def test_apply_writes_the_environment_manifest_for_the_environment_check(execution_case, tmp_path):
    module, inputs, values, commands, _blobs, execute = execution_case
    plan = module.deliver(
        inputs, "plan", values=values, runner=execute, protection_checker=lambda *args: None
    )
    target = tmp_path / "manifest.json"
    values["TF_DEPLOYMENT_ENVIRONMENT"] = "dev-terraform-app-environment-apply"
    values["ENVIRONMENT_MANIFEST_PATH"] = str(target)
    module.deliver(
        inputs,
        "apply",
        expected_digest=plan["plan_sha256"],
        values=values,
        runner=execute,
        protection_checker=lambda *args: None,
    )
    assert json.loads(target.read_text()) == {"environment": "dev"}
    assert any(command[:3] == ["terraform", "output", "-json"] for command in commands)


def test_delivery_rejects_tampered_blob_before_apply(execution_case):
    module, inputs, values, commands, blobs, execute = execution_case
    plan = module.deliver(
        inputs, "plan", values=values, runner=execute, protection_checker=lambda *args: None
    )
    for name in blobs:
        if name.endswith("plan.tfplan"):
            blobs[name] += b"changed"
    values["TF_DEPLOYMENT_ENVIRONMENT"] = "dev-terraform-app-environment-apply"
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
                command,
                0,
                stdout=json.dumps({"publicNetworkAccess": "Enabled", "allowSharedKeyAccess": True}),
                stderr="",
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
    inputs = module.load_inputs("dev", "app_environment", "tfplans", directory=directory)
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
        module.verify_environment_protection(
            inputs,
            "apply",
            {
                "GITHUB_TOKEN": "synthetic-token",
                "TF_DEPLOYMENT_ENVIRONMENT": "dev-terraform-app-environment-apply",
            },
        )
    else:
        with pytest.raises(ValueError, match="terraform_environment_unprotected"):
            module.verify_environment_protection(
                inputs,
                "apply",
                {
                    "GITHUB_TOKEN": "synthetic-token",
                    "TF_DEPLOYMENT_ENVIRONMENT": "dev-terraform-app-environment-apply",
                },
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
        ("terraform-state.yml", "state"),
        ("terraform-bootstrap.yml", "bootstrap"),
        ("terraform-app-environment.yml", "app_environment"),
    ):
        caller = yaml.load((directory / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert set(caller["on"]) == {"workflow_dispatch"}
        assert caller["jobs"]["deploy"]["uses"] == "./.github/workflows/terraform-deploy.yml"
        assert caller["jobs"]["deploy"]["with"]["root"] == stack
        assert caller["jobs"]["deploy"]["with"]["slug"] == stack.replace("_", "-")
        assert caller["jobs"]["deploy"]["with"]["slug"] == stack.replace("_", "-")
        assert "inherit" not in json.dumps(caller)


def _state_values(variables):
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "marvinbuss/caller-ai",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "TF_DEPLOYMENT_ENVIRONMENT": "dev-terraform-state-apply",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.githubusercontent.com/fixture",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-request-token",
        "GITHUB_TOKEN": "synthetic-github-token",
        **{
            "ARM_" + key.upper(): variables[key]
            for key in ("subscription_id", "tenant_id", "client_id")
        },
    }


@pytest.fixture
def state_case(delivery_case, monkeypatch):
    import subprocess

    module, directory, _backend, variables = delivery_case
    inputs = module.load_inputs("dev", "state", "tfplans", directory=directory)
    monkeypatch.setattr(module, "require_toolchain", lambda: Path("terraform"))
    log = {"commands": [], "blob": False, "migrate_failures": 0, "migrated": False}

    def execute(command, *, directory, values, timeout):
        log["commands"].append(command)
        output, code = "", 0
        if command[:4] == ["az", "storage", "blob", "exists"]:
            output = json.dumps({"exists": log["blob"]})
        elif command[1] == "plan":
            path = next(v.removeprefix("-out=") for v in command if v.startswith("-out="))
            Path(path).write_bytes(b"synthetic")
            code = 2
        elif command[1] == "show":
            output = json.dumps({"resource_changes": [{"change": {"actions": ["create"]}}]})
        elif command[1] == "init" and "-migrate-state" in command:
            if log["migrate_failures"] > 0:
                log["migrate_failures"] -= 1
                code = 1
            else:
                log["blob"] = True
                log["migrated"] = True
        return subprocess.CompletedProcess(command, code, stdout=output, stderr="")

    return module, inputs, _state_values(variables), log, execute


def test_state_create_plans_only_without_the_confirmation(state_case):
    module, inputs, values, log, execute = state_case
    report = module.state_create(inputs, values=values, runner=execute)
    assert report["result"] == "terraform_state_planned"
    assert not any(c[1] == "apply" for c in log["commands"] if c[0] == "terraform")


def test_state_create_applies_migrates_and_removes_local_state(state_case):
    module, inputs, values, log, execute = state_case
    (inputs.root / "terraform.tfstate").write_text("{}")
    try:
        report = module.state_create(
            inputs,
            confirm_apply=True,
            values=values,
            runner=execute,
            sleep=lambda seconds: None,
        )
        assert report["result"] == "terraform_state_created"
        assert log["migrated"] and not (inputs.root / "terraform.tfstate").exists()
        init = next(c for c in log["commands"] if "-migrate-state" in c)
        assert "-force-copy" in init
    finally:
        (inputs.root / "terraform.tfstate").unlink(missing_ok=True)


def test_state_create_retries_migration_for_role_propagation(state_case):
    module, inputs, values, log, execute = state_case
    log["migrate_failures"] = 2
    pauses = []
    report = module.state_create(
        inputs,
        confirm_apply=True,
        values=values,
        runner=execute,
        sleep=pauses.append,
    )
    assert report["result"] == "terraform_state_created" and len(pauses) == 2


def test_state_create_keeps_local_state_when_migration_fails(state_case):
    module, inputs, values, log, execute = state_case
    log["migrate_failures"] = 99
    (inputs.root / "terraform.tfstate").write_text("{}")
    try:
        with pytest.raises(ValueError, match="terraform_state_migration_failed"):
            module.state_create(
                inputs,
                confirm_apply=True,
                values=values,
                runner=execute,
                sleep=lambda seconds: None,
                attempts=3,
            )
        assert (inputs.root / "terraform.tfstate").exists()
    finally:
        (inputs.root / "terraform.tfstate").unlink(missing_ok=True)


def test_state_create_refuses_an_existing_state_blob_and_other_roots(state_case, delivery_case):
    module, inputs, values, log, execute = state_case
    log["blob"] = True
    with pytest.raises(ValueError, match="terraform_state_exists"):
        module.state_create(
            inputs,
            confirm_apply=True,
            values=values,
            runner=execute,
        )
    _module, directory, _backend, _variables = delivery_case
    other = module.load_inputs("dev", "bootstrap", "tfplans", directory=directory)
    with pytest.raises(ValueError, match="terraform_state_create_forbidden"):
        module.state_create(other, values=values, runner=execute)


def test_expect_no_changes_fails_when_the_plan_has_changes(execution_case):
    module, inputs, values, _commands, _blobs, execute = execution_case
    with pytest.raises(ValueError, match="terraform_unexpected_changes"):
        module.deliver(
            inputs,
            "plan",
            expect_no_changes=True,
            values=values,
            runner=execute,
            protection_checker=lambda *a: None,
        )


def test_destroy_is_refused_for_the_state_root_and_needs_a_saved_plan(
    execution_case, delivery_case
):
    module, inputs, values, commands, blobs, execute = execution_case
    _module, directory, _backend, _variables = delivery_case
    state = module.load_inputs("dev", "state", "tfplans", directory=directory)
    with pytest.raises(ValueError, match="terraform_destroy_forbidden"):
        module.deliver(state, "plan", destroy=True, values=values, runner=execute)
    assert commands == []
    assert (
        module.plan_changes(
            {"resource_changes": [{"change": {"actions": ["delete"]}}]}, allow_delete=True
        )
        == 1
    )
    module.deliver(
        inputs,
        "plan",
        destroy=True,
        values=values,
        runner=execute,
        protection_checker=lambda *a: None,
    )
    assert any("-destroy" in c for c in commands if c[:2] == ["terraform", "plan"])
    assert json.loads(blobs[next(n for n in blobs if n.endswith("manifest.json"))])["destroy"]


def test_the_apply_job_checks_the_environment_and_callers_offer_the_new_inputs():
    import yaml

    directory = SCRIPTS.parents[2] / ".github/workflows"
    reusable = yaml.load(
        (directory / "terraform-deploy.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    steps = reusable["jobs"]["apply"]["steps"]
    names = [step["name"] for step in steps]
    check = next(step for step in steps if step["name"] == "Check the deployed environment")
    assert names.index(check["name"]) > names.index(
        "Apply only the reviewed checksum-bound saved plan"
    )
    assert "check_environment.py" in check["run"] and "--manifest" in check["run"]
    assert "app_environment" in check["if"] and "destroy" in check["if"]
    inputs = reusable["on"]["workflow_call"]["inputs"]
    assert {"expect_no_changes", "destroy"} <= set(inputs)
    for name, destroy in (
        ("terraform-state.yml", False),
        ("terraform-bootstrap.yml", True),
        ("terraform-app-environment.yml", True),
    ):
        caller = yaml.load((directory / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        offered = set(caller["on"]["workflow_dispatch"]["inputs"])
        assert ("destroy" in offered) is destroy and "expect_no_changes" in offered


def test_state_create_runs_locally_with_the_cli_login_and_no_github_context(state_case):
    module, inputs, _values, log, _execute = state_case
    seen = []

    def execute(command, *, directory, values, timeout):
        seen.append((command, values))
        return _execute(command, directory=directory, values=values, timeout=timeout)

    module.state_create(inputs, confirm_apply=True, values={}, runner=execute, sleep=lambda s: None)
    assert all(env["ARM_USE_CLI"] == "true" and env["ARM_USE_OIDC"] == "false" for _, env in seen)
    migration = next(c for c, _ in seen if "-migrate-state" in c)
    assert "-backend-config=use_cli=true" in migration and "-reconfigure" not in migration
    assert log["migrated"]
