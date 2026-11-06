"""Validates the per-environment configuration files before any resource is touched.

Each directory under `code/infra/environments/` holds one variable file and three backend files.
Findings are printed as one JSON line each on standard error, naming the file, the setting and a
stable problem code. A finding never contains the offending value. Open source ranges only warn.
"""

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENTS = ROOT / "code/infra/environments"
ENVIRONMENT_NAMES = ("int", "dev", "tst", "prd")
DEPLOYMENTS = ("state", "bootstrap", "app_environment")
TOP_LEVEL = ("environment", "state", "bootstrap", "app_environment")
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
STORAGE_NAME = re.compile(r"[a-z0-9]{3,24}")
APP_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,30}[a-z0-9]")
KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
TAG = re.compile(r"sha-[0-9a-f]{40}|v[0-9]+\.[0-9]+\.[0-9]+")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SECRET_KEY = re.compile(r"secret|password|key|token|connection_string|sas", re.IGNORECASE)
SECRET_KEY_ALLOWED = {
    "secret_store",
    "signing_key",
    "key_id",
    "soft_delete_days",
    "max_input_tokens",
    "max_output_tokens",
}
SECRET_VALUE = re.compile(
    r"-----BEGIN|AccountKey=|SharedAccessSignature|eyJ[A-Za-z0-9_-]{10,}\.|[A-Za-z0-9+/=]{40,}"
)
LOCATION = re.compile(r"[a-z0-9]{3,30}")
FORBIDDEN_BACKEND = ("access_key", "sas_token", "client_secret")
BACKEND_KEYS = {
    "resource_group_name",
    "storage_account_name",
    "container_name",
    "key",
    "use_azuread_auth",
    "use_oidc",
    "use_cli",
}


class Finding(Exception):
    """Carries one validation finding without the offending value."""


class Report:
    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []

    def error(self, file: str, setting: str, problem: str) -> None:
        self.errors.append({"file": file, "setting": setting, "problem": problem})

    def warning(self, file: str, setting: str, problem: str) -> None:
        self.warnings.append({"warning": problem, "file": file, "setting": setting})


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def load(path: Path, report: Report) -> dict[str, Any] | None:
    name = relative(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except OSError:
        report.error(name, "$", "missing")
        return None
    except ValueError as error:
        problem = "duplicate_key" if "duplicate_key" in str(error) else "invalid_format"
        report.error(name, "$", problem)
        return None
    if not isinstance(value, dict):
        report.error(name, "$", "invalid_format")
        return None
    return value


def get(
    data: dict[str, Any], path: str, report: Report, file: str, *, kinds: tuple[type, ...] = (str,)
) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            report.error(file, path, "missing")
            return None
        current = current[part]
    if isinstance(current, bool) and bool not in kinds:
        report.error(file, path, "invalid_format")
        return None
    if not isinstance(current, kinds):
        report.error(file, path, "invalid_format")
        return None
    return current


def network(value: Any) -> ipaddress.IPv4Network | None:
    try:
        parsed = ipaddress.ip_network(value, strict=True)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, ipaddress.IPv4Network) else None


