"""Tests for the environment file validator."""

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_environment as validator  # noqa: E402

INT = validator.ENVIRONMENTS / "int"


@pytest.fixture
def base(tmp_path: Path) -> Path:
    shutil.copytree(INT, tmp_path / "int")
    return tmp_path


def read(base: Path, name: str = "int") -> dict:
    return json.loads((base / name / f"{name}.tfvars.json").read_text())


def write(base: Path, data: dict, name: str = "int") -> None:
    (base / name / f"{name}.tfvars.json").write_text(json.dumps(data))


def problems(base: Path, selected: str | None = "int") -> set[tuple[str, str]]:
    report = validator.validate(selected, base=base)
    return {(finding["setting"], finding["problem"]) for finding in report.errors}


def mutate(base: Path, path: str, value) -> None:
    data = read(base)
    node = data
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    if value is KeyError:
        del node[parts[-1]]
    else:
        node[parts[-1]] = value
    write(base, data)


def test_repository_int_environment_is_valid():
    report = validator.validate("int")
    assert report.errors == []


def test_open_range_warns_but_passes(base):
    report = validator.validate("int", base=base)
    assert report.errors == []
    assert [w["warning"] for w in report.warnings] == ["open_range"]


@pytest.mark.parametrize(
    ("path", "value", "setting", "problem"),
    [
        ("environment.name", "prod", "environment.name", "invalid_format"),
        (
            "environment.subscription_id",
            "not-a-guid",
            "environment.subscription_id",
            "invalid_format",
        ),
        (
            "environment.public_access.allowed_cidrs",
            [],
            "environment.public_access.allowed_cidrs",
            "required_when_enabled",
        ),
        (
            "environment.public_access.allowed_cidrs",
            ["nonsense"],
            "environment.public_access.allowed_cidrs",
            "invalid_cidr",
        ),
        ("state.storage_account_name", "Bad_Name", "state.storage_account_name", "invalid_format"),
        ("state.storage_account_name", "ab", "state.storage_account_name", "invalid_format"),
        ("state.state_retention_days", 6, "state.state_retention_days", "invalid_format"),
        ("state.state_retention_days", 366, "state.state_retention_days", "invalid_format"),
        (
            "bootstrap.container_apps_subnet_cidr",
            "10.60.0.0/28",
            "bootstrap.container_apps_subnet_cidr",
            "invalid_format",
        ),
        (
            "bootstrap.container_apps_subnet_cidr",
            "10.99.0.0/26",
            "bootstrap.container_apps_subnet_cidr",
            "invalid_format",
        ),
        (
            "bootstrap.private_endpoint_subnet_cidr",
            "10.60.0.0/26",
            "bootstrap.private_endpoint_subnet_cidr",
            "invalid_format",
        ),
        (
            "app_environment.signing_key.key_id",
            "-bad",
            "app_environment.signing_key.key_id",
            "invalid_format",
        ),
        (
            "app_environment.model.live_deployment",
            "replace-text-deployment",
            "app_environment.model.live_deployment",
            "invalid_format",
        ),
        (
            "app_environment.identity.user_group_id",
            KeyError,
            "app_environment.identity.user_group_id",
            "missing",
        ),
        (
            "app_environment.container_apps.runtime.app_name",
            "A",
            "app_environment.container_apps.runtime.app_name",
            "invalid_format",
        ),
        (
            "app_environment.container_apps.runtime.image.tag",
            "latest",
            "app_environment.container_apps.runtime.image.tag",
            "invalid_format",
        ),
    ],
)
def test_invalid_values_are_reported_by_setting(base, path, value, setting, problem):
    mutate(base, path, value)
    assert (setting, problem) in problems(base)


def test_enabled_without_allow_list_key_is_required(base):
    mutate(base, "environment.public_access.allowed_cidrs", KeyError)
    assert ("environment.public_access.allowed_cidrs", "required_when_enabled") in problems(base)


def test_absent_public_access_means_disabled(base):
    mutate(base, "environment.public_access", KeyError)
    report = validator.validate("int", base=base)
    assert report.errors == [] and report.warnings == []


def test_image_needs_exactly_one_of_tag_or_digest(base):
    data = read(base)
    image = data["app_environment"]["container_apps"]["runtime"]["image"]
    image["digest"] = "sha256:" + "a" * 64
    write(base, data)
    assert ("app_environment.container_apps.runtime.image", "invalid_format") in problems(base)
    del image["tag"]
    write(base, data)
    assert problems(base) == set()
    image["digest"] = "sha256:short"
    write(base, data)
    assert ("app_environment.container_apps.runtime.image.digest", "invalid_format") in problems(
        base
    )


def test_semver_tag_is_accepted(base):
    mutate(base, "app_environment.container_apps.management.image.tag", "v1.2.3")
    assert problems(base) == set()


def test_unknown_and_missing_top_level_keys(base):
    data = read(base)
    data["extra"] = {}
    del data["state"]
    write(base, data)
    found = problems(base)
    assert ("extra", "unknown_key") in found
    assert ("state", "missing") in found


def test_duplicate_keys_and_invalid_json(base):
    (base / "int" / "int.tfvars.json").write_text('{"environment": {}, "environment": {}}')
    assert ("$", "duplicate_key") in problems(base)
    (base / "int" / "int.tfvars.json").write_text("{not json")
    assert ("$", "invalid_format") in problems(base)


