"""Release regression summaries cannot turn empty, skipped or missing live evidence into success."""

import asyncio
import importlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def release(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("verify_release")


@pytest.mark.parametrize("kind", ["pass", "empty", "failed", "skipped", "missing", "wrong_exit"])
def test_local_regression_requires_actual_nonempty_complete_xml_and_successful_exit(release, kind):
    commands = []

    def run(command, values, timeout):
        commands.append(command)
        assert not any(key.startswith(("CALLER_", "AZURE_", "OPENAI_")) for key in values)
        output = Path(
            next(value.split("=", 1)[1] for value in command if value.startswith("--junitxml="))
        )
        if kind != "missing":
            root = ET.Element("testsuites")
            suite = ET.SubElement(root, "testsuite", tests="0" if kind == "empty" else "1")
            if kind != "empty":
                case = ET.SubElement(suite, "testcase", name="fixture")
                if kind in {"failed", "skipped"}:
                    ET.SubElement(case, "failure" if kind == "failed" else "skipped")
            ET.ElementTree(root).write(output, encoding="utf-8")
        return 1 if kind in {"failed", "wrong_exit"} else 0

    report = release.run_local(runner=run)
    assert len(commands) == 3
    assert report["local_status"] == ("passed" if kind == "pass" else "failed")
    assert report["release_status"] == "blocked" and report["live_status"] == "pending"
    assert {suite["name"] for suite in report["suites"]} == {"runtime", "management", "tools"}
    assert all("-k" not in command and "--ignore" not in command for command in commands)
    assert all(
        command[command.index("--no-sync") + 1 : command.index("--no-sync") + 4]
        == ["python", "-m", "pytest"]
        for command in commands
    )


def test_unattended_suite_runner_closes_inherited_input_without_changing_deadline(
    release, monkeypatch
):
    from types import SimpleNamespace

    options_seen, cleaned = [], []
    process = SimpleNamespace(
        wait=lambda *, timeout: 0 if timeout == 900 else pytest.fail("runner_budget_changed")
    )

    def spawn(command, **options):
        assert command == ["owned-pytest"]
        options_seen.append(options)
        return process

    monkeypatch.setattr(release.psutil, "Popen", spawn)
    monkeypatch.setattr(release, "stop", lambda processes: cleaned.extend(processes))
    assert release.execute(["owned-pytest"], {}, 900) == 0
    assert options_seen[0]["stdin"] == release.subprocess.DEVNULL
    assert options_seen[0]["stdout"] == options_seen[0]["stderr"] == release.subprocess.DEVNULL
    assert cleaned == [process]


def test_requirement_inventory_is_complete_and_participant_study_stays_deferred(release):
    inventory = release.requirements()
    assert set(inventory) == {f"FR-{index:03}" for index in range(1, 35)} | {
        f"SC-{index:03}" for index in range(1, 20)
    }
    assert inventory["SC-008"]["status"] == "deferred"
    assert all(
        value["status"] in {"pending_qualification", "deferred"} for value in inventory.values()
    )


def test_multimodal_inventory_does_not_inherit_historical_participant_deferral(release):
    inventory = release.requirements(feature="002-multimodal-agent-management")
    assert set(inventory) == {f"FR-{index:03}" for index in range(1, 35)} | {
        f"SC-{index:03}" for index in range(1, 11)
    }
    assert all(value["status"] == "pending_qualification" for value in inventory.values())
    assert all(
        value["evidence"] == "docs/acceptance/multimodal-agent-management.md"
        for value in inventory.values()
    )
    assert release.requirements()["SC-008"]["status"] == "deferred"


def test_multimodal_live_inventory_is_read_only_without_any_runner(release, monkeypatch, capsys):
    def forbidden(*arguments, **keywords):
        pytest.fail("feature_inventory_started_live_execution")

    monkeypatch.setattr(release, "execute", forbidden)
    assert release.main(["--profile", "live", "--feature", "002-multimodal-agent-management"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["live_status"] == "pending" and report["release_status"] == "blocked"
    assert len(report["requirements"]) == 44


def test_live_profile_refuses_without_any_runner_or_credentials(release, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("live_profile_started_execution")

    monkeypatch.setattr(release, "execute", forbidden)
    assert release.main(["--profile", "live"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["live_status"] == "pending" and report["release_status"] == "blocked"


@pytest.mark.parametrize("kind", ["pass", "denied", "unreviewed", "revoked", "uncertain"])
def test_release_requires_fresh_authority_and_reviewed_plan_before_immutable_publication(
    release, kind
):
    checks = importlib.import_module("release_checks")
    request = checks.ReleaseRequest(
        "operation-fixture",
        "a" * 40,
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
        "environment-fixture",
    )
    events = []

    class Authority:
        async def authorize(self, request, *, deadline):
            events.append("authorize")
            return kind != "denied" and not (kind == "revoked" and events.count("authorize") > 1)

    class Port:
        async def reviewed_plan(self, request, *, deadline):
            events.append("plan")
            return kind != "unreviewed"

        async def publish(self, request, *, deadline):
            events.append("publish")
            if kind == "uncertain":
                raise RuntimeError("synthetic-private-publication-details")
            return "published"

    outcome = asyncio.run(checks.release(request, authority=Authority(), port=Port()))
    assert outcome == (
        "published" if kind == "pass" else "uncertain" if kind == "uncertain" else "blocked"
    )
    assert ("publish" in events) is (kind in {"pass", "uncertain"})


def test_release_workflow_is_manual_read_only_and_refuses_unqualified_effects(release):
    root = Path(__file__).resolve().parents[3]
    workflow = yaml.load(
        (root / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["review"]
    assert job["environment"] == "release-review"
    assert job["steps"][-1]["run"].endswith("release_checks.py --preflight")
    assert all(
        step["run"].startswith("python code/tools/scripts/")
        for step in job["steps"]
        if "run" in step
    )
    assert "id-token" not in json.dumps(workflow) and "secrets." not in json.dumps(workflow)


def test_release_preflight_rejects_unknown_sensitive_arguments_without_reflection(release, capsys):
    checks = importlib.import_module("release_checks")
    assert checks.main(["--preflight", "--credential", "synthetic-private-candidate"]) == 2
    output = capsys.readouterr()
    assert "synthetic-private-candidate" not in output.out + output.err
