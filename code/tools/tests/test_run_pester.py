"""The Pester runner calls PowerShell 7 with Pester 5 over the identity script tests."""

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_the_runner_invokes_pester_5_on_the_identity_tests(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import run_pester

    seen = {}

    def run(command, **keywords):
        seen["command"], seen["cwd"] = command, keywords["cwd"]
        return type("Result", (), {"returncode": 7})()

    monkeypatch.setattr(run_pester.shutil, "which", lambda name: "pwsh")
    monkeypatch.setattr(run_pester.subprocess, "run", run)
    assert run_pester.main(tmp_path) == 7
    assert seen["cwd"] == tmp_path and seen["command"][0] == "pwsh"
    assert "-MinimumVersion 5.0.0" in seen["command"][-1]
    assert "code/infra/scripts/identity/tests" in seen["command"][-1]


def test_a_missing_powershell_fails_without_claiming_success(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import run_pester

    monkeypatch.setattr(run_pester.shutil, "which", lambda name: None)
    assert run_pester.main() == 2


def test_the_identity_tests_exist_and_ci_runs_them():
    root = SCRIPTS.parents[2]
    assert (root / "code/infra/scripts/identity/tests/Initialize-Identity.Tests.ps1").is_file()
    assert "run_pester.py" in (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
