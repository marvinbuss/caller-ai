"""Starts the real backend and management processes locally and verifies they become ready.

It owns the processes it starts on free loopback ports, waits for `/readyz` within a fixed budget,
checks the instance header so a foreign listener is never accepted, and stops only its own process
trees. `running_configuration_editor` is the test host for the browser and relay suites; each host
builds its own frontend because the compiled frontend bakes in that host's origin port.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import ModuleType
from uuid import uuid4

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[3]


def build_tools() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "caller_build_environment", Path(__file__).with_name("ci.py")
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("build_environment_unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    module.ROOT = ROOT
    return module


def build_environment() -> dict[str, str]:
    return build_tools().environment()


def restore_frontend_lock() -> None:
    build_tools().restore_frontend_lock()


def free_ports(count: int) -> list[int]:
    listeners: list[socket.socket] = []
    try:
        for _ in range(count):
            listener = socket.socket()
            listeners.append(listener)
            listener.bind(("127.0.0.1", 0))
        return [listener.getsockname()[1] for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()


def stop(processes: list[psutil.Popen]) -> None:
    owned: dict[int, psutil.Process] = {}
    for process in processes:
        try:
            for child in process.children(recursive=True):
                owned[child.pid] = child
            owned[process.pid] = process
        except psutil.NoSuchProcess:
            continue
    for process in reversed(list(owned.values())):
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(list(owned.values()), timeout=5)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(alive, timeout=5)
    if alive:
        raise RuntimeError("smoke_process_cleanup_failed")


def owns_port(processes: list[psutil.Popen], port: int) -> bool:
    for root in processes:
        try:
            for process in [root, *root.children(recursive=True)]:
                try:
                    connections = process.net_connections(kind="tcp")
                except psutil.NoSuchProcess:
                    continue
                if any(
                    connection.status == psutil.CONN_LISTEN and connection.laddr.port == port
                    for connection in connections
                ):
                    return True
        except psutil.NoSuchProcess:
            continue
    return False


def wait_ready(urls: dict[str, str], instance: str, processes: list[psutil.Popen]) -> None:
    deadline = time.monotonic() + 120
    with httpx.Client(timeout=1, trust_env=False) as client:
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("smoke_application_exited_before_ready")
            try:
                for name in ("runtime", "management"):
                    response = client.get(urls[name] + "/readyz")
                    response.raise_for_status()
                    if response.headers.get("X-Caller-Instance") != instance:
                        raise RuntimeError("smoke_response_from_unowned_process")
                    if response.json()["status"] != "ready":
                        raise ValueError("application_not_ready")
                frontend = client.get(urls["ui"])
                frontend.raise_for_status()
                if "<html" in frontend.text.lower():
                    if not owns_port(processes, int(urls["ui"].rsplit(":", 1)[1])):
                        raise RuntimeError("smoke_frontend_from_unowned_process")
                    return
            except (httpx.HTTPError, ValueError, KeyError):
                Event().wait(0.1)
    raise TimeoutError("smoke_readiness_deadline_exceeded")


@contextmanager
def running_apps() -> Iterator[dict[str, str]]:
    runtime, management, ui = free_ports(3)
    urls = {
        "runtime": f"http://127.0.0.1:{runtime}",
        "management": f"http://127.0.0.1:{management}",
        "ui": f"http://127.0.0.1:{ui}",
    }
    environment = {
        key: value
        for key, value in build_environment().items()
        if not key.startswith(("CALLER_", "AZURE_", "OPENAI_"))
    }
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    environment.update(
        {
            "CALLER_MODE": "local",
            "CALLER_INSTANCE_ID": uuid4().hex,
            "UV_DEFAULT_INDEX": environment.get("UV_DEFAULT_INDEX", "https://pypi.org/simple"),
            "NPM_CONFIG_REGISTRY": environment.get(
                "NPM_CONFIG_REGISTRY", "https://registry.npmjs.org/"
            ),
        }
    )
    processes: list[psutil.Popen] = []
    with tempfile.TemporaryDirectory(prefix="caller-smoke-") as temporary:
        with (Path(temporary) / "process.log").open("w+", encoding="utf-8") as output:
            try:
                commands = [
                    [
                        "uv",
                        "run",
                        "--directory",
                        "code/backend",
                        "--locked",
                        "uvicorn",
                        "caller_runtime.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(runtime),
                        "--loop",
                        "asyncio",
                        "--ws-max-size",
                        "65536",
                    ],
                    [
                        "uv",
                        "run",
                        "--directory",
                        "code/management",
                        "--locked",
                        "reflex",
                        "run",
                        "--env",
                        "dev",
                        "--frontend-port",
                        str(ui),
                        "--backend-port",
                        str(management),
                        "--backend-host",
                        "127.0.0.1",
                    ],
                ]
                for command in commands:
                    processes.append(
                        psutil.Popen(
                            command,
                            cwd=ROOT,
                            env=environment,
                            stdout=output,
                            stderr=subprocess.STDOUT,
                        )
                    )
                wait_ready(urls, environment["CALLER_INSTANCE_ID"], processes)
                yield urls
            finally:
                stop(processes)
                restore_frontend_lock()


@contextmanager
def running_configuration_editor(
    *, graph: dict, identities: tuple[str, ...], mode: str
) -> Iterator[dict]:
    if mode != "local":
        raise ValueError("test_identity_live_mode_prohibited")
    port, runtime_port = free_ports(2)
    origin = f"https://127.0.0.1:{port}"
    environment = {
        key: value
        for key, value in build_environment().items()
        if not key.startswith(("CALLER_", "AZURE_", "OPENAI_"))
    }
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    environment.update(
        {
            "CALLER_MODE": "local",
            "CALLER_INSTANCE_ID": uuid4().hex,
            "APP_MODULE_IMPORT": "tests.editor_app",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "API_URL": origin,
            "REFLEX_SSR": "true",
            "UV_DEFAULT_INDEX": environment.get("UV_DEFAULT_INDEX", "https://pypi.org/simple"),
            "NPM_CONFIG_REGISTRY": environment.get(
                "NPM_CONFIG_REGISTRY", "https://registry.npmjs.org/"
            ),
            "BUN_CONFIG_DEFAULT_REGISTRY": environment.get(
                "BUN_CONFIG_DEFAULT_REGISTRY", environment["NPM_CONFIG_REGISTRY"]
            ),
        }
    )
    processes: list[psutil.Popen] = []
    with tempfile.TemporaryDirectory(prefix="caller-editor-") as temporary:
        directory = Path(temporary).resolve()
        fixture = directory / "fixture.json"
        fixture.write_text(
            json.dumps(
                {
                    "mode": mode,
                    "graph": graph,
                    "identities": identities,
                    "origin": origin,
                    "port": port,
                    "runtime_port": runtime_port,
                }
            )
        )
        environment["CALLER_EDITOR_TEST_FIXTURE"] = str(fixture)
        environment["REFLEX_WEB_WORKDIR"] = str(directory / "web")
        with (directory / "process.log").open("w+", encoding="utf-8") as output:
            try:
                build = psutil.Popen(
                    [
                        "uv",
                        "run",
                        "--directory",
                        "code/management",
                        "--locked",
                        "python",
                        "-c",
                        "from tests.editor_app import compile_frontend; compile_frontend()",
                    ],
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                )
                processes.append(build)
                if build.wait(timeout=180) != 0:
                    output.flush()
                    category = build_tools().build_failure_category(directory / "process.log")
                    metadata = build_tools().build_failure_metadata(directory / "process.log")
                    raise RuntimeError(
                        "editor_frontend_build_failed:"
                        + category
                        + ":"
                        + json.dumps(metadata, separators=(",", ":"))
                    )
                environment.update(
                    {
                        "__REFLEX_SKIP_COMPILE": "true",
                        "__REFLEX_MOUNT_FRONTEND_COMPILED_APP": "true",
                        "REFLEX_ENV_MODE": "prod",
                        "UV_OFFLINE": "1",
                    }
                )
                deadline = time.monotonic() + 120
                fixture_document = json.loads(fixture.read_text())
                fixture_document["readiness_deadline"] = deadline
                fixture.write_text(json.dumps(fixture_document))
                server = psutil.Popen(
                    [
                        "uv",
                        "run",
                        "--directory",
                        "code/management",
                        "--locked",
                        "python",
                        "-c",
                        "from tests.editor_app import run_server; run_server()",
                    ],
                    cwd=ROOT,
                    env=environment,
                    stdout=output,
                    stderr=output,
                )
                processes.append(server)
                context = ssl.create_default_context(cafile=str(directory / "certificate.pem"))
                with httpx.Client(verify=context, timeout=1, trust_env=False) as client:
                    while time.monotonic() < deadline:
                        if server.poll() is not None:
                            output.flush()
                            category = build_tools().build_failure_category(
                                directory / "process.log"
                            )
                            metadata = build_tools().build_failure_metadata(
                                directory / "process.log"
                            )
                            raise RuntimeError(
                                "editor_process_exited_before_ready:"
                                + category
                                + ":"
                                + json.dumps(metadata, separators=(",", ":"))
                            )
                        try:
                            response = client.get(origin + "/readyz")
                            response.raise_for_status()
                            if (
                                response.headers.get("X-Caller-Instance")
                                != environment["CALLER_INSTANCE_ID"]
                            ):
                                raise RuntimeError("editor_response_from_unowned_process")
                            if owns_port(processes, port):
                                break
                        except httpx.HTTPError:
                            Event().wait(0.1)
                    else:
                        raise RuntimeError("editor_readiness_deadline_exceeded")
                yield {
                    "ui": origin,
                    "management": origin,
                    "runtime": f"https://127.0.0.1:{runtime_port}",
                    "certificate": str(directory / "certificate.pem"),
                    "processes": processes,
                    "sessions": json.loads((directory / "sessions.json").read_text()),
                }
            except BaseException:
                output.flush()
                log = ROOT / "code/management/test-results/editor-host.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(directory / "process.log", log)
                renderer = directory / "web/build/server/index.js"
                if renderer.is_file():
                    shutil.copyfile(renderer, log.parent / "editor-renderer.js")
                    retained = ROOT / "code/management/.web/editor-renderer"
                    shutil.copytree(renderer.parent, retained, dirs_exist_ok=True)
                raise
            finally:
                stop(processes)
                restore_frontend_lock()

                output.flush()
                log = ROOT / "code/management/test-results/editor-host.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(directory / "process.log", log)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--editor", action="store_true")
    arguments = parser.parse_args()
    if arguments.editor:
        values = build_environment()
        values.pop("VIRTUAL_ENV", None)
        prepared = subprocess.run(
            [
                "uv",
                "run",
                "--directory",
                "code/management",
                "--locked",
                "--no-sync",
                "python",
                "-c",
                "import json; from tests.conftest import configuration_graph_document; "
                "print(json.dumps(configuration_graph_document()))",
            ],
            cwd=ROOT,
            env=values,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        with running_configuration_editor(
            graph=json.loads(prepared.stdout),
            identities=("manager-one", "manager-two"),
            mode="local",
        ) as host:
            print(
                json.dumps(
                    {
                        "preview_url": host["ui"] + "/_test/login/manager-one",
                        "profile": "synthetic_local_only",
                    }
                ),
                flush=True,
            )
            host["processes"][-1].wait()
        return
    with running_apps() as urls:
        with httpx.Client(timeout=1, trust_env=False) as client:
            for service in ("runtime", "management"):
                for route in ("/healthz", "/readyz"):
                    started = time.monotonic()
                    response = client.get(urls[service] + route)
                    response.raise_for_status()
                    if time.monotonic() - started >= 1:
                        raise RuntimeError("smoke_health_latency_exceeded")
        if arguments.browser:
            environment = dict(build_environment(), CALLER_UI_URL=urls["ui"])
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--directory",
                    "code/management",
                    "--locked",
                    "pytest",
                    "tests/browser",
                    "-q",
                    "--screenshot=on",
                    "--tb=short",
                ],
                cwd=ROOT,
                env=environment,
                check=True,
                timeout=180,
            )
        print(json.dumps({"result": "local_smoke_passed", "browser": arguments.browser}))


if __name__ == "__main__":
    main()
