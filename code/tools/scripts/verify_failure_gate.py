"""Verify a real health assertion can fail and recover in a disposable fixture."""

import json
import subprocess
import tempfile
from pathlib import Path

from ci import ROOT, environment

FIXTURE = """from fastapi.testclient import TestClient
from caller_runtime.main import create_app

def test_starter_health_identity():
    with TestClient(create_app()) as client:
        assert client.get("/healthz").json()["service"] == {expected!r}
"""


def verify() -> dict[str, int | bool]:
    values = {
        key: value
        for key, value in environment().items()
        if not key.startswith(("CALLER_", "AZURE_", "OPENAI_"))
    }
    values["CALLER_MODE"] = "local"
    values["PYTHONDONTWRITEBYTECODE"] = "1"
    results: dict[str, int | bool] = {}
    with tempfile.TemporaryDirectory(prefix="caller-failure-gate-") as temporary:
        directory = Path(temporary)
        fixture = directory / "test_disposable_health.py"
        for phase, expected, exit_code in (
            ("baseline", "caller-runtime", 0),
            ("broken", "deliberately-wrong-service", 1),
            ("restored", "caller-runtime", 0),
        ):
            fixture.write_text(FIXTURE.format(expected=expected), encoding="utf-8")
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "--locked",
                    "--project",
                    str(ROOT / "code/backend"),
                    "pytest",
                    str(fixture),
                    "-c",
                    str(ROOT / "code/backend/pyproject.toml"),
                    "--confcutdir",
                    str(directory),
                    "-q",
                    "--tb=short",
                ],
                cwd=ROOT,
                env=values,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=45,
            )
            if result.returncode != exit_code:
                raise RuntimeError(f"failure_gate_{phase}_unexpected_exit")
            if phase == "broken":
                if "AssertionError" not in result.stdout or "1 failed" not in result.stdout:
                    raise RuntimeError("failure_gate_did_not_observe_assertion")
            elif "1 passed" not in result.stdout:
                raise RuntimeError("failure_gate_empty_or_incomplete_run")
            results[f"{phase}_exit"] = result.returncode
    results["fixture_cleaned"] = not directory.exists()
    if not results["fixture_cleaned"]:
        raise RuntimeError("failure_gate_fixture_cleanup_failed")
    return results


if __name__ == "__main__":
    print(json.dumps(verify()))
