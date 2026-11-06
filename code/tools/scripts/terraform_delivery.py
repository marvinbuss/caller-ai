"""Protected Terraform delivery: plan, review and apply for any stage, using OIDC only.

The stage name and state key come from the backend and variable files, and the protected
deployment environment comes from the workflow input, so no stage list exists here. A plan is saved
privately and bound to the exact source, workflow run and inputs for one day; apply refuses a
tampered, expired or cross-run plan and any plan that deletes a resource. Output is fixed metadata
only and plan bytes are never published.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Never
from uuid import UUID

import httpx
from ci import ROOT
from validate_environment import validate as validate_environment_files
from verify_terraform import require_toolchain

BASE = ROOT / "code/infra/terraform"
ENVIRONMENTS = ROOT / "code/infra/environments"
ROOTS = {
    "state": BASE / "state",
    "bootstrap": BASE / "bootstrap",
    "app_environment": BASE / "app_environment",
}
SIGNIN_SECRET_VARIABLE = "TF_VAR_management_signin_secret"  # pragma: allowlist secret


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_delivery_property")
        result[key] = value
    return result


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("delivery_file_unavailable")
    with path.open("rb") as source:
        content = source.read(131073)
    if len(content) > 131072:
        raise ValueError("delivery_file_too_large")
    document = json.loads(content, object_pairs_hook=unique_object)
    if not isinstance(document, dict):
        raise ValueError("delivery_object_required")
    return document


@dataclass(frozen=True, repr=False)
class DeliveryInputs:
    environment: str
    stack: str
    root: Path
    backend_path: Path
    variable_path: Path
    backend: dict[str, Any]
    variables: dict[str, Any]
    plan_container: str

    @property
    def fingerprint(self) -> str:
        value = {
            "environment": self.environment,
            "stack": self.stack,
            "backend": self.backend,
            "variables": self.variables,
            "plan_container": self.plan_container,
        }
        sources = {self.backend_path, self.variable_path, self.root / ".terraform.lock.hcl"}
        sources.update(self.root.rglob("*.tf"))
        sources.update((BASE / "modules").rglob("*.tf"))
        value["sources"] = {
            str(path.relative_to(BASE)) if path.is_relative_to(BASE) else path.name: hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(sources)
            if ".terraform" not in path.parts and path.is_file()
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def valid_state_key(key: Any) -> bool:
    """The state key is plain data from the backend file; only its shape is checked."""
    return (
        isinstance(key, str)
        and re.fullmatch(r"[A-Za-z0-9._-]{1,64}(/[A-Za-z0-9._-]{1,64}){0,3}\.tfstate", key)
        is not None
        and ".." not in key
    )


def load_inputs(
    environment: str, stack: str, plan_container: str, *, directory: Path = ENVIRONMENTS
) -> DeliveryInputs:
    """Load reviewed nonsecret per-environment JSON; construction performs no cloud I/O.

    `directory` holds one folder per environment. The variable file is sliced so that a root only
    receives the `environment` object and its own object (the app environment root also receives the
    state coordinates it needs to read the bootstrap outputs).
    """
    try:
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,30}", environment) is None
            or stack not in ROOTS
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", plan_container) is None
            or "--" in plan_container
        ):
            raise ValueError("invalid_selector")
        if validate_environment_files(environment, base=directory).errors:
            raise ValueError("environment_files_invalid")
        folder = directory / environment
        backend_path = folder / f"{stack}.azurerm.tfbackend.json"
        variable_path = folder / f"{environment}.tfvars.json"
        backend, document = read_json(backend_path), read_json(variable_path)
        variables: dict[str, Any] = {"environment": document["environment"], stack: document[stack]}
        if stack == "app_environment":
            variables["state"] = {
                key: document["state"][key]
                for key in ("resource_group_name", "storage_account_name")
            }
        required = {
            "resource_group_name",
            "storage_account_name",
            "container_name",
            "key",
            "use_azuread_auth",
            "use_oidc",
            "use_cli",
        }
        if (
            not required <= backend.keys()
            or not backend.keys() <= required | {"subscription_id", "tenant_id", "client_id"}
            or backend["use_azuread_auth"] is not True
            or backend["use_oidc"] is not True
            or backend["use_cli"] is not False
            or not valid_state_key(backend["key"])
            or re.fullmatch(r"[a-z0-9]{3,24}", backend["storage_account_name"]) is None
            or re.fullmatch(r"[A-Za-z0-9._()-]{1,90}", backend["resource_group_name"]) is None
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", backend["container_name"]) is None
            or plan_container == backend["container_name"]
        ):
            raise ValueError("invalid_backend_binding")
        identity = variables["environment"]
        for key in ("subscription_id", "tenant_id", "client_id"):
            value = identity[key]
            if not isinstance(value, str) or str(UUID(value)).lower() != value.lower():
                raise ValueError("invalid_identity")
            if key in backend:
                backend_value = backend[key]
                if (
                    not isinstance(backend_value, str)
                    or str(UUID(backend_value)).lower() != backend_value.lower()
                    or (key != "subscription_id" and backend_value != value)
                ):
                    raise ValueError("backend_identity_mismatch")

        def contains_credentials(value: Any) -> bool:
            if isinstance(value, dict):
                forbidden = {
                    "password",
                    "client_secret",
                    "access_key",
                    "sas_token",
                    "connection_string",
                    "api_key",
                    "secret_value",
                }
                return any(
                    key.lower() in forbidden or contains_credentials(item)
                    for key, item in value.items()
                )
            return isinstance(value, list) and any(contains_credentials(item) for item in value)

        if contains_credentials(variables):
            raise ValueError("credentials_in_variables")
        return DeliveryInputs(
            environment,
            stack,
            ROOTS[stack],
            backend_path,
            variable_path,
            backend,
            variables,
            plan_container,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("terraform_delivery_inputs_invalid") from None


def require_context(
    inputs: DeliveryInputs, operation: str, values: dict[str, str] | None = None
) -> dict[str, str]:
    """Require trusted manual GitHub/main/OIDC context before any deployment I/O."""
    values = dict(os.environ) if values is None else values
    try:
        if (
            operation not in {"plan", "apply"}
            or values.get("GITHUB_ACTIONS") != "true"
            or values.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or values.get("GITHUB_REPOSITORY") != "marvinbuss/caller-ai"
            or values.get("GITHUB_REF") != "refs/heads/main"
            or values.get("TF_DEPLOYMENT_ENVIRONMENT")
            != f"{inputs.environment}-terraform-{inputs.stack.replace('_', '-')}-{operation}"
            or not values.get("ACTIONS_ID_TOKEN_REQUEST_URL")
            or not values.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
            or not values.get("GITHUB_TOKEN")
            or re.fullmatch(r"[a-f0-9]{40}", values.get("GITHUB_SHA", "")) is None
            or any(
                re.fullmatch(r"[1-9][0-9]{0,19}", values.get(key, "")) is None
                for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
            )
            or any(
                values.get("ARM_" + key.upper()) != inputs.variables["environment"][key]
                for key in ("subscription_id", "tenant_id", "client_id")
            )
        ):
            raise ValueError("untrusted_context")
        return {
            "source_sha": values["GITHUB_SHA"],
            "run_id": values["GITHUB_RUN_ID"],
            "run_attempt": values["GITHUB_RUN_ATTEMPT"],
        }
    except (ValueError, KeyError, TypeError):
        raise ValueError("terraform_delivery_context_invalid") from None


def plan_manifest(
    inputs: DeliveryInputs,
    binding: dict[str, str],
    digest: str,
    *,
    now: float | None = None,
    destroy: bool = False,
) -> dict[str, Any]:
    """Bind private plan bytes to exact source, workflow run, inputs and a one-day review window."""
    return {
        "schema_version": 1,
        "environment": inputs.environment,
        "stack": inputs.stack,
        "configuration_sha256": inputs.fingerprint,
        "plan_sha256": digest,
        "destroy": destroy,
        "created_at": time.time() if now is None else now,
        **binding,
    }


def verify_plan(
    inputs: DeliveryInputs,
    binding: dict[str, str],
    manifest: dict[str, Any],
    content: bytes,
    expected_digest: str,
    *,
    now: float | None = None,
) -> None:
    """Reject tampered, cross-run, changed-config or expired plans before apply."""
    try:
        current = time.time() if now is None else now
        created = manifest["created_at"]
        if (
            type(created) not in (int, float)
            or not 0 <= current - created <= 86400
            or re.fullmatch(r"[a-f0-9]{64}", expected_digest) is None
            or not 0 < len(content) <= 64 * 1024 * 1024
            or hashlib.sha256(content).hexdigest() != expected_digest
            or manifest
            != plan_manifest(
                inputs, binding, expected_digest, now=created, destroy=manifest["destroy"] is True
            )
        ):
            raise ValueError("invalid_plan")
    except (ValueError, TypeError, KeyError):
        raise ValueError("terraform_plan_binding_invalid") from None


def plan_changes(document: dict[str, Any], *, allow_delete: bool = False) -> int:
    """Count resource/output changes; deletion is allowed only for a reviewed destroy plan."""
    changes = document.get("resource_changes", [])
    if not isinstance(changes, list):
        raise ValueError("terraform_plan_invalid")
    count = 0
    for item in changes:
        actions = item.get("change", {}).get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("terraform_plan_invalid")
        if "delete" in actions and not allow_delete:
            raise ValueError("terraform_destructive_plan_blocked")
        if allow_delete and actions == ["delete"]:
            count += 1
            continue
        if actions not in (["no-op"], ["read"], ["create"], ["update"]):
            raise ValueError("terraform_plan_invalid")
        count += actions in (["create"], ["update"])
    outputs = document.get("output_changes", {})
    if not isinstance(outputs, dict):
        raise ValueError("terraform_plan_invalid")
    for output in outputs.values():
        actions = output.get("actions") if isinstance(output, dict) else None
        if actions not in (["no-op"], ["create"], ["update"], ["delete"]):
            raise ValueError("terraform_plan_invalid")
        count += actions != ["no-op"]
    return count


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_command(
    command: list[str], *, directory: Path, values: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    """Capture potentially sensitive tool output; callers emit only fixed metadata."""
    result = subprocess.run(
        command,
        cwd=directory,
        env=values,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if len(result.stdout) + len(result.stderr) > 64 * 1024 * 1024:
        raise ValueError("terraform_delivery_output_limit")
    return result


def verify_environment_protection(
    inputs: DeliveryInputs, operation: str, values: dict[str, str]
) -> None:
    """Require existing protected deployment environments; never create or weaken them."""
    # The protected environment name comes from the workflow input, not from a name format.
    name = values.get("TF_DEPLOYMENT_ENVIRONMENT", "")
    try:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,90}", name) is None:
            raise ValueError("invalid_protected_environment")
        with httpx.Client(
            timeout=30, verify=True, trust_env=False, follow_redirects=False
        ) as client:
            branch = client.get(
                "https://api.github.com/repos/marvinbuss/caller-ai/branches/main",
                headers={
                    "Authorization": "Bearer " + values["GITHUB_TOKEN"],
                    "Accept": "application/vnd.github+json",
                },
            )
            branch.raise_for_status()
            if len(branch.content) > 65536 or branch.json().get("protected") is not True:
                raise ValueError("unprotected_main_branch")
            response = client.get(
                "https://api.github.com/repos/marvinbuss/caller-ai/environments/" + name,
                headers={
                    "Authorization": "Bearer " + values["GITHUB_TOKEN"],
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            if len(response.content) > 65536:
                raise ValueError("environment_response_limit")
            document = response.json()
        if document.get("deployment_branch_policy", {}).get("protected_branches") is not True:
            raise ValueError("unprotected_branch_policy")
        if operation == "apply" and not any(
            rule.get("type") == "required_reviewers"
            and rule.get("reviewers")
            and rule.get("prevent_self_review") is True
            for rule in document.get("protection_rules", [])
        ):
            raise ValueError("review_required")
    except Exception:
        raise ValueError("terraform_environment_unprotected") from None


def delivery_environment(values: dict[str, str], stack: str = "") -> dict[str, str]:
    allowed_arm = {"ARM_CLIENT_ID", "ARM_TENANT_ID", "ARM_SUBSCRIPTION_ID"}
    # The sign-in secret reaches only the app environment root, as a sensitive environment variable.
    allowed_secret = {SIGNIN_SECRET_VARIABLE} if stack == "app_environment" else set()
    cleaned = {
        key: value
        for key, value in values.items()
        if not key.startswith(("TF_VAR_", "TF_CLI_ARGS", "TF_TOKEN_", "ARM_", "AZURE_STORAGE_"))
        or key in allowed_arm
        or key in allowed_secret
    }
    cleaned.update(
        ARM_USE_OIDC="true",
        ARM_USE_AZUREAD="true",
        ARM_USE_CLI="false",
        AZURE_STORAGE_AUTH_MODE="login",
        TF_INPUT="0",
        TF_IN_AUTOMATION="1",
        TF_WORKSPACE="default",
        CHECKPOINT_DISABLE="1",
    )
    return cleaned


def deliver(
    inputs: DeliveryInputs,
    operation: str,
    *,
    expected_digest: str = "",
    destroy: bool = False,
    expect_no_changes: bool = False,
    values: dict[str, str] | None = None,
    runner: Runner = run_command,
    protection_checker: Callable[..., None] = verify_environment_protection,
) -> dict[str, Any]:
    """Plan or apply an exact private artifact only from protected manual GitHub/main runs."""
    if destroy and inputs.stack not in {"bootstrap", "app_environment"}:
        raise ValueError("terraform_destroy_forbidden")
    if destroy and expect_no_changes:
        raise ValueError("terraform_delivery_arguments_invalid")
    original = dict(os.environ) if values is None else dict(values)
    binding = require_context(inputs, operation, original)
    protection_checker(inputs, operation, original)
    environment = delivery_environment(original, inputs.stack)
    fingerprint = inputs.fingerprint
    binary = str(require_toolchain())

    def command(arguments: list[str], *, timeout: int = 300, codes: tuple[int, ...] = (0,)) -> str:
        result = runner(arguments, directory=inputs.root, values=environment, timeout=timeout)
        if result.returncode not in codes:
            raise ValueError("terraform_delivery_command_failed")
        return result.stdout

    if command(["git", "rev-parse", "HEAD"]).strip() != binding["source_sha"]:
        raise ValueError("terraform_source_mismatch")
    if operation == "apply" and re.fullmatch(r"[a-f0-9]{64}", expected_digest) is None:
        raise ValueError("terraform_plan_binding_invalid")
    account = json.loads(
        command(
            [
                "az",
                "storage",
                "account",
                "show",
                "--name",
                inputs.backend["storage_account_name"],
                "--resource-group",
                inputs.backend["resource_group_name"],
                "--subscription",
                inputs.backend.get(
                    "subscription_id", inputs.variables["environment"]["subscription_id"]
                ),
                "--only-show-errors",
                "--output",
                "json",
            ]
        )
    )
    # The state storage keeps a public endpoint so GitHub-hosted runners can reach it. Access is by
    # identity only, with shared keys and anonymous access off (FR-031).
    if (
        account.get("publicNetworkAccess") not in {"Enabled", None}
        or account.get("allowSharedKeyAccess") is not False
        or account.get("allowBlobPublicAccess") is not False
        or account.get("enableHttpsTrafficOnly") is not True
        or account.get("minimumTlsVersion") not in {"TLS1_2", "TLS1_3"}
        or account.get("encryption", {}).get("services", {}).get("blob", {}).get("enabled")
        is not True
    ):
        raise ValueError("terraform_storage_policy_invalid")
    for container in (inputs.backend["container_name"], inputs.plan_container):
        policy = json.loads(
            command(
                [
                    "az",
                    "storage",
                    "container",
                    "show",
                    "--account-name",
                    inputs.backend["storage_account_name"],
                    "--name",
                    container,
                    "--auth-mode",
                    "login",
                    "--only-show-errors",
                    "--output",
                    "json",
                ]
            )
        )
        if "properties" not in policy or policy["properties"].get("publicAccess") not in {
            None,
            "None",
            "off",
        }:
            raise ValueError("terraform_storage_policy_invalid")
    prefix = (
        f"{inputs.environment}/{inputs.stack}/{binding['source_sha']}/"
        f"{binding['run_id']}-{binding['run_attempt']}"
    )
    storage = [
        "--account-name",
        inputs.backend["storage_account_name"],
        "--container-name",
        inputs.plan_container,
        "--auth-mode",
        "login",
        "--only-show-errors",
        "--output",
        "none",
    ]

    def blob(action: str, path: Path) -> None:
        arguments = [
            "az",
            "storage",
            "blob",
            action,
            *storage,
            "--name",
            prefix + "/" + path.name,
            "--file",
            str(path),
            "--no-progress",
            "--timeout",
            "60",
        ]
        if action == "upload":
            arguments.extend(["--overwrite", "false", "--type", "block", "--if-none-match", "*"])
        command(arguments, timeout=120)

    previous_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="caller-tf-delivery-") as temporary:
            directory = Path(temporary)
            variable_file = directory / "variables.tfvars.json"
            variable_file.write_text(json.dumps(inputs.variables), encoding="utf-8")
            plan, manifest_path, review = (
                directory / name for name in ("plan.tfplan", "manifest.json", "review.txt")
            )
            initialization = [
                binary,
                "init",
                "-input=false",
                "-lockfile=readonly",
                "-reconfigure",
                "-no-color",
            ]
            initialization.extend(
                "-backend-config="
                + key
                + "="
                + (str(value).lower() if type(value) is bool else str(value))
                for key, value in sorted(inputs.backend.items())
            )
            if operation == "plan":
                command(initialization)
                command([binary, "validate", "-no-color"])
                command(
                    [
                        binary,
                        "plan",
                        "-input=false",
                        "-no-color",
                        "-lock-timeout=300s",
                        "-detailed-exitcode",
                        "-var-file=" + str(variable_file),
                        *(["-destroy"] if destroy else []),
                        "-out=" + str(plan),
                    ],
                    timeout=2700,
                    codes=(0, 2),
                )
                if not plan.is_file() or not 0 < plan.stat().st_size <= 64 * 1024 * 1024:
                    raise ValueError("terraform_plan_invalid")
                changes = plan_changes(
                    json.loads(command([binary, "show", "-json", str(plan)])),
                    allow_delete=destroy,
                )
                if expect_no_changes and changes:
                    raise ValueError("terraform_unexpected_changes")
                review.write_text(
                    command([binary, "show", "-no-color", str(plan)]), encoding="utf-8"
                )
                digest = hashlib.sha256(plan.read_bytes()).hexdigest()
                if inputs.fingerprint != fingerprint:
                    raise ValueError("terraform_configuration_changed")
                manifest_path.write_text(
                    json.dumps(
                        plan_manifest(inputs, binding, digest, destroy=destroy), sort_keys=True
                    ),
                    encoding="utf-8",
                )
                for artifact in (plan, review, manifest_path):
                    artifact.chmod(0o600)
                    blob("upload", artifact)
                return {
                    "result": "terraform_plan_stored",
                    "changes": changes,
                    "plan_sha256": digest,
                    "configuration_sha256": fingerprint,
                }
            for artifact in (plan, manifest_path):
                blob("download", artifact)
                artifact.chmod(0o600)
            if not plan.is_file() or plan.stat().st_size > 64 * 1024 * 1024:
                raise ValueError("terraform_plan_invalid")
            manifest = read_json(manifest_path)
            verify_plan(inputs, binding, manifest, plan.read_bytes(), expected_digest)
            command(initialization)
            plan_changes(
                json.loads(command([binary, "show", "-json", str(plan)])),
                allow_delete=manifest["destroy"] is True,
            )
            if inputs.fingerprint != fingerprint:
                raise ValueError("terraform_configuration_changed")
            command(
                [binary, "apply", "-input=false", "-no-color", "-lock-timeout=300s", str(plan)],
                timeout=5400,
            )
            target = original.get("ENVIRONMENT_MANIFEST_PATH", "")
            if target and inputs.stack == "app_environment" and manifest["destroy"] is not True:
                # The manifest names resources and settings only; it holds no secret value.
                description = command([binary, "output", "-json", "environment_manifest"])
                json.loads(description)
                Path(target).write_text(description, encoding="utf-8")
            return {
                "result": "terraform_plan_applied",
                "plan_sha256": expected_digest,
                "configuration_sha256": fingerprint,
            }
    finally:
        os.umask(previous_umask)


def state_create(
    inputs: DeliveryInputs,
    *,
    confirm_apply: bool = False,
    values: dict[str, str] | None = None,
    runner: Runner = run_command,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 6,
) -> dict[str, Any]:
    """Create the state storage with local state, then migrate the state into it.

    Run locally by an engineer who is signed in with the Azure CLI. This is the only operation that
    may use local state (FR-006, FR-007). Without `confirm_apply` it only plans. The local state is
    removed only after the remote blob exists.
    """
    if inputs.stack != "state":
        raise ValueError("terraform_state_create_forbidden")
    original = dict(os.environ) if values is None else dict(values)
    environment = delivery_environment(original, inputs.stack)
    environment.update(ARM_USE_OIDC="false", ARM_USE_CLI="true")
    local_backend = {**inputs.backend, "use_oidc": False, "use_cli": True}
    binary = str(require_toolchain())
    storage = [
        "--account-name",
        inputs.backend["storage_account_name"],
        "--container-name",
        inputs.backend["container_name"],
        "--name",
        inputs.backend["key"],
        "--auth-mode",
        "login",
        "--only-show-errors",
        "--output",
        "json",
    ]

    def blob_exists() -> bool:
        result = runner(
            ["az", "storage", "blob", "exists", *storage],
            directory=inputs.root,
            values=environment,
            timeout=120,
        )
        return result.returncode == 0 and json.loads(result.stdout or "{}").get("exists") is True

    def command(arguments: list[str], *, timeout: int = 600, codes: tuple[int, ...] = (0,)) -> str:
        result = runner(arguments, directory=inputs.root, values=environment, timeout=timeout)
        if result.returncode not in codes:
            raise ValueError("terraform_delivery_command_failed")
        return result.stdout

    if blob_exists():
        raise ValueError("terraform_state_exists")
    previous_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="caller-tf-state-") as temporary:
            directory = Path(temporary)
            variable_file = directory / "variables.tfvars.json"
            variable_file.write_text(json.dumps(inputs.variables), encoding="utf-8")
            plan = directory / "state.tfplan"
            command([binary, "init", "-backend=false", "-input=false", "-no-color"])
            command(
                [
                    binary,
                    "plan",
                    "-input=false",
                    "-no-color",
                    "-detailed-exitcode",
                    "-var-file=" + str(variable_file),
                    "-out=" + str(plan),
                ],
                timeout=1800,
                codes=(0, 2),
            )
            changes = plan_changes(json.loads(command([binary, "show", "-json", str(plan)])))
            if not confirm_apply:
                return {"result": "terraform_state_planned", "changes": changes}
            command([binary, "apply", "-input=false", "-no-color", str(plan)], timeout=3600)
            migration = [
                binary,
                "init",
                "-migrate-state",
                "-force-copy",
                "-input=false",
                "-no-color",
                *(
                    "-backend-config="
                    + key
                    + "="
                    + (str(value).lower() if type(value) is bool else str(value))
                    for key, value in sorted(local_backend.items())
                ),
            ]
            for attempt in range(attempts):
                result = runner(migration, directory=inputs.root, values=environment, timeout=600)
                if result.returncode == 0:
                    break
                if attempt + 1 == attempts:
                    raise ValueError("terraform_state_migration_failed")
                sleep(30)  # role assignments need time to propagate (research R-08)
            if not blob_exists():
                raise ValueError("terraform_state_migration_failed")
            for leftover in ("terraform.tfstate", "terraform.tfstate.backup"):
                (inputs.root / leftover).unlink(missing_ok=True)
            return {"result": "terraform_state_created", "changes": changes}
    finally:
        os.umask(previous_umask)


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("terraform_delivery_arguments_invalid")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = SafeParser(add_help=False)
        parser.add_argument(
            "--operation",
            choices=("check", "guard", "plan", "apply", "state-create"),
            default="check",
        )
        parser.add_argument("--environment", required=True)
        parser.add_argument("--root", choices=tuple(ROOTS), required=True)
        parser.add_argument("--plan-container", required=True)
        parser.add_argument("--expected-plan-sha256", default="")
        parser.add_argument("--expect-no-changes", action="store_true")
        parser.add_argument("--destroy", action="store_true")
        parser.add_argument("--confirm-apply", action="store_true")
        arguments = parser.parse_args(argv)
        inputs = load_inputs(arguments.environment, arguments.root, arguments.plan_container)
        if arguments.operation == "check":
            report = {
                "result": "terraform_delivery_inputs_valid",
                "configuration_sha256": inputs.fingerprint,
            }
        elif arguments.operation == "guard":
            operation = os.environ.get("TF_DELIVERY_OPERATION", "")
            require_context(inputs, operation)
            verify_environment_protection(inputs, operation, dict(os.environ))
            report = {"result": "terraform_delivery_environment_protected"}
        elif arguments.operation == "state-create":
            report = state_create(inputs, confirm_apply=arguments.confirm_apply)
        else:
            report = deliver(
                inputs,
                arguments.operation,
                expected_digest=arguments.expected_plan_sha256,
                destroy=arguments.destroy,
                expect_no_changes=arguments.expect_no_changes,
            )
        print(json.dumps(report, sort_keys=True))
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a", encoding="utf-8") as target:
                for key in ("plan_sha256", "configuration_sha256", "changes"):
                    if key in report:
                        target.write(f"{key}={report[key]}\n")
        return 0
    except Exception:
        print(json.dumps({"result": "terraform_delivery_failed", "raw_output_suppressed": True}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
