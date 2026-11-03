"""Deterministic full-suite regression entry point; no live execution or release approval."""

import argparse
import json
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Never

import psutil
from ci import ROOT, environment
from smoke import stop

Runner = Callable[[list[str], dict[str, str], int], int]
SUITES = (("runtime", "code/backend"), ("management", "code/management"), ("tools", "code/tools"))
FEATURES = ("001-call-center-agents", "002-multimodal-agent-management")


def requirements(*, feature: str = "001-call-center-agents") -> dict[str, dict[str, str]]:
    if feature not in FEATURES:
        raise ValueError("unknown_release_feature")
    multimodal = feature == "002-multimodal-agent-management"
    keys = [f"FR-{index:03}" for index in range(1, 35)] + [
        f"SC-{index:03}" for index in range(1, 11 if multimodal else 20)
    ]
    return {
        key: {
            "status": "deferred" if key == "SC-008" and not multimodal else "pending_qualification",
            "evidence": "docs/acceptance/multimodal-agent-management.md"
            if multimodal
            else "docs/acceptance/requirements.md",
        }
        for key in keys
    }


def execute(command: list[str], values: dict[str, str], timeout: int) -> int:
    process = psutil.Popen(
        command,
        cwd=ROOT,
        env=values,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        code = process.wait(timeout=timeout)
        return int(code) if code is not None else 125
    finally:
        stop([process])


def junit(path: Path, code: int) -> dict[str, int | str]:
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        return {"status": "failed", "reason": "missing_or_oversized_results", "exit_code": code}
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failures = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    passed = code == 0 and bool(cases) and not (failures or errors or skipped)
    return {
        "status": "passed" if passed else "failed",
        "reason": "complete" if passed else "regression_gate_failed",
        "exit_code": code,
        "tests": len(cases),
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    }


def run_local(
    *, runner: Runner = execute, feature: str = "001-call-center-agents"
) -> dict[str, Any]:
    inventory = requirements(feature=feature)
    values = {
        key: value
        for key, value in environment().items()
        if not key.startswith(("CALLER_", "AZURE_", "OPENAI_"))
    }
    suites = []
    with tempfile.TemporaryDirectory(prefix="caller-release-") as temporary:
        for name, project in SUITES:
            path = Path(temporary) / f"{name}.xml"
            command = [
                "uv",
                "run",
                "--directory",
                project,
                "--locked",
                "--no-sync",
                "python",
                "-m",
                "pytest",
                "tests",
                "-q",
                "--tb=short",
                "--junitxml=" + str(path),
            ]
            try:
                code = runner(command, values, 900)
                result = junit(path, code)
            except Exception:
                result = {"status": "failed", "reason": "runner_or_result_unavailable"}
            suites.append({"name": name, **result})
    return {
        "schema_version": 1,
        "feature": feature,
        "profile": "local",
        "local_status": "passed"
        if all(suite["status"] == "passed" for suite in suites)
        else "failed",
        "live_status": "pending",
        "release_status": "blocked",
        "suites": suites,
        "requirements": inventory,
    }


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("invalid_release_arguments")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = SafeParser(description=__doc__)
    parser.add_argument("--profile", choices=("local", "live"), default="local")
    parser.add_argument("--feature", choices=FEATURES, default=FEATURES[0])
    try:
        options = parser.parse_args(arguments)
    except Exception:
        print(json.dumps({"release_status": "blocked", "reason": "invalid_release_arguments"}))
        return 2
    if options.profile == "live":
        print(
            json.dumps(
                {
                    "profile": "live",
                    "feature": options.feature,
                    "live_status": "pending",
                    "release_status": "blocked",
                    "reason": "authorized_environment_and_live_evidence_required",
                    "requirements": requirements(feature=options.feature),
                }
            )
        )
        return 2
    report = run_local(feature=options.feature)
    print(json.dumps(report))
    return 0 if report["local_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
