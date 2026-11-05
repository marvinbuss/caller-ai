"""Management failure recovery uses its actual composed app and owned processes."""

import importlib.util
import json
import os
import shutil
import socket
import time
import tomllib
from pathlib import Path
from threading import Event

import psutil
import pytest

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT.parents[1]


@pytest.fixture
def runner():
    specification = importlib.util.spec_from_file_location(
        "management_recovery_smoke",
        ROOT / "code/tools/scripts/smoke.py",
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def child_environment() -> dict[str, str]:
    values = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("CALLER_", "AZURE_", "OPENAI_"))
    }
    values.pop("VIRTUAL_ENV", None)
    values["CALLER_MODE"] = "local"
    values["UV_DEFAULT_INDEX"] = "https://pypi.org/simple"
    values["UV_OFFLINE"] = "1"
    values["__REFLEX_SKIP_COMPILE"] = "true"
    return values


def execute(arguments, values, runner, directory: Path) -> tuple[int, str]:
    with (directory / "process.log").open("w+", encoding="utf-8") as output:
        process = psutil.Popen(arguments, cwd=PROJECT, env=values, stdout=output, stderr=output)
        try:
            code = process.wait(timeout=30)
        finally:
            runner.stop([process])
        assert not process.is_running()
        output.seek(0)
        return code, output.read()


def test_occupied_port_fails_without_stealing_listener(runner, child_environment, tmp_path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        code, output = execute(
            [
                "uv",
                "run",
                "--locked",
                "granian",
                "--interface",
                "asgi",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--loop",
                "asyncio",
                "management_app.management_app:app",
            ],
            child_environment,
            runner,
            tmp_path,
        )
        assert code != 0
        assert any(error in output.lower() for error in ("in use", "10048", "os error 10013"))
        assert listener.getsockname()[1] == port
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            accepted, _ = listener.accept()
            accepted.close()


@pytest.mark.parametrize("mode", ["invalid", "live"])
def test_invalid_or_unconfigured_live_mode_fails(runner, child_environment, tmp_path, mode) -> None:
    child_environment["CALLER_MODE"] = mode
    source = (
        "from fastapi.testclient import TestClient; "
        "from management_app.management_app import app; "
        "client=TestClient(app()); client.__enter__()"
    )
    code, output = execute(
        ["uv", "run", "--locked", "python", "-c", source], child_environment, runner, tmp_path
    )
    assert code != 0
    assert "ValidationError" in output if mode == "invalid" else "settings_missing" in output


@pytest.mark.parametrize("state", ["missing", "stale"])
def test_missing_or_stale_lock_cannot_start(runner, child_environment, tmp_path, state) -> None:
    code, _ = execute(["uv", "lock", "--check"], child_environment, runner, tmp_path)
    assert code == 0
    project = tmp_path / "project"
    project.mkdir()
    metadata = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]
    (project / "pyproject.toml").write_text(
        "[project]\nname = "
        + json.dumps(metadata["name"])
        + '\nversion = "0.0.0"\nrequires-python = ">=3.12,<3.13"\ndependencies = []\n'
    )
    if state == "stale":
        shutil.copyfile(PROJECT / "uv.lock", project / "uv.lock")
    code, output = execute(
        ["uv", "lock", "--check", "--project", str(project)], child_environment, runner, tmp_path
    )
    assert code != 0
    assert "lockfile" in output.lower()
    assert not (project / ".venv").exists()


def test_interrupted_startup_cleans_owned_tree(runner, child_environment, tmp_path) -> None:
    marker = tmp_path / "starting"
    source = f"""import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi.testclient import TestClient
from management_app.management_app import create_app
app = create_app()
@asynccontextmanager
async def delayed():
    Path({str(marker)!r}).write_text("starting")
    await asyncio.Event().wait()
    yield
app.register_lifespan_task(delayed)
with TestClient(app()):
    raise AssertionError("interrupted startup unexpectedly completed")
"""
    with (tmp_path / "startup.log").open("w", encoding="utf-8") as output:
        process = psutil.Popen(
            ["uv", "run", "--locked", "python", "-c", source],
            cwd=PROJECT,
            env=child_environment,
            stdout=output,
            stderr=output,
        )
        owned = []
        try:
            deadline = time.monotonic() + 30
            while not marker.exists() and time.monotonic() < deadline:
                assert process.poll() is None
                Event().wait(0.01)
            assert marker.exists()
            owned = process.children(recursive=True)
            runner.stop([process])
            assert not process.is_running()
            assert all(
                not child.is_running() or child.status() == psutil.STATUS_ZOMBIE for child in owned
            )
        finally:
            runner.stop([process])
