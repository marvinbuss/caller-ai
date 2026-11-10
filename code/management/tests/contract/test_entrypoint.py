"""The management entrypoint: local mode unchanged, live mode composes at lifespan startup."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from management_app.live import LiveManagementApplication
from management_app.management_app import create_app_from_environment
from management_app.settings import Settings, StartupError
from tests.unit.test_live_settings import complete


class Managed:
    """A stand-in composition that answers lifespan and HTTP like the real one."""

    def __init__(self) -> None:
        self.stopped = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                else:
                    self.stopped = True
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"composed"})


def drive(application: LiveManagementApplication) -> dict[str, Any]:
    result: dict[str, Any] = {}

    async def request() -> int:
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await application({"type": "http", "path": "/", "headers": []}, receive, send)
        return int(sent[0]["status"])

    async def scenario() -> None:
        inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        outbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        task = asyncio.create_task(application({"type": "lifespan"}, inbound.get, outbound.put))
        result["before"] = await request()
        await inbound.put({"type": "lifespan.startup"})
        result["startup"] = await outbound.get()
        if result["startup"]["type"] == "lifespan.startup.complete":
            result["during"] = await request()
            await inbound.put({"type": "lifespan.shutdown"})
            result["shutdown"] = await outbound.get()
        await task
        result["after"] = await request()

    asyncio.run(scenario())
    return result


def test_the_application_composes_inside_lifespan_and_serves_only_while_composed() -> None:
    managed = Managed()

    async def build(settings: Settings) -> Any:
        return managed

    result = drive(LiveManagementApplication(Settings(mode="live"), build=build))
    assert result["before"] == 503 and result["during"] == 200 and result["after"] == 503
    assert result["startup"] == {"type": "lifespan.startup.complete"}
    assert result["shutdown"] == {"type": "lifespan.shutdown.complete"} and managed.stopped


@pytest.mark.parametrize("code", ["fingerprint_mismatch", "review_missing", "key_invalid"])
def test_a_refused_composition_fails_startup_with_the_one_fixed_code(code: str) -> None:
    async def build(settings: Settings) -> Any:
        raise StartupError(code)

    result = drive(LiveManagementApplication(Settings(mode="live"), build=build))
    assert result["startup"] == {"type": "lifespan.startup.failed", "message": code}
    assert result["after"] == 503


def test_an_unexpected_failure_never_leaks_its_text() -> None:
    async def build(settings: Settings) -> Any:
        raise RuntimeError("synthetic-private-detail")

    result = drive(LiveManagementApplication(Settings(mode="live"), build=build))
    assert result["startup"]["message"] == "store_unavailable"
    assert "synthetic-private-detail" not in str(result)


def test_local_mode_keeps_the_existing_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in complete():
        monkeypatch.delenv(name, raising=False)
    assert not isinstance(create_app_from_environment(), LiveManagementApplication)


def test_live_mode_returns_the_composing_application(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLER_MODE", "live")
    application = create_app_from_environment()
    assert isinstance(getattr(application, "__wrapped__", None), LiveManagementApplication)


def test_live_mode_takes_the_scheme_from_the_ingress(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["scheme"])

    monkeypatch.setenv("CALLER_MODE", "live")
    monkeypatch.setattr("management_app.live.LiveManagementApplication", lambda settings: inner)
    application = create_app_from_environment()
    for kind, scheme in (("http", "http"), ("websocket", "ws")):
        scope = {
            "type": kind,
            "scheme": scheme,
            "client": ("100.100.0.7", 41000),
            "headers": [(b"x-forwarded-proto", b"https"), (b"x-forwarded-for", b"203.0.113.9")],
        }
        asyncio.run(application(scope, None, None))
    assert seen == ["https", "wss"]


def test_the_image_starts_the_factory_entrypoint_through_the_start_script() -> None:
    root = Path(__file__).parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "management_app.start" in dockerfile
    script = (root / "management_app" / "start.py").read_text(encoding="utf-8")
    assert "management_app.management_app:create_app_from_environment" in script
    assert '"--factory"' in script
