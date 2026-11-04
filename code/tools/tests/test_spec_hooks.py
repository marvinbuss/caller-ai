"""Spec Kit hooks stay optional, prompted, on Spec Kit events only, and skip without gh."""

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / ".specify/extensions.yml"
PUSH_PR = ROOT / ".specify/extensions/caller-github/scripts/python/push_pr.py"
SPEC_KIT_EVENTS = {
    f"{moment}_{command}"
    for moment in ("before", "after")
    for command in (
        "constitution",
        "specify",
        "clarify",
        "plan",
        "tasks",
        "implement",
        "checklist",
        "analyze",
        "taskstoissues",
    )
}


@pytest.fixture(scope="module")
def push_pr():
    specification = importlib.util.spec_from_file_location("caller_push_pr", PUSH_PR)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_registry_parses_and_every_hook_is_optional_and_prompted():
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))

    hooks = [entry for entries in registry["hooks"].values() for entry in entries]
    assert hooks and registry["settings"]["auto_execute_hooks"] is False
    for entry in hooks:
        assert entry["optional"] is True
        assert isinstance(entry["prompt"], str) and entry["prompt"].strip().endswith("?")
        assert entry["extension"] in registry["installed"]


def test_hooks_are_registered_only_on_spec_kit_events():
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))

    assert set(registry["hooks"]) <= SPEC_KIT_EVENTS


def test_hook_set_matches_the_contract():
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["hooks"]

    commands = {
        event: [entry["command"] for entry in entries] for event, entries in registry.items()
    }
    assert commands["before_specify"] == ["speckit.git.feature"]
    assert "speckit.github.taskstoissues" in commands["after_tasks"]
    assert commands["after_implement"][-1] == "speckit.caller-github.push-pr"
    for event in ("after_specify", "after_plan", "after_tasks", "after_implement"):
        assert "speckit.git.commit" in commands[event]


def test_missing_gh_or_login_skips_with_a_reason_and_changes_nothing(push_pr):
    calls = []

    def runner(command):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 1, "", "")

    assert push_pr.preflight(lambda tool: None if tool == "gh" else tool, runner) == (
        "gh is not installed"
    )
    assert (
        push_pr.preflight(lambda tool: tool, runner) == "gh is not authenticated; run gh auth login"
    )
    assert calls == [["gh", "auth", "status"]]


def test_push_and_pull_request_refuse_without_confirmation(push_pr, capsys):
    calls = []

    def runner(command):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "feature-branch\n", "")

    assert push_pr.execute("Title", confirmed=False, runner=runner) == 2
    assert calls == [] and "REFUSED" in capsys.readouterr().out


def test_confirmed_run_never_force_pushes_or_acts_on_main(push_pr):
    calls = []

    def runner(command):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "feature-branch\n", "")

    assert push_pr.execute("Title", confirmed=True, runner=runner) == 0
    push = next(call for call in calls if call[:2] == ["git", "push"])
    assert not {"--force", "-f", "--force-with-lease"} & set(push)
    assert push[-2:] == ["origin", "feature-branch"]
    on_main = lambda command: subprocess.CompletedProcess(command, 0, "main\n", "")  # noqa: E731
    assert push_pr.execute("Title", confirmed=True, runner=on_main) == 2


def test_the_command_skips_cleanly_when_gh_is_not_on_path():
    environment = {"PATH": str(Path(subprocess.sys.executable).parent), "SYSTEMROOT": "C:\\Windows"}
    result = subprocess.run(
        [subprocess.sys.executable, "-B", str(PUSH_PR), "--check"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.startswith("SKIP: ") and "not installed" in result.stdout
