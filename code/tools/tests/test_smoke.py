"""Owned-process and readiness-identity regressions for the local smoke harness."""

import importlib.util
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import psutil
import pytest

SPECIFICATION = importlib.util.spec_from_file_location(
    "caller_smoke",
    Path(__file__).resolve().parents[1] / "scripts/smoke.py",
)
assert SPECIFICATION is not None and SPECIFICATION.loader is not None
SMOKE = importlib.util.module_from_spec(SPECIFICATION)
sys.modules[SPECIFICATION.name] = SMOKE
SPECIFICATION.loader.exec_module(SMOKE)


def test_smoke_loads_local_settings_without_shell_activation(monkeypatch, tmp_path):
    monkeypatch.setattr(SMOKE, "ROOT", tmp_path)
    for key in (
        "UV_DEFAULT_INDEX",
        "NPM_CONFIG_REGISTRY",
        "BUN_CONFIG_DEFAULT_REGISTRY",
        "CI",
        "GITHUB_ACTIONS",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = tmp_path / "code/.local/feeds.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '[environment]\nUV_DEFAULT_INDEX = "https://mirror.example.invalid/simple"\n'
        'NPM_CONFIG_REGISTRY = "https://mirror.example.invalid/npm/"\n'
    )
    values = SMOKE.build_environment()
    assert values["UV_DEFAULT_INDEX"] == "https://mirror.example.invalid/simple"
    assert values["NPM_CONFIG_REGISTRY"] == "https://mirror.example.invalid/npm/"
    assert values["UV_NO_SYNC"] == "1"
    monkeypatch.setenv("BUN_CONFIG_DEFAULT_REGISTRY", "https://explicit.example.invalid/bun/")
    assert (
        SMOKE.build_environment()["BUN_CONFIG_DEFAULT_REGISTRY"]
        == "https://explicit.example.invalid/bun/"
    )


def test_allocated_ports_are_distinct_and_released() -> None:
    ports = SMOKE.free_ports(3)
    assert len(set(ports)) == 3
    listeners = []
    try:
        for port in ports:
            listener = socket.socket()
            listeners.append(listener)
            listener.bind(("127.0.0.1", port))
    finally:
        for listener in listeners:
            listener.close()


def test_foreign_application_cannot_satisfy_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "ready"},
            headers={"X-Caller-Instance": "unowned"},
        )

    client = httpx.Client(transport=httpx.MockTransport(response))
    monkeypatch.setattr(SMOKE.httpx, "Client", lambda **arguments: client)
    with pytest.raises(RuntimeError, match="smoke_response_from_unowned_process"):
        SMOKE.wait_ready(
            {"runtime": "http://127.0.0.1:1", "management": "http://127.0.0.1:2"},
            "owned",
            [SimpleNamespace(poll=lambda: None)],
        )


def test_early_exit_is_not_ready() -> None:
    with pytest.raises(RuntimeError, match="smoke_application_exited_before_ready"):
        SMOKE.wait_ready({}, "owned", [SimpleNamespace(poll=lambda: 1)])


def test_unowned_frontend_port_is_rejected() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert SMOKE.owns_port([], port) is False
        assert SMOKE.owns_port([psutil.Process()], port) is True


def test_process_tree_is_cleaned(tmp_path: Path) -> None:
    marker = tmp_path / "child-pid.txt"
    source = (
        "import pathlib,subprocess,sys; from threading import Event; "
        "child=subprocess.Popen([sys.executable,'-c',"
        "'from threading import Event; Event().wait()']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); Event().wait()"
    )
    process = psutil.Popen([sys.executable, "-c", source, str(marker)])
    try:
        from threading import Event
        from time import monotonic

        deadline = monotonic() + 10
        while not marker.exists() and monotonic() < deadline:
            Event().wait(0.01)
        assert marker.exists()
        child = psutil.Process(int(marker.read_text()))
        SMOKE.stop([process])
        assert not process.is_running()
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        SMOKE.stop([process])