def scan_secrets(value: Any, path: str, report: Report, file: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if SECRET_KEY.search(key) and key not in SECRET_KEY_ALLOWED:
                report.error(file, child_path, "secret_like_key")
            scan_secrets(child, child_path, report, file)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_secrets(child, f"{path}[{index}]", report, file)
    elif isinstance(value, str) and ".image." not in f".{path}." and SECRET_VALUE.search(value):
        if not (GUID.fullmatch(value) or value.startswith("/subscriptions/")):
            report.error(file, path, "secret_like_value")


def check_environment(data: dict[str, Any], name: str, report: Report, file: str) -> None:
    env_name = get(data, "environment.name", report, file)
    if env_name is not None and (env_name not in ENVIRONMENT_NAMES or env_name != name):
        report.error(file, "environment.name", "invalid_format")
    for field in ("tenant_id", "subscription_id", "client_id"):
        value = get(data, f"environment.{field}", report, file)
        if value is not None and not GUID.fullmatch(value):
            report.error(file, f"environment.{field}", "invalid_format")
    location = get(data, "environment.location", report, file)
    if location is not None and not LOCATION.fullmatch(location):
        report.error(file, "environment.location", "invalid_format")
    tags = get(data, "environment.tags", report, file, kinds=(dict,))
    if tags is not None and "owner" not in tags:
        report.error(file, "environment.tags.owner", "missing")
    access = data.get("environment", {}).get("public_access", {})
    if not isinstance(access, dict):
        report.error(file, "environment.public_access", "invalid_format")
        return
    enabled = access.get("enabled", False)
    if not isinstance(enabled, bool):
        report.error(file, "environment.public_access.enabled", "invalid_format")
        return
    if not enabled:
        return
    cidrs = access.get("allowed_cidrs")
    setting = "environment.public_access.allowed_cidrs"
    if not isinstance(cidrs, list) or not cidrs:
        report.error(file, setting, "required_when_enabled")
        return
    for entry in cidrs:
        try:
            parsed = ipaddress.ip_network(entry, strict=True)
        except (ValueError, TypeError):
            report.error(file, setting, "invalid_cidr")
            continue
        if parsed.prefixlen == 0:
            report.warning(file, setting, "open_range")


def check_state(data: dict[str, Any], report: Report, file: str) -> None:
    get(data, "state.resource_group_name", report, file)
    account = get(data, "state.storage_account_name", report, file)
    if account is not None and not STORAGE_NAME.fullmatch(account):
        report.error(file, "state.storage_account_name", "invalid_format")
    days = get(data, "state.state_retention_days", report, file, kinds=(int,))
    if days is not None and not 7 <= days <= 365:
        report.error(file, "state.state_retention_days", "invalid_format")
    writers = get(data, "state.writer_principal_ids", report, file, kinds=(list,))
    if writers is not None and (
        not writers or any(not isinstance(w, str) or not GUID.fullmatch(w) for w in writers)
    ):
        report.error(file, "state.writer_principal_ids", "invalid_format")


def check_bootstrap(data: dict[str, Any], report: Report, file: str) -> None:
    get(data, "bootstrap.resource_group_name", report, file)
    get(data, "bootstrap.vnet_name", report, file)
    space = network(get(data, "bootstrap.address_space", report, file))
    apps_value = get(data, "bootstrap.container_apps_subnet_cidr", report, file)
    endpoint_value = get(data, "bootstrap.private_endpoint_subnet_cidr", report, file)
    apps, endpoints = network(apps_value), network(endpoint_value)
    for setting, parsed, value in (
        ("bootstrap.address_space", space, True),
        ("bootstrap.container_apps_subnet_cidr", apps, apps_value is not None),
        ("bootstrap.private_endpoint_subnet_cidr", endpoints, endpoint_value is not None),
    ):
        if value and parsed is None:
            report.error(file, setting, "invalid_cidr")
    if apps is not None and apps.prefixlen > 27:
        report.error(file, "bootstrap.container_apps_subnet_cidr", "invalid_format")
    if space is not None:
        for setting, parsed in (
            ("bootstrap.container_apps_subnet_cidr", apps),
            ("bootstrap.private_endpoint_subnet_cidr", endpoints),
        ):
            if parsed is not None and not parsed.subnet_of(space):
                report.error(file, setting, "invalid_format")
    if apps is not None and endpoints is not None and apps.overlaps(endpoints):
        report.error(file, "bootstrap.private_endpoint_subnet_cidr", "invalid_format")


def check_image(data: dict[str, Any], prefix: str, report: Report, file: str) -> None:
    get(data, f"{prefix}.image.repository", report, file)
    node = data
    for part in f"{prefix}.image".split("."):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    tag, digest = node.get("tag") if node else None, node.get("digest") if node else None
    setting = f"{prefix}.image"
    if (tag is None) == (digest is None):
        report.error(file, setting, "invalid_format")
    elif tag is not None and not (isinstance(tag, str) and TAG.fullmatch(tag)):
        report.error(file, f"{setting}.tag", "invalid_format")
    elif digest is not None and not (isinstance(digest, str) and DIGEST.fullmatch(digest)):
        report.error(file, f"{setting}.digest", "invalid_format")


def check_app_environment(data: dict[str, Any], report: Report, file: str) -> None:
    get(data, "app_environment.resource_group_name", report, file)
    get(data, "app_environment.container_apps.environment_name", report, file)
    for application in ("runtime", "management"):
        prefix = f"app_environment.container_apps.{application}"
        name = get(data, f"{prefix}.app_name", report, file)
        if name is not None and not APP_NAME.fullmatch(name):
            report.error(file, f"{prefix}.app_name", "invalid_format")
        check_image(data, prefix, report, file)
        get(data, f"{prefix}.cpu", report, file, kinds=(int, float))
        get(data, f"{prefix}.memory", report, file)
    get(data, "app_environment.identity.resource_group_name", report, file)
    for field in ("management_app_client_id", "runtime_app_client_id", "user_group_id"):
        value = get(data, f"app_environment.identity.{field}", report, file)
        if value is not None and not GUID.fullmatch(value):
            report.error(file, f"app_environment.identity.{field}", "invalid_format")
    management = get(data, "app_environment.identity.management_app_client_id", report, file)
    runtime = get(data, "app_environment.identity.runtime_app_client_id", report, file)
    if management is not None and management == runtime:
        report.error(file, "app_environment.identity.runtime_app_client_id", "must_differ")
    get(data, "app_environment.identity.runtime_identity_name", report, file)
    for role in ("editor", "tester", "runtime", "management"):
        value = get(data, f"app_environment.identity.roles.{role}", report, file)
        if value is not None and not value.strip():
            report.error(file, f"app_environment.identity.roles.{role}", "missing")
    get(data, "app_environment.model.account_id", report, file)
    text = get(data, "app_environment.model.text_deployment", report, file)
    live = get(data, "app_environment.model.live_deployment", report, file)
    if text is not None and live is not None and (not text or not live or text == live):
        report.error(file, "app_environment.model.live_deployment", "invalid_format")
    get(data, "app_environment.data_store.name", report, file)
    get(data, "app_environment.secret_store.name", report, file)
    key_id = get(data, "app_environment.signing_key.key_id", report, file)
    if key_id is not None and not KEY_ID.fullmatch(key_id):
        report.error(file, "app_environment.signing_key.key_id", "invalid_format")
    get(data, "app_environment.monitoring.workspace_name", report, file)


def check_variable_file(directory: Path, name: str, report: Report) -> dict[str, Any] | None:
    path = directory / f"{name}.tfvars.json"
    file = relative(path)
    data = load(path, report)
    if data is None:
        return None
    for key in data:
        if key not in TOP_LEVEL:
            report.error(file, key, "unknown_key")
    for key in TOP_LEVEL:
        if key not in data:
            report.error(file, key, "missing")
    if not all(key in data for key in TOP_LEVEL):
        return data
    scan_secrets(data, "", report, file)
    check_environment(data, name, report, file)
    check_state(data, report, file)
    check_bootstrap(data, report, file)
    check_app_environment(data, report, file)
    return data


def check_backend(
    directory: Path, name: str, deployment: str, variables: dict[str, Any] | None, report: Report
) -> dict[str, Any] | None:
    path = directory / f"{deployment}.azurerm.tfbackend.json"
    file = relative(path)
    data = load(path, report)
    if data is None:
        return None
    for key in data:
        if key in FORBIDDEN_BACKEND:
            report.error(file, key, "secret_like_key")
        elif key not in BACKEND_KEYS:
            report.error(file, key, "unknown_key")
    state = (
        (variables or {}).get("state", {})
        if isinstance((variables or {}).get("state"), dict)
        else {}
    )
    for field in ("resource_group_name", "storage_account_name"):
        value = data.get(field)
        if value is None:
            report.error(file, field, "missing")
        elif variables is not None and value != state.get(field):
            report.error(file, field, "invalid_format")
    if data.get("container_name") != "tfstate":
        report.error(file, "container_name", "invalid_format")
    if data.get("key") != f"{name}/{deployment}.tfstate":
        report.error(file, "key", "invalid_format")
    for flag, expected in (("use_azuread_auth", True), ("use_oidc", True), ("use_cli", False)):
        if data.get(flag) is not expected:
            report.error(file, flag, "invalid_format")
    return data


def check_directory(directory: Path, report: Report) -> dict[str, Any]:
    name = directory.name
    expected = {f"{name}.tfvars.json"} | {f"{d}.azurerm.tfbackend.json" for d in DEPLOYMENTS}
    present = {entry.name for entry in directory.iterdir()} if directory.is_dir() else set()
    for missing in sorted(expected - present):
        report.error(relative(directory / missing), "$", "missing")
    for extra in sorted(present - expected):
        report.error(relative(directory / extra), "$", "unknown_key")
    if name not in ENVIRONMENT_NAMES:
        report.error(relative(directory), "environment.name", "invalid_format")
    variables = check_variable_file(directory, name, report)
    keys: dict[str, str] = {}
    for deployment in DEPLOYMENTS:
        backend = check_backend(directory, name, deployment, variables, report)
        if backend is not None and isinstance(backend.get("key"), str):
            keys[deployment] = backend["key"]
    return {"variables": variables, "keys": keys}


def global_values(variables: dict[str, Any] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if not variables:
        return values
    sources = {
        "environment.subscription_id": ("environment", "subscription_id"),
        "state.storage_account_name": ("state", "storage_account_name"),
        "app_environment.data_store.name": ("app_environment", "data_store", "name"),
        "app_environment.secret_store.name": ("app_environment", "secret_store", "name"),
    }
    for setting, path in sources.items():
        node: Any = variables
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str):
            values[setting] = node.lower()
    return values


def check_uniqueness(
    results: dict[str, dict[str, Any]], selected: list[str], report: Report
) -> None:
    seen: dict[tuple[str, str], str] = {}
    for env in sorted(results):
        entries = global_values(results[env]["variables"])
        entries.update({f"state_key.{k}": v for k, v in results[env]["keys"].items()})
        for setting, value in entries.items():
            marker = (setting.split(".")[0] if setting.startswith("state_key") else setting, value)
            owner = seen.get(marker)
            if owner is not None and owner != env and env in selected:
                file = relative(ENVIRONMENTS / env / f"{env}.tfvars.json")
                report.error(file, setting, "duplicate_across_environments")
            seen.setdefault(marker, env)


def validate(selected: str | None = None, base: Path | None = None) -> Report:
    base = base or ENVIRONMENTS
    report = Report()
    directories = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    if selected is not None:
        directories = [p for p in directories if p.name == selected] or [base / selected]
    results: dict[str, dict[str, Any]] = {}
    for directory in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
        scratch = Report()
        target = report if selected is None or directory.name == selected else scratch
        results[directory.name] = check_directory(directory, target)
    for directory in directories:
        if directory.name not in results:
            check_directory(directory, report)
    check_uniqueness(results, [d.name for d in directories], report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=ENVIRONMENT_NAMES)
    arguments = parser.parse_args()
    report = validate(arguments.environment)
    for finding in report.warnings:
        print(json.dumps(finding), file=sys.stderr)
    for finding in report.errors:
        print(json.dumps(finding), file=sys.stderr)
    if report.errors:
        return 1
    names = sorted(p.name for p in ENVIRONMENTS.iterdir() if p.is_dir())
    if arguments.environment:
        names = [arguments.environment]
    print(json.dumps({"result": "environment_valid", "environments": names}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
