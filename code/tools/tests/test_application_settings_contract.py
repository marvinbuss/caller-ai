"""The setting names in the contract, the Terraform deployment and both applications must match."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "specs/004-int-environment-setup/contracts/application-settings.md"
TERRAFORM = ROOT / "code/infra/terraform/app_environment/container_apps.tf"
INTERNAL = {
    "instance_id",
    "trace_destination",
    "trace_include_content",
    "agents_sdk_tracing_enabled",
    "realtime_tracing_enabled",
}


def contract_names(section: str) -> set[str]:
    text = CONTRACT.read_text(encoding="utf-8")
    body = re.search(rf"^## {section}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert body is not None
    names: set[str] = set()
    for line in body.group(1).splitlines():
        if line.startswith("| `"):
            cell = line.split("|")[1]
            names.update(re.findall(r"`([A-Z][A-Z0-9_]+)`", cell))
    return names


def terraform_names(module: str) -> set[str]:
    text = TERRAFORM.read_text(encoding="utf-8")
    block = re.search(rf'module "{module}" \{{(.*?)\n\}}\n', text, re.DOTALL)
    assert block is not None
    names: set[str] = set()
    for key in ("settings", "secret_references"):
        part = re.search(rf"{key}\s*=\s*\{{(.*?)\n  \}}", block.group(1), re.DOTALL)
        if part:
            names.update(re.findall(r"^\s+([A-Z][A-Z0-9_]+)\s*=", part.group(1), re.MULTILINE))
    return names


def application_names(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    body = text.split("class Settings(BaseSettings):", 1)[1].split("    @model_validator", 1)[0]
    fields = re.findall(r"^    ([a-z_]+): ", body, re.MULTILINE)
    names = {"CALLER_" + field.upper() for field in fields if field not in INTERNAL}
    names.discard("CALLER_AZURE_CLIENT_ID")
    names.add("AZURE_CLIENT_ID")
    return names


def test_runtime_names_match_in_the_contract_terraform_and_application() -> None:
    expected = contract_names("Runtime")
    assert expected, "the contract table was not found"
    assert terraform_names("runtime_app") == expected
    assert application_names(ROOT / "code/backend/src/caller_runtime/settings.py") == expected


def test_management_names_match_in_the_contract_terraform_and_application() -> None:
    expected = contract_names("Management")
    assert expected, "the contract table was not found"
    assert terraform_names("management_app") == expected
    assert application_names(ROOT / "code/management/management_app/settings.py") == expected


def test_the_two_secret_settings_are_only_key_vault_references_in_terraform() -> None:
    text = TERRAFORM.read_text(encoding="utf-8")
    block = re.search(r"secret_references\s*=\s*\{(.*?)\n  \}", text, re.DOTALL)
    assert block is not None
    assert set(re.findall(r"^\s+([A-Z][A-Z0-9_]+)\s*=", block.group(1), re.MULTILINE)) == {
        "CALLER_ENTRA_CLIENT_SECRET",
        "CALLER_SIGNING_KEY_PEM",
    }
    contract = CONTRACT.read_text(encoding="utf-8")
    secrets = re.findall(r"^\| `(CALLER_[A-Z_]+)` \| \*\*yes\*\*", contract, re.MULTILINE)
    assert set(secrets) == {"CALLER_ENTRA_CLIENT_SECRET", "CALLER_SIGNING_KEY_PEM"}
