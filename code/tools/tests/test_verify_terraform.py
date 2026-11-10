"""Offline contracts for the credential-free Terraform verification."""

import json
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import verify_terraform

    return verify_terraform


def test_the_three_roots_and_every_module_are_discovered(module):
    assert [path.name for path in module.ROOTS] == ["state", "bootstrap", "app_environment"]
    names = {path.name for path in module.modules()}
    assert len(names) == 13
    assert {"network", "container-app", "permit-key", "model-access"} <= names


def test_check_runs_fmt_validate_and_test_for_each_root_and_module(module, monkeypatch):
    calls = []

    def execute(binary, arguments, *, directory, values):
        calls.append((arguments[0], directory))
        output = ""
        if arguments[0] == "test":
            output = json.dumps({"type": "test_summary", "test_summary": _summary()})
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(module, "require_toolchain", lambda: Path("terraform"))
    monkeypatch.setattr(module, "execute", execute)
    monkeypatch.setattr(module, "environment_findings", lambda: [])
    scans = []
    monkeypatch.setattr(module, "scan_configuration", lambda: scans.append(1))
    module.check()
    assert scans == [1]
    directories = [*module.ROOTS, *module.modules()]
    for command in ("validate", "test"):
        assert [d for name, d in calls if name == command] == directories
    assert [name for name, _ in calls].count("fmt") == 1


def test_the_configuration_scan_is_left_out_only_when_asked(module, monkeypatch, capsys):
    monkeypatch.setattr(module, "require_toolchain", lambda: Path("terraform"))
    monkeypatch.setattr(
        module,
        "execute",
        lambda binary, arguments, **keywords: subprocess.CompletedProcess(
            arguments,
            0,
            stdout=json.dumps({"type": "test_summary", "test_summary": _summary()}),
            stderr="",
        ),
    )
    monkeypatch.setattr(module, "environment_findings", lambda: [])
    scans = []
    monkeypatch.setattr(module, "scan_configuration", lambda: scans.append(1))
    module.check(scan=False)
    assert scans == []
    assert json.loads(capsys.readouterr().out)["configuration_scan"] is False


def test_the_hook_leaves_out_the_container_scan_and_the_terraform_job_runs_it():
    import yaml

    root = SCRIPTS.parents[2]
    hooks = {
        hook["id"]: hook
        for hook in yaml.safe_load((root / "code/.pre-commit-config.yaml").read_text())["repos"][0][
            "hooks"
        ]
    }
    # Windows runners cannot start the Linux scanner image; the Linux Terraform job runs it.
    assert hooks["terraform"]["entry"].endswith("verify_terraform.py check --no-scan")
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    terraform = workflow["jobs"]["terraform"]
    assert terraform["runs-on"] == "ubuntu-24.04"
    runs = [step.get("run", "") for step in terraform["steps"]]
    assert any(run.endswith("verify_terraform.py check") for run in runs)


def test_environment_validation_failure_stops_the_check_before_terraform_runs(module, monkeypatch):
    calls = []
    monkeypatch.setattr(module, "require_toolchain", lambda: Path("terraform"))
    monkeypatch.setattr(
        module,
        "execute",
        lambda binary, arguments, **keywords: (
            calls.append(arguments[0])
            or subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(module, "environment_findings", lambda: ["bad"])
    with pytest.raises(RuntimeError, match="environment_validation_failed"):
        module.check()
    assert "validate" not in calls and "test" not in calls


def test_the_reviewed_environment_folders_pass_validation(module):
    assert module.environment_findings() == []


def test_incomplete_native_tests_are_rejected(module):
    bad = json.dumps({"type": "test_summary", "test_summary": _summary(failed=1)})
    with pytest.raises(RuntimeError, match="terraform_native_tests_incomplete"):
        module.native_test_count(bad)


def _summary(**overrides):
    return {"status": "pass", "passed": 3, "failed": 0, "errored": 0, "skipped": 0, **overrides}


def _docker_available():
    import shutil

    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def test_the_scan_fails_on_any_misconfiguration_and_on_empty_results(module):
    clean = json.dumps({"Results": [{"Target": "x", "Class": "config"}]})
    bad = json.dumps({"Results": [{"Target": "x", "Misconfigurations": [{"ID": "AZU-0012"}]}]})
    assert module.scan_configuration(Path("."), run_docker=lambda *a: clean) == 1
    for output in (bad, json.dumps({"Results": []}), "not json"):
        with pytest.raises(RuntimeError, match="terraform_security_scan_failed"):
            module.scan_configuration(Path("."), run_docker=lambda *a, output=output: output)


def test_the_scan_uses_the_pinned_image_and_a_read_only_mount(module):
    seen = []
    module.scan_configuration(
        Path("."), run_docker=lambda *a: seen.append(a) or json.dumps({"Results": [{}]})
    )
    arguments = seen[0]
    assert "sha256:" in arguments[4] and arguments[3].endswith(":/scan:ro")
    assert "HIGH,CRITICAL" in arguments


@pytest.mark.skipif(not _docker_available(), reason="Docker is required for the real scan")
def test_the_real_scan_fails_on_a_seeded_misconfiguration_and_passes_on_a_clean_tree(module):
    fixtures = Path(__file__).parent / "fixtures"
    with pytest.raises(RuntimeError, match="terraform_security_scan_failed"):
        module.scan_configuration(fixtures / "terraform-misconfig")
    module.scan_configuration(fixtures / "terraform-clean")
