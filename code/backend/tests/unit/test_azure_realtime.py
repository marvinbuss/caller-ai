"""Azure realtime protocol boundary with no credential discovery or network calls."""

import asyncio
import time
from urllib.parse import urlunsplit
from uuid import uuid4

import pytest
from azure.core.credentials import AccessToken


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class CredentialDouble:
    def __init__(self):
        self.calls = []
        self.tokens = [
            AccessToken("synthetic-token-one", 9999999999),
            AccessToken("synthetic-token-two", 9999999999),
        ]

    async def get_token(self, *scopes, **keywords):
        self.calls.append(scopes)
        return self.tokens[min(len(self.calls) - 1, len(self.tokens) - 1)]


class TransportDouble:
    def __init__(self, *, fail=False, block=False):
        self.fail = fail
        self.block = block
        self.options = []
        self.listeners = []
        self.sent = []
        self.closed = 0

    async def connect(self, options):
        self.options.append(options)
        if self.block:
            await asyncio.Event().wait()
        if self.fail:
            raise OSError("sensitive-transport-details")

    def add_listener(self, listener):
        self.listeners.append(listener)

    def remove_listener(self, listener):
        self.listeners.remove(listener)

    async def send_event(self, event):
        self.sent.append(event)

    async def send_event_if(self, event, send_if):
        if send_if():
            self.sent.append(event)
            return True
        return False

    async def close(self):
        self.closed += 1


def adapter(credential=None, factory=None, **values):
    from caller_runtime.adapters.azure_realtime import AzureRealtimeModel

    return AzureRealtimeModel(
        endpoint="https://approved-fixture.openai.azure.com",
        deployment="realtime-fixture",
        credential=credential or CredentialDouble(),
        transport_factory=factory or TransportDouble,
        **values,
    )


def unsafe_userinfo_endpoint() -> str:
    return urlunsplit(
        ("https", f"{uuid4().hex}:{uuid4().hex}@approved-fixture.openai.azure.com", "", "", "")
    )


@pytest.mark.anyio
async def test_ga_url_and_explicit_managed_identity_headers() -> None:
    credential, transport = CredentialDouble(), TransportDouble()
    model = adapter(credential, lambda: transport)
    await model.connect({"initial_model_settings": {"tracing": None}})
    try:
        options = transport.options[0]
        assert (
            options["url"]
            == "wss://approved-fixture.openai.azure.com/openai/v1/realtime?model=realtime-fixture"
        )
        assert options["headers"] == {"Authorization": "Bearer synthetic-token-one"}
        assert credential.calls == [("https://ai.azure.com/.default",)]
        assert options["initial_model_settings"]["tracing"] is None
        assert "api_key" not in options and "api-version" not in options["url"]
        assert "synthetic-token-one" not in repr(model)
    finally:
        await model.close()
    assert transport.closed == 1


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://approved-fixture.openai.azure.com",
        "https://api.openai.com",
        "https://approved-fixture.openai.azure.com/openai/realtime",
        "https://approved-fixture.openai.azure.com?api-key=synthetic",
        unsafe_userinfo_endpoint(),
        "https://approved-fixture.openai.azure.com:8443",
    ],
    ids=["http", "public_openai", "preview", "query", "userinfo", "alternate_port"],
)
def test_non_ga_or_unsafe_endpoint_is_rejected(endpoint) -> None:
    from caller_runtime.adapters.azure_realtime import AzureRealtimeModel

    with pytest.raises(ValueError):
        AzureRealtimeModel(
            endpoint=endpoint,
            deployment="fixture",
            credential=CredentialDouble(),
            transport_factory=TransportDouble,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "options",
    [
        {"url": "wss://api.openai.com"},
        {"headers": {"api-key": "synthetic"}},
        {"api_key": str(uuid4())},
        {"call_id": "existing-call"},
    ],
)
async def test_caller_cannot_override_endpoint_identity_or_attach_existing_call(options) -> None:
    credential = CredentialDouble()
    model = adapter(credential)
    with pytest.raises(ValueError):
        await model.connect(options)
    assert credential.calls == []


@pytest.mark.anyio
async def test_retry_refreshes_token_and_cleans_failed_transport() -> None:
    transports = [TransportDouble(fail=True), TransportDouble()]
    pending = iter(transports)
    credential = CredentialDouble()
    model = adapter(credential, lambda: next(pending))
    await model.connect({})
    try:
        assert len(credential.calls) == 2
        assert transports[0].options[0]["headers"]["Authorization"] == "Bearer synthetic-token-one"
        assert transports[1].options[0]["headers"]["Authorization"] == "Bearer synthetic-token-two"
        assert transports[0].closed == 1
    finally:
        await model.close()


@pytest.mark.anyio
async def test_expired_token_is_not_sent_and_diagnostics_are_safe() -> None:
    credential = CredentialDouble()
    credential.tokens = [AccessToken("expired-sensitive-token", 1)]
    transport = TransportDouble()
    model = adapter(credential, lambda: transport)
    with pytest.raises(RuntimeError, match="azure_realtime_connection_failed") as failure:
        await model.connect({})
    assert "expired-sensitive-token" not in str(failure.value)
    assert transport.options == []


@pytest.mark.anyio
async def test_one_operation_budget_cancels_blocked_connect() -> None:
    transport = TransportDouble(block=True)
    model = adapter(factory=lambda: transport, connection_budget=0.05)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="azure_realtime_connection_failed"):
        await model.connect({})
    assert time.monotonic() - started < 0.5
    assert len(transport.options) == 1 and transport.closed == 1


@pytest.mark.anyio
async def test_real_sdk_session_uses_azure_wrapper_without_network() -> None:
    from agents.realtime import RealtimeAgent, RealtimeRunner
    from agents.realtime.model_inputs import RealtimeModelSendUserInput

    transport = TransportDouble()
    model = adapter(factory=lambda: transport)
    runner = RealtimeRunner(
        RealtimeAgent(name="azure-sdk-fixture", instructions="Synthetic only"),
        model=model,
        config={"tracing_disabled": True, "model_settings": {"tracing": None}},
    )
    async with await runner.run() as session:
        await session.send_message("synthetic-input")
        assert isinstance(transport.sent[-1], RealtimeModelSendUserInput)
        assert transport.sent[-1].user_input == "synthetic-input"
        assert transport.listeners == [session]
        assert transport.options[0]["url"].startswith(
            "wss://approved-fixture.openai.azure.com/openai/v1/"
        )
    assert transport.listeners == [] and transport.closed == 1


def test_malformed_endpoint_is_rejected_without_echoing_fragments() -> None:
    from caller_runtime.adapters.azure_realtime import AzureRealtimeModel

    with pytest.raises(ValueError, match="unapproved_azure_realtime_endpoint") as failure:
        AzureRealtimeModel(
            endpoint="https://approved-fixture.openai.azure.com:synthetic-sensitive-port",
            deployment="fixture",
            credential=CredentialDouble(),
            transport_factory=TransportDouble,
        )
    assert "synthetic-sensitive-port" not in str(failure.value)
