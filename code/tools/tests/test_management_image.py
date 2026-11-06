"""Spike S-12: the management image follows the address it is started with and ships every page.

Reflex bakes the backend address into the exported frontend, but the address of a deployment exists
only after the apply. The image therefore exports with a placeholder and rewrites it at start from
`API_URL`. This test starts the image with two different values and checks the served frontend
follows each, and that the export contains the configuration editor and the test console pages.
It needs a Linux container engine and builds the image unless `CALLER_MANAGEMENT_IMAGE` names one;
set `CALLER_IMAGE_TESTS=1` to run it.
"""

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
PLACEHOLDER = "caller-api-origin.invalid"

pytestmark = pytest.mark.skipif(
    os.environ.get("CALLER_IMAGE_TESTS") != "1" or shutil.which("docker") is None,
    reason="set CALLER_IMAGE_TESTS=1 with a Linux container engine to run image tests",
)


def docker(*arguments: str, timeout: int = 1800) -> str:
    result = subprocess.run(
        ["docker", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture(scope="module")
def image() -> Iterator[str]:
    named = os.environ.get("CALLER_MANAGEMENT_IMAGE")
    if named:
        yield named
        return
    tag = f"caller-management:s12-{uuid4().hex[:12]}"
    docker("build", "--tag", tag, "code/management")
    try:
        yield tag
    finally:
        subprocess.run(["docker", "rmi", "--force", tag], capture_output=True, check=False)


def start(image: str, origin: str | None) -> tuple[str, int]:
    arguments = ["run", "--detach", "--publish", "127.0.0.1::3000"]
    if origin is not None:
        arguments += ["--env", f"API_URL={origin}"]
    container = docker(*arguments, image)
    port = int(docker("port", container, "3000/tcp").splitlines()[0].rsplit(":", 1)[1])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                return container, port
        except httpx.HTTPError:
            time.sleep(0.5)
    docker("rm", "--force", container)
    raise TimeoutError("management_image_not_ready")


def event_asset(container: str) -> str:
    """The exported file that holds the socket address, relative to the served root."""
    script = (
        "import pathlib\n"
        "root = pathlib.Path('/app/.web/build/client')\n"
        "print(next(str(p.relative_to(root)) for p in root.rglob('*.js') if '/_event' in p.read_text()))\n"
    )
    return docker("exec", container, "/app/.venv/bin/python", "-c", script)


def socket_form(origin: str) -> str:
    return origin.replace("https://", "wss://").replace("http://", "ws://")


@pytest.mark.parametrize("origin", ["https://one.example.net", "http://127.0.0.1:3999"])
def test_the_frontend_follows_the_address_the_image_is_started_with(
    image: str, origin: str
) -> None:
    container, port = start(image, origin)
    try:
        asset = event_asset(container).replace("\\", "/")
        served = httpx.get(f"http://127.0.0.1:{port}/{asset}", timeout=5)
        assert served.status_code == 200
        assert f"{socket_form(origin)}/_event" in served.text
        assert PLACEHOLDER not in served.text
    finally:
        docker("rm", "--force", container)


def test_an_address_that_is_not_a_plain_origin_stops_the_start(image: str) -> None:
    container = docker("run", "--detach", "--env", "API_URL=https://x.example.net/<script>", image)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if docker("inspect", "--format", "{{.State.Status}}", container) == "exited":
                break
            time.sleep(0.5)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", container) == "2"
        assert "frontend_origin_invalid" in subprocess.run(
            ["docker", "logs", container], capture_output=True, text=True, check=False
        ).stderr
    finally:
        docker("rm", "--force", container)


def test_the_export_contains_the_editor_and_test_console_pages(image: str) -> None:
    container = docker("create", image)
    try:
        routes = json.loads(
            docker("run", "--rm", "--entrypoint", "cat", image, "/app/.web/routes.json")
        )
    finally:
        docker("rm", "--force", container)
    assert "index" in routes
    assert any(re.fullmatch(r"configuration", route) for route in routes)
    assert any(route.startswith("configuration/test") for route in routes)
    assert any("community_id" in route or "communities" in route for route in routes)