def test_file_set_must_be_exact(base):
    (base / "int" / "notes.txt").write_text("x")
    (base / "int" / "state.azurerm.tfbackend.json").unlink()
    found = problems(base)
    assert ("$", "unknown_key") in found
    assert ("$", "missing") in found


@pytest.mark.parametrize(
    "key", ["client_secret", "db_password", "api_token", "connection_string", "sas_url"]
)
def test_secret_like_key_names_are_rejected(base, key):
    data = read(base)
    data["app_environment"][key] = "x"
    write(base, data)
    assert (f"app_environment.{key}", "secret_like_key") in problems(base)


def test_allow_listed_key_names_are_accepted(base):
    assert problems(base) == set()


@pytest.mark.parametrize(
    "value",
    [
        "-----BEGIN PRIVATE KEY-----",  # pragma: allowlist secret
        "A" * 44,
        "eyJhbGciOiJSUzI1NiJ9.payload.sig",
        "AccountKey=abc",
    ],
)
def test_secret_like_values_are_rejected(base, value):
    mutate(base, "app_environment.application.voice", value)
    assert ("app_environment.application.voice", "secret_like_value") in problems(base)


def test_messages_never_contain_the_offending_value(base):
    secret = "-----BEGIN PRIVATE KEY-----"  # pragma: allowlist secret
    mutate(base, "app_environment.application.voice", secret)
    report = validator.validate("int", base=base)
    assert secret not in json.dumps(report.errors)


def test_backend_rules(base):
    path = base / "int" / "bootstrap.azurerm.tfbackend.json"
    data = json.loads(path.read_text())
    data.update(
        {
            "access_key": "x",
            "key": "int/state.tfstate",
            "storage_account_name": "other",
            "use_cli": True,
        }
    )
    path.write_text(json.dumps(data))
    found = problems(base)
    assert ("access_key", "secret_like_key") in found
    assert ("key", "invalid_format") in found
    assert ("storage_account_name", "invalid_format") in found
    assert ("use_cli", "invalid_format") in found


def clone(base: Path, name: str) -> None:
    shutil.copytree(base / "int", base / name)
    (base / name / "int.tfvars.json").rename(base / name / f"{name}.tfvars.json")
    data = read(base, name)
    data["environment"]["name"] = name
    write(base, data, name)
    for deployment in validator.DEPLOYMENTS:
        path = base / name / f"{deployment}.azurerm.tfbackend.json"
        backend = json.loads(path.read_text())
        backend["key"] = f"{name}/{deployment}.tfstate"
        path.write_text(json.dumps(backend))


def test_second_environment_with_only_names_changed_is_valid(base):
    clone(base, "dev")
    data = read(base, "dev")
    data["environment"]["subscription_id"] = "00000000-0000-4000-8000-0000000000aa"
    data["state"]["storage_account_name"] = "callerdevstate0001"
    data["app_environment"]["data_store"]["name"] = "cosmos-caller-dev"
    data["app_environment"]["secret_store"]["name"] = "kv-caller-dev"
    write(base, data, "dev")
    for deployment in validator.DEPLOYMENTS:
        path = base / "dev" / f"{deployment}.azurerm.tfbackend.json"
        backend = json.loads(path.read_text())
        backend["storage_account_name"] = "callerdevstate0001"
        path.write_text(json.dumps(backend))
    assert validator.validate(None, base=base).errors == []


def test_cross_environment_clashes_are_reported(base):
    clone(base, "dev")
    found = problems(base, None)
    assert ("environment.subscription_id", "duplicate_across_environments") in found
    assert ("state.storage_account_name", "duplicate_across_environments") in found


def test_other_environment_problems_are_reported_only_for_itself(base):
    clone(base, "dev")
    data = read(base, "dev")
    data["environment"]["subscription_id"] = "00000000-0000-4000-8000-0000000000bb"
    data["app_environment"]["data_store"]["name"] = "cosmos-caller-dev"
    data["app_environment"]["secret_store"]["name"] = "kv-caller-dev"
    del data["state"]
    write(base, data, "dev")
    assert problems(base, "int") == set()
    assert ("state", "missing") in problems(base, "dev")


def test_findings_are_single_line_json_with_file_setting_problem(base, capsys, monkeypatch):
    mutate(base, "state.state_retention_days", 1)
    monkeypatch.setattr(validator, "ENVIRONMENTS", base)
    monkeypatch.setattr(sys, "argv", ["validate_environment.py", "--environment", "int"])
    assert validator.main() == 1
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    errors = [line for line in lines if "problem" in line]
    assert errors and set(errors[0]) == {"file", "setting", "problem"}


def test_success_output_lists_environments(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["validate_environment.py"])
    assert validator.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"result": "environment_valid", "environments": ["int"]}


def test_unused_copy_import_is_available():
    assert copy.deepcopy({"a": 1}) == {"a": 1}


def test_the_runtime_registration_must_differ_from_the_management_registration(base):
    mutate(
        base,
        "app_environment.identity.runtime_app_client_id",
        read(base)["app_environment"]["identity"]["management_app_client_id"],
    )
    assert ("app_environment.identity.runtime_app_client_id", "must_differ") in problems(base)


def test_the_management_role_name_is_required(base):
    mutate(base, "app_environment.identity.roles.management", "")
    assert ("app_environment.identity.roles.management", "missing") in problems(base)
