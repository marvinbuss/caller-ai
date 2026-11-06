"""Build and exercise real Linux images; never substitute native source servers.

The script builds the backend and management images, checks that each runs as the non-root user
with no build tools and works with the network disabled, starts both on free local ports with a
run-unique label, and verifies `/healthz` and `/readyz` against the OpenAPI contract, including
the `X-Caller-Instance` header so a response from someone else's container cannot pass. The
browser shell test then runs against the management image. Every container it created is removed
at the end, and a failed removal fails the run.
"""

import json
import subprocess
import time
from threading import Event
from uuid import uuid4

import httpx
import jsonschema
import psutil
from ci import environment
from smoke import ROOT, free_ports, stop

CONTRACT = json.loads((ROOT / "code/contracts/foundation.openapi.json").read_text())


def docker(*arguments: str) -> str:
    result = subprocess.run(
        ["docker", *arguments],
        cwd=ROOT,
        env=environment(),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    return result.stdout.strip()


def create(image: str, host_port: int, internal_port: int, instance: str) -> str:
    return docker(
        "create",
        "--label",
        f"caller-ai.test-run={instance}",
        "--publish",
        f"127.0.0.1:{host_port}:{internal_port}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--env",
        "CALLER_MODE=local",
        "--env",
        f"CALLER_INSTANCE_ID={instance}",
        image,
    )


def cleanup(containers: list[str]) -> None:
    failures = []
    for container in reversed(containers):
        try:
            docker("rm", "--force", container)
        except subprocess.SubprocessError:
            failures.append(container)
    if failures:
        raise RuntimeError("owned_image_container_cleanup_failed")


def verify_response(response: httpx.Response, instance: str, service: str) -> None:
    response.raise_for_status()
    if response.headers.get("X-Caller-Instance") != instance:
        raise RuntimeError("image_response_from_unowned_container")
    schema = "Readiness" if response.request.url.path == "/readyz" else "Health"
    jsonschema.validate(
        response.json(),
        {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]},
    )
    body = response.json()
    if body["service"] != service or body["mode"] != "local":
        raise RuntimeError("image_health_identity_invalid")
    if schema == "Readiness" and (
        body["status"] != "ready"
        or body["capabilities"]
        != {
            "live_calls": False,
            "management_configuration": False,
        }
    ):
        raise RuntimeError("image_readiness_contract_invalid")


def wait_ready(urls: dict[str, str], containers: list[str], instance: str) -> None:
    deadline = time.monotonic() + 120
    with httpx.Client(timeout=1, trust_env=False) as client:
        while time.monotonic() < deadline:
            for container in containers:
                if docker("inspect", "--format", "{{.State.Status}}", container) != "running":
                    raise RuntimeError("image_exited_before_ready")
            try:
                for service, url in urls.items():
                    verify_response(client.get(url + "/readyz"), instance, service)
                return
            except httpx.HTTPError:
                Event().wait(0.1)
    raise TimeoutError("image_readiness_deadline_exceeded")


def offline(image: str, service: str) -> None:
    source = """
import os, shutil
from fastapi.testclient import TestClient
assert os.getuid() == 10001
assert all(shutil.which(tool) is None for tool in ('uv', 'node', 'bun'))
"""
    source += (
        "from caller_runtime.main import app\nclient = TestClient(app)\n"
        if service == "caller-runtime"
        else "from management_app.management_app import app\nclient = TestClient(app())\n"
    )
    source += """
with client:
    assert client.get('/readyz').status_code == 200
    assert client.get('/healthz').status_code == 200
"""
    docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "/app/.venv/bin/python",
        image,
        "-c",
        source,
    )


def verify_browser(url: str) -> None:
    values = environment()
    values["CALLER_UI_URL"] = url
    command = [
        "uv",
        "run",
        "--directory",
        "code/management",
        "--locked",
        "pytest",
        "tests/browser/test_shell.py",
        "-q",
        "--screenshot=on",
        "--tb=short",
    ]
    process = psutil.Popen(command, cwd=ROOT, env=values)
    try:
        code = process.wait(timeout=180)
        if code != 0:
            raise subprocess.CalledProcessError(code, command)
    except (subprocess.TimeoutExpired, psutil.TimeoutExpired):
        raise RuntimeError("image_browser_deadline_exceeded") from None
    finally:
        stop([process])


def verify_start_address(image: str) -> None:
    """Spike S-12: the exported frontend follows `API_URL` at start and ships every page."""
    values = environment()
    values["CALLER_IMAGE_TESTS"] = "1"
    values["CALLER_MANAGEMENT_IMAGE"] = image
    command = [
        "uv",
        "run",
        "--directory",
        "code/tools",
        "--locked",
        "pytest",
        "tests/test_management_image.py",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    process = psutil.Popen(command, cwd=ROOT, env=values)
    try:
        code = process.wait(timeout=300)
        if code != 0:
            raise subprocess.CalledProcessError(code, command)
    except (subprocess.TimeoutExpired, psutil.TimeoutExpired):
        raise RuntimeError("image_start_address_deadline_exceeded") from None
    finally:
        stop([process])


def main() -> None:
    if docker("info", "--format", "{{.OSType}}") != "linux":
        raise RuntimeError("linux_container_engine_required")
    runtime_port, management_port = free_ports(2)
    instance = uuid4().hex
    runtime = f"caller-runtime:smoke-{instance}"
    management = f"caller-management:smoke-{instance}"
    urls = {
        "caller-runtime": f"http://127.0.0.1:{runtime_port}",
        "management": f"http://127.0.0.1:{management_port}",
    }
    containers: list[str] = []
    images: list[str] = []
    try:
        docker("build", "--tag", runtime, "code/backend")
        images.append(runtime)
        docker(
            "build",
            "--tag",
            management,
            "--build-arg",
            f"PUBLIC_API_URL={urls['management']}",
            "code/management",
        )
        images.append(management)
        for image, service in ((runtime, "caller-runtime"), (management, "management")):
            offline(image, service)
            if docker("image", "inspect", "--format", "{{.Config.User}}", image) != "10001:10001":
                raise RuntimeError("nonroot_image_required")
        for image, host, internal in (
            (runtime, runtime_port, 8000),
            (management, management_port, 3000),
        ):
            container = create(image, host, internal, instance)
            containers.append(container)
            docker("start", container)
        wait_ready(urls, containers, instance)
        with httpx.Client(timeout=1, trust_env=False) as client:
            for service, url in urls.items():
                for route in ("/healthz", "/readyz"):
                    started = time.monotonic()
                    verify_response(client.get(url + route), instance, service)
                    if time.monotonic() - started >= 1:
                        raise RuntimeError("image_health_latency_exceeded")
            client.get(urls["management"] + "/").raise_for_status()
        verify_browser(urls["management"])
        verify_start_address(management)
        print(
            json.dumps(
                {
                    "result": "actual_image_smoke_passed",
                    "engine": "linux",
                    "offline_startup": True,
                    "frontend_follows_start_address": True,
                    "browser_widths": [360, 768, 1440],
                }
            )
        )
    finally:
        try:
            cleanup(containers)
        finally:
            for image in reversed(images):
                docker("image", "rm", image)


if __name__ == "__main__":
    main()