@pytest.fixture
def images(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    specification = importlib.util.spec_from_file_location(
        "caller_images",
        Path(__file__).resolve().parents[1] / "scripts/test_images.py",
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_image_creation_is_loopback_and_hardened(images, monkeypatch: pytest.MonkeyPatch) -> None:
    commands = []
    monkeypatch.setattr(
        images, "docker", lambda *arguments: commands.append(arguments) or "owned-id"
    )
    assert images.create("candidate", 12345, 3000, "a" * 32) == "owned-id"
    command = commands[0]
    assert command[0] == "create"
    assert command[command.index("--publish") + 1] == "127.0.0.1:12345:3000"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges:true"
    assert "--privileged" not in command
    assert "CALLER_INSTANCE_ID=" + "a" * 32 in command


def test_editor_build_module_override_survives_frontend_reinitialization(monkeypatch, tmp_path):
    commands = []
    restored = []
    child = SimpleNamespace(wait=lambda **kwargs: 1)

    def spawn(command, **options):
        commands.append(options["env"])
        assert options["stdin"] == SMOKE.subprocess.DEVNULL
        return child

    monkeypatch.setattr(SMOKE, "ROOT", tmp_path)
    monkeypatch.setattr(SMOKE, "free_ports", lambda count: [12345])
    monkeypatch.setattr(SMOKE.psutil, "Popen", spawn)
    monkeypatch.setattr(SMOKE, "stop", lambda processes: None)
    monkeypatch.setattr(SMOKE, "restore_frontend_lock", lambda: restored.append(True))
    with pytest.raises(RuntimeError, match="editor_frontend_build_failed"):
        with SMOKE.running_configuration_editor(
            graph={}, identities=("manager-one",), mode="local"
        ):
            pytest.fail("failed_editor_compile_was_accepted")
    assert len(commands) == 1
    assert commands[0]["APP_MODULE_IMPORT"] == "tests.editor_app"
    assert restored == [True]


def test_editor_build_failure_reports_only_safe_category(monkeypatch, tmp_path):
    candidate = "sensitive-editor-fixture-must-not-leak"
    child = SimpleNamespace(wait=lambda **options: 1)

    def spawn(*arguments, **options):
        options["stdout"].write("Prerender: Request failed for /: timeout\n" + candidate)
        return child

    monkeypatch.setattr(SMOKE, "ROOT", tmp_path)
    monkeypatch.setattr(SMOKE, "free_ports", lambda count: [12345])
    monkeypatch.setattr(SMOKE.psutil, "Popen", spawn)
    monkeypatch.setattr(SMOKE, "stop", lambda processes: None)
    monkeypatch.setattr(SMOKE, "restore_frontend_lock", lambda: None)
    with pytest.raises(
        RuntimeError, match="editor_frontend_build_failed:frontend_prerender_failed"
    ) as rejected:
        with SMOKE.running_configuration_editor(
            graph={}, identities=("manager-one",), mode="local"
        ):
            pytest.fail("failed_build_was_accepted")
    assert candidate not in str(rejected.value)


def test_image_cleanup_targets_owned_ids_only(images, monkeypatch: pytest.MonkeyPatch) -> None:
    commands = []
    monkeypatch.setattr(images, "docker", lambda *arguments: commands.append(arguments) or "")
    images.cleanup(["owned-one", "owned-two"])
    assert commands == [("rm", "--force", "owned-two"), ("rm", "--force", "owned-one")]


@pytest.mark.parametrize("outcome", ["pass", "fail", "timeout"])
def test_image_browser_profile_and_owned_cleanup(images, monkeypatch, outcome):
    commands, cleaned = [], []

    class Child:
        def wait(self, *, timeout):
            assert timeout == 180
            if outcome == "timeout":
                raise images.subprocess.TimeoutExpired("owned-image-browser", timeout)
            return 1 if outcome == "fail" else 0

    child = Child()

    def spawn(command, **options):
        commands.append(command)
        assert options["env"]["CALLER_UI_URL"] == "http://127.0.0.1:12345"
        return child

    monkeypatch.setattr(images.psutil, "Popen", spawn)
    monkeypatch.setattr(images, "stop", lambda processes: cleaned.extend(processes))
    if outcome == "pass":
        images.verify_browser("http://127.0.0.1:12345")
    else:
        with pytest.raises((RuntimeError, images.subprocess.CalledProcessError)):
            images.verify_browser("http://127.0.0.1:12345")
    assert cleaned == [child]
    assert "tests/browser/test_shell.py" in commands[0]
    assert "tests/browser" not in commands[0]
    assert not any("test_configuration_editor" in value for value in commands[0])


def test_image_probe_rejects_foreign_response(images) -> None:
    response = httpx.Response(
        200,
        json={"status": "ready"},
        headers={"X-Caller-Instance": "foreign"},
        request=httpx.Request("GET", "http://127.0.0.1:12345/readyz"),
    )
    with pytest.raises(RuntimeError, match="image_response_from_unowned_container"):
        images.verify_response(response, "owned", "management")


def test_image_command_decodes_utf8_explicitly(images, monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(arguments, **keywords):
        assert keywords["encoding"] == "utf-8"
        assert keywords["errors"] == "replace"
        return SimpleNamespace(stdout="docker-result\n")

    monkeypatch.setattr(images.subprocess, "run", execute)
    assert images.docker("version") == "docker-result"
