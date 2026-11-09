"""Contracts for the Terraform structure check, using good and bad fixture trees."""

import shutil
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

VARIABLE = 'variable "name" {\n  description = "A name."\n  type        = string\n}\n'
OUTPUT = 'output "id" {\n  description = "An id."\n  value       = "x"\n}\n'
TERRAFORM = (
    'terraform {\n  required_version = "= 1.17.0"\n  required_providers {\n'
    '    azurerm = {\n      source  = "hashicorp/azurerm"\n'
    '      version = "= 5.11.0"\n    }\n  }\n}\n'
)


@pytest.fixture
def structure(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import check_terraform_structure

    base = tmp_path / "terraform"
    for name in check_terraform_structure.ROOT_NAMES:
        folder = base / name
        folder.mkdir(parents=True)
        (folder / "variables.tf").write_text(VARIABLE)
        (folder / "outputs.tf").write_text(OUTPUT)
        (folder / "providers.tf").write_text(TERRAFORM)
        (folder / "main.tf").write_text("locals {}\n")
        (folder / "service.tf").write_text('module "service" {\n  source = "../modules/one"\n}\n')
    module = base / "modules/one"
    (module / "tests").mkdir(parents=True)
    (module / "tests/one.tftest.hcl").write_text('run "x" {}\n')
    (module / "README.md").write_text("# one\n")
    (module / "main.tf").write_text("locals {}\n")
    (module / "variables.tf").write_text(VARIABLE)
    (module / "outputs.tf").write_text(OUTPUT)
    (module / "versions.tf").write_text(TERRAFORM)
    return check_terraform_structure, base


def findings(module, base):
    return module.check(base)[0]


def test_a_good_tree_is_valid(structure):
    module, base = structure
    assert module.check(base) == ([], 3, 1)


@pytest.mark.parametrize("missing", ["variables.tf", "providers.tf", "outputs.tf", "main.tf"])
def test_a_root_without_a_required_file_fails(structure, missing):
    module, base = structure
    (base / "state" / missing).unlink()
    assert any(f"state: missing {missing}" in item for item in findings(module, base))


def test_a_module_call_outside_a_service_file_fails(structure):
    module, base = structure
    (base / "bootstrap/main.tf").write_text('module "x" {\n  source = "../modules/one"\n}\n')
    assert any("bootstrap/main.tf" in item for item in findings(module, base))


@pytest.mark.parametrize(
    "missing", ["README.md", "main.tf", "variables.tf", "outputs.tf", "versions.tf"]
)
def test_a_module_without_a_standard_file_fails(structure, missing):
    module, base = structure
    (base / "modules/one" / missing).unlink()
    assert any(f"modules/one: missing {missing}" in item for item in findings(module, base))


def test_a_module_without_tests_fails(structure):
    module, base = structure
    shutil.rmtree(base / "modules/one/tests")
    assert any("missing tests" in item for item in findings(module, base))


def test_variables_and_outputs_need_descriptions(structure):
    module, base = structure
    (base / "modules/one/variables.tf").write_text('variable "x" {\n  type = string\n}\n')
    (base / "modules/one/outputs.tf").write_text('output "y" {\n  value = 1\n}\n')
    found = findings(module, base)
    assert any("variable x has no description" in item for item in found)
    assert any("output y has no description" in item for item in found)


def test_a_nested_module_call_outside_main_fails(structure):
    module, base = structure
    (base / "modules/one/extra.tf").write_text('module "n" {\n  source = "../two"\n}\n')
    assert any("nested module call outside main.tf" in item for item in findings(module, base))


def test_network_resources_belong_only_to_bootstrap_and_the_network_module(structure):
    module, base = structure
    (base / "app_environment/net.tf").write_text('resource "azurerm_subnet" "x" {}\n')
    (base / "bootstrap/net.tf").write_text('resource "azurerm_subnet" "x" {}\n')
    found = findings(module, base)
    assert any("app_environment/net.tf" in item for item in found)
    assert not any("bootstrap/net.tf" in item for item in found)


@pytest.mark.parametrize(
    "text",
    [
        'resource "azurerm_network_security_rule" "x" {}\n',
        'resource "azurerm_route" "x" {}\n',
        'resource "azurerm_nat_gateway" "x" {}\n',
        'resource "azurerm_firewall" "x" {}\n',
        'resource "azurerm_network_security_group" "x" {\n  security_rule {}\n}\n',
    ],
)
def test_custom_network_rules_fail_everywhere(structure, text):
    module, base = structure
    (base / "bootstrap/rules.tf").write_text(text)
    assert any("custom security rule" in item for item in findings(module, base))


def test_azuread_is_forbidden(structure):
    module, base = structure
    (base / "app_environment/identity.tf").write_text('data "azuread_client_config" "x" {}\n')
    assert any("azuread" in item for item in findings(module, base))


@pytest.mark.parametrize("version", ["~> 5.0", ">= 5.11.0", "5"])
def test_unpinned_versions_fail(structure, version):
    module, base = structure
    path = base / "modules/one/versions.tf"
    path.write_text(path.read_text().replace('"= 5.11.0"', f'"{version}"'))
    assert any("not pinned exactly" in item for item in findings(module, base))


def test_leftover_retired_layout_fails(structure):
    module, base = structure
    (base / "environments").mkdir()
    assert any("retired layout" in item for item in findings(module, base))


def test_the_repository_tree_is_valid():
    import check_terraform_structure as module

    found, roots, modules = module.check()
    assert found == [] and (roots, modules) == (3, 13)
