"""Checks the Terraform layout rules (FR-001, FR-002, FR-004, FR-044, FR-050).

Roots keep one file for variables, providers, outputs and main, and each module call lives in its
service's own file. Reusable modules follow the standard module structure. Nothing here runs
Terraform or reads a secret; it only reads the `.tf` files.
"""

import json
import re
import sys
from pathlib import Path

from ci import ROOT

BASE = ROOT / "code/infra/terraform"
ROOT_NAMES = ("state", "bootstrap", "app_environment")
ROOT_FILES = ("variables.tf", "providers.tf", "outputs.tf", "main.tf")
STRUCTURAL = set(ROOT_FILES)
MODULE_FILES = ("README.md", "main.tf", "variables.tf", "outputs.tf", "versions.tf")
NETWORK_HOMES = ("bootstrap", "modules/network")
NETWORK_TYPES = re.compile(
    r'^\s*resource\s+"azurerm_(virtual_network|subnet|network_security_group|route_table|'
    r"subnet_network_security_group_association|subnet_route_table_association|"
    r'virtual_network_peering)"',
    re.MULTILINE,
)
FORBIDDEN_TYPES = re.compile(
    r'^\s*resource\s+"azurerm_(network_security_rule|route|nat_gateway[a-z_]*|firewall[a-z_]*)"',
    re.MULTILINE,
)
AZUREAD = re.compile(
    r'\b(azuread_[a-z_]+|provider\s+"azuread"|hashicorp/azuread)\b|^\s*azuread\s*=\s*\{',
    re.MULTILINE,
)
INLINE_RULES = re.compile(r"^\s*(security_rule|route)\s*\{", re.MULTILINE)
RETIRED = ("modules/platform", "environments", "config", "tests")
MODULE_CALL = re.compile(r'^\s*module\s+"[^"]+"\s*\{', re.MULTILINE)
BLOCK = re.compile(r'^(variable|output)\s+"([^"]+)"\s*\{', re.MULTILINE)
PINNED = re.compile(r'^\s*(required_version|version)\s*=\s*"([^"]*)"', re.MULTILINE)
EXACT = re.compile(r"= ?[0-9]+\.[0-9]+\.[0-9]+")


def strip_comments(text: str) -> str:
    return re.sub(r"(?m)(#|//).*$", "", text)


def blocks(text: str) -> list[tuple[str, str, str]]:
    """Return `(kind, name, body)` for every top-level variable and output block."""
    found = []
    for match in BLOCK.finditer(text):
        depth, index = 1, match.end()
        while index < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            index += 1
        found.append((match.group(1), match.group(2), text[match.end() : index]))
    return found


def terraform_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.tf") if path.is_file())


def check_descriptions(directory: Path, findings: list[str], label: str) -> None:
    for path in terraform_files(directory):
        for kind, name, body in blocks(strip_comments(path.read_text(encoding="utf-8"))):
            if not re.search(r"^\s*description\s*=", body, re.MULTILINE):
                findings.append(f"{label}/{path.name}: {kind} {name} has no description")


def check_pins(directory: Path, findings: list[str], label: str) -> None:
    for path in terraform_files(directory):
        text = strip_comments(path.read_text(encoding="utf-8"))
        for key, value in PINNED.findall(text):
            if not EXACT.fullmatch(value):
                findings.append(f"{label}/{path.name}: {key} {value!r} is not pinned exactly")
    required = [p for p in terraform_files(directory) if "required_version" in p.read_text()]
    if not required:
        findings.append(f"{label}: no pinned required_version")


def check_common(directory: Path, relative: str, findings: list[str]) -> None:
    for path in terraform_files(directory):
        text = strip_comments(path.read_text(encoding="utf-8"))
        where = f"{relative}/{path.name}"
        if AZUREAD.search(text):
            findings.append(f"{where}: azuread is not used by Terraform (FR-050)")
        if FORBIDDEN_TYPES.search(text) or INLINE_RULES.search(text):
            findings.append(f"{where}: custom security rule, route, NAT gateway or firewall")
        if NETWORK_TYPES.search(text) and relative not in NETWORK_HOMES:
            findings.append(f"{where}: network resource outside bootstrap and the network module")
    check_descriptions(directory, findings, relative)
    check_pins(directory, findings, relative)


def check_root(base: Path, name: str, findings: list[str]) -> None:
    directory = base / name
    for required in ROOT_FILES:
        if not (directory / required).is_file():
            findings.append(f"{name}: missing {required}")
    for path in terraform_files(directory):
        if path.name in STRUCTURAL - {"main.tf"} and MODULE_CALL.search(
            strip_comments(path.read_text(encoding="utf-8"))
        ):
            findings.append(f"{name}/{path.name}: module call outside a service file")
        if path.name == "main.tf" and MODULE_CALL.search(
            strip_comments(path.read_text(encoding="utf-8"))
        ):
            findings.append(f"{name}/main.tf: module call outside its service file")
    check_common(directory, name, findings)


def check_module(base: Path, directory: Path, findings: list[str]) -> None:
    relative = directory.relative_to(base).as_posix()
    for required in MODULE_FILES:
        if not (directory / required).is_file():
            findings.append(f"{relative}: missing {required}")
    if not (directory / "tests").is_dir() or not list((directory / "tests").glob("*.tftest.hcl")):
        findings.append(f"{relative}: missing tests")
    for path in terraform_files(directory):
        if path.name != "main.tf" and MODULE_CALL.search(
            strip_comments(path.read_text(encoding="utf-8"))
        ):
            findings.append(f"{relative}/{path.name}: nested module call outside main.tf")
    check_common(directory, relative, findings)


def check(base: Path = BASE) -> tuple[list[str], int, int]:
    findings: list[str] = []
    for name in ROOT_NAMES:
        if not (base / name).is_dir():
            findings.append(f"{name}: missing root")
            continue
        check_root(base, name, findings)
    module_directories = sorted(
        path for path in (base / "modules").glob("*") if path.is_dir() and path.name != ".terraform"
    )
    for directory in module_directories:
        check_module(base, directory, findings)
    for retired in RETIRED:
        if (base / retired).exists():
            findings.append(f"{retired}: retired layout is still present")
    return findings, len(ROOT_NAMES), len(module_directories)


def main() -> int:
    findings, roots, modules = check()
    if findings:
        print(json.dumps({"result": "structure_invalid", "findings": findings}, indent=2))
        return 1
    print(json.dumps({"result": "structure_valid", "roots": roots, "modules": modules}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
