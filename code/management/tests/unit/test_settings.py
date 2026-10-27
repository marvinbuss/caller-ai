"""Management imports and local startup must not discover Azure credentials."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from management_app.settings import Settings


def test_local_defaults_are_frozen() -> None:
    settings = Settings()
    assert settings.mode == "local"
    assert settings.trace_destination == "memory"
    assert settings.trace_include_content is False
    with pytest.raises(ValidationError):
        settings.mode = "live"


@pytest.mark.parametrize(
    "values",
    [
        {"mode": "invalid"},
        {"trace_include_content": True},
        {"trace_destination": "https://unapproved.example"},
    ],
)
def test_invalid_mode_and_unsafe_telemetry_fail(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)


def test_local_startup_does_not_construct_live_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from management_app.management_app import create_app

    def forbidden(*arguments: object, **keywords: object) -> None:
        raise AssertionError("Live dependency constructed during local startup")

    monkeypatch.setattr("azure.identity.aio.DefaultAzureCredential", forbidden)
    monkeypatch.setattr("azure.cosmos.aio.CosmosClient", forbidden)
    with TestClient(create_app(Settings())()) as client:
        assert client.get("/readyz").status_code == 200


def test_live_startup_has_no_local_fallback() -> None:
    from management_app.management_app import create_app

    with pytest.raises(RuntimeError, match="live_mode_unavailable"):
        with TestClient(create_app(Settings(mode="live"))()):
            pytest.fail("Live startup silently fell back to local mode")


def test_external_connections_are_blocked() -> None:
    import socket

    from pytest_socket import SocketConnectBlockedError

    with socket.socket() as connection:
        with pytest.raises(SocketConnectBlockedError):
            connection.connect(("203.0.113.10", 443))


def test_preview_warmup_hook_is_registered_and_idempotent() -> None:
    from rxconfig import PreviewWarmupPlugin, config

    plugin = next(value for value in config.plugins if isinstance(value, PreviewWarmupPlugin))
    tasks = []
    plugin.pre_compile(add_modify_task=lambda path, modify: tasks.append((path, modify)))
    assert len(tasks) == 1 and tasks[0][0] == "vite.config.js"
    original = (
        "export default defineConfig(() => ({plugins: "
        "[alwaysUseReactDomServerNode(), reactRouter()]}));"
    )
    transformed = tasks[0][1](original)
    assert transformed.count("function callerPreviewWarmup()") == 1
    assert transformed.count("callerPreviewWarmup(),") == 1
    assert "async configurePreviewServer" in transformed
    assert '"build/server/index.js"' in transformed
    assert "await import" in transformed
    assert tasks[0][1](transformed) == transformed
    assert "timeout" not in transformed and "no-sandbox" not in transformed


def test_preview_warmup_rejects_changed_framework_template() -> None:
    from rxconfig import PreviewWarmupPlugin

    tasks = []
    PreviewWarmupPlugin().pre_compile(add_modify_task=lambda path, modify: tasks.append(modify))
    with pytest.raises(RuntimeError, match="frontend_preview_template_changed"):
        tasks[0]("export default unrelatedConfiguration;")


@pytest.mark.parametrize("waits_for_import", [True, False])
def test_preview_startup_awaits_real_module_before_requests(tmp_path, waits_for_import) -> None:
    import json
    import subprocess

    from rxconfig import PREVIEW_WARMUP

    entry = tmp_path / "build/server/index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("await Promise.resolve(); globalThis.callerPreviewReady = true;\n")
    source = PREVIEW_WARMUP
    if not waits_for_import:
        source = source.replace("await import(pathToFileURL", "void import(pathToFileURL")
    source += (
        "\nawait callerPreviewWarmup().configurePreviewServer({config: {root: process.argv[1]}});"
    )
    source += "\nconsole.log(JSON.stringify({ready: globalThis.callerPreviewReady === true}));"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source, str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout)["ready"] is waits_for_import
