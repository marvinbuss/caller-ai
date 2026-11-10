"""Foundry Responses API model for text turns, wrapped so the application owns every dispatch.

The adapter gives the Agents SDK a model whose calls go through admission (an attempt is recorded
before the request and settled after), use a managed-identity token, and travel over a bounded
transport: no environment proxies, no retries, identity encoding only and responses capped at
256 KiB. Failures surface as fixed codes; provider text, tokens and request bodies are never logged
or returned.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
from agents import ModelSettings, set_tracing_disabled
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import Model, ModelTracing
from agents.models.openai_responses import OpenAIResponsesModel
from agents.tool import FunctionTool, Tool
from azure.core.credentials import AccessToken
from openai import APIStatusError, AsyncOpenAI
from openai.types.responses.response_prompt_param import ResponsePromptParam

from caller_runtime.domain.conversation import AttemptIntent, TestSlot, WorkBudget, utc
from caller_runtime.observability import Telemetry
from caller_runtime.services.admission import (
    AdmissionError,
    SettlementObserver,
    TestAdmissionService,
)


class FoundryCredential(Protocol):
    async def get_token(self, *scopes: str) -> AccessToken: ...


class _BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, source: httpx2.AsyncByteStream) -> None:
        self._source, self._closed = source, False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        total = 0
        async for chunk in self._source:
            if len(chunk) > 256 * 1024 - total:
                raise ValueError("model_response_limit")
            total += len(chunk)
            yield chunk

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._source.aclose()


class _BoundedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, delegate: httpx2.AsyncBaseTransport | None) -> None:
        self._delegate = (
            delegate
            if delegate is not None
            else httpx2.AsyncHTTPTransport(
                verify=True,
                trust_env=False,
                retries=0,
            )
        )

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        # Compressed bodies could expand past the size bound after the limit check.
        request.headers["Accept-Encoding"] = "identity"
        response = await self._delegate.handle_async_request(request)
        length = response.headers.get("content-length")
        if (
            not isinstance(response.stream, httpx2.AsyncByteStream)
            or response.headers.get("content-encoding", "identity").lower() != "identity"
            or (length is not None and (not length.isdecimal() or int(length) > 256 * 1024))
        ):
            await response.aclose()
            raise ValueError("model_response_limit")
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=_BoundedStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._delegate.aclose()


def quiet_model_diagnostics() -> None:
    set_tracing_disabled(True)
    for name in tuple(logging.Logger.manager.loggerDict):
        if (
            name == "openai"
            or name.startswith("openai.")
            or name == "httpx2"
            or name.startswith("httpx2.")
        ):
            logging.getLogger(name).disabled = True


class FoundryTextAdapter:
    def __init__(
        self,
        *,
        endpoint: str,
        deployment: str,
        approved_hosts: frozenset[str],
        credential: FoundryCredential,
        admission: TestAdmissionService,
        utc_now: Callable[[], datetime],
        max_input_tokens: int,
        max_output_tokens: int,
        transport: httpx2.AsyncBaseTransport | None = None,
        settlement_observer: SettlementObserver | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        try:
            parts = urlsplit(endpoint)
            if (
                parts.scheme != "https"
                or parts.hostname not in approved_hosts
                or not parts.hostname
                or not parts.hostname.endswith((".openai.azure.com", ".services.ai.azure.com"))
                or parts.username is not None
                or parts.password is not None
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or parts.port not in {None, 443}
                or "\\" in endpoint
                or any(ord(character) < 33 for character in endpoint)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", deployment)
                or deployment == "gpt-live-1"
                or credential is None
                or admission is None
                or type(max_input_tokens) is not int
                or not 1024 <= max_input_tokens <= 131072
                or type(max_output_tokens) is not int
                or not 1 <= max_output_tokens <= 4096
            ):
                raise ValueError("invalid_binding")
        except (ValueError, TypeError):
            raise ValueError("invalid_foundry_text_binding") from None
        self.endpoint, self.deployment = endpoint.rstrip("/"), deployment
        self.credential, self.admission, self.utc_now = credential, admission, utc_now
        self.max_input_tokens, self.max_output_tokens = max_input_tokens, max_output_tokens
        self._transport, self._closed = transport, False
        self._clients: set[AsyncOpenAI] = set()
        self._settlement_observer = settlement_observer
        self._telemetry = telemetry
        self._active_operations = 0
        self._disposal_unknown = False
        self.binding_hash = hashlib.sha256(
            json.dumps(
                {"endpoint": self.endpoint, "deployment": deployment, "mode": "responses"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        quiet_model_diagnostics()

    def model_for(self, slot: TestSlot, work: WorkBudget) -> Model:
        if self._closed:
            raise PermissionError("model_unavailable")
        return _FoundryModel(self, slot, work)

    @property
    def disposed(self) -> bool:
        return (
            self._closed
            and not self._clients
            and not self._active_operations
            and not self._disposal_unknown
        )

    async def _settle(self, intent: AttemptIntent) -> None:
        reference = "model-" + uuid4().hex
        if self._settlement_observer is not None:
            self._settlement_observer.record(intent, reference)
        await self.admission.settle_attempt(intent, settlement_ref=reference)

    async def _settle_refusal(self, intent: AttemptIntent | None, error: APIStatusError) -> None:
        """A 4xx answer proves the provider finished the request; 5xx and no answer stay open."""
        if intent is not None and 400 <= error.status_code < 500:
            try:
                await self._settle(intent)
            except Exception:
                pass

    async def _dispose_client(self, client: AsyncOpenAI) -> None:
        try:
            await client.close()
            self._clients.discard(client)
        except BaseException:
            self._disposal_unknown = True
            raise

    def _settings(
        self,
        instructions: str | None,
        inputs: str | list[TResponseInputItem],
        settings: ModelSettings,
        tools: list[Tool],
        handoffs: list[Handoff[Any, Any]],
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelSettings:
        if (
            handoffs
            or previous_response_id is not None
            or conversation_id is not None
            or prompt is not None
            or len(tools) > 128
            or any(not isinstance(tool, FunctionTool) for tool in tools)
            or any(
                value is not None
                for value in (
                    settings.extra_query,
                    settings.extra_body,
                    settings.extra_headers,
                    settings.extra_args,
                    settings.metadata,
                    settings.prompt_cache_retention,
                    settings.prompt_cache_options,
                    settings.context_management,
                    settings.response_include,
                    settings.timeout,
                )
            )
        ):
            raise PermissionError("unapproved_model_request")
        if settings.retry is not None and (
            settings.retry.max_retries != 0
            or settings.retry.backoff is not None
            or settings.retry.policy is not None
        ):
            raise PermissionError("unapproved_model_request")
        descriptors = [
            {"name": tool.name, "description": tool.description, "schema": tool.params_json_schema}
            for tool in tools
            if isinstance(tool, FunctionTool)
        ]
        try:
            size = len(
                json.dumps(
                    {"instructions": instructions, "input": inputs, "tools": descriptors},
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (ValueError, TypeError, RecursionError):
            raise PermissionError("invalid_model_context") from None
        if (instructions is not None and len(instructions.encode("utf-8")) > 16 * 1024) or (
            size + 4096 + len(tools) * 1024 > self.max_input_tokens
        ):
            raise PermissionError("model_context_limit")
        return replace(
            settings,
            store=False,
            parallel_tool_calls=False,
            include_usage=True,
            max_tokens=min(settings.max_tokens or self.max_output_tokens, self.max_output_tokens),
            prompt_cache_retention=None,
            preserve_raw_usage=False,
        )

    async def _response(
        self,
        slot: TestSlot,
        work: WorkBudget,
        instructions: str | None,
        inputs: str | list[TResponseInputItem],
        settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff[Any, Any]],
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        if self._closed:
            raise PermissionError("model_unavailable")
        checked = self._settings(
            instructions,
            inputs,
            settings,
            tools,
            handoffs,
            previous_response_id,
            conversation_id,
            prompt,
        )
        client: AsyncOpenAI | None = None
        intent: AttemptIntent | None = None
        self._active_operations += 1
        try:
            await self.admission.check_work(
                slot.session_id, slot.owner_epoch, generation=slot.generation, work=work
            )
            remaining = work.remaining(utc(self.utc_now()))
            work.require(utc(self.utc_now()))
            async with asyncio.timeout(remaining):
                token = await self.credential.get_token("https://ai.azure.com/.default")
                if (
                    not token.token
                    or not math.isfinite(token.expires_on)
                    or token.expires_on <= work.work_deadline_utc.timestamp()
                ):
                    raise PermissionError("model_identity_unavailable")
                http = httpx2.AsyncClient(
                    transport=_BoundedTransport(self._transport),
                    verify=True,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx2.Timeout(remaining),
                )
                client = AsyncOpenAI(
                    api_key=token.token,
                    base_url=self.endpoint + "/openai/v1/",
                    max_retries=0,
                    http_client=http,
                    timeout=remaining,
                )
                self._clients.add(client)
                # Reasoning deployments accept function tools only on the Responses API.
                delegate = OpenAIResponsesModel(model=self.deployment, openai_client=client)
                intent = await self.admission.begin_attempt(
                    slot.session_id,
                    slot.owner_epoch,
                    kind="model",
                    binding_hash=self.binding_hash,
                    generation=slot.generation,
                    work=work,
                )

                async def request() -> ModelResponse:
                    return await delegate.get_response(
                        system_instructions=instructions,
                        input=inputs,
                        model_settings=checked,
                        tools=tools,
                        output_schema=output_schema,
                        handoffs=[],
                        tracing=ModelTracing.DISABLED,
                    )

                result = await self.admission.perform(intent, request, work=work)
                await self._settle(intent)
                if self._telemetry is not None:
                    self._telemetry.model_usage(
                        mode=slot.mode,
                        input_tokens=result.usage.input_tokens,
                        output_tokens=result.usage.output_tokens,
                    )
                return result
        except AdmissionError as error:
            raise PermissionError(error.code) from None
        except PermissionError:
            raise
        except APIStatusError as error:
            await self._settle_refusal(intent, error)
            raise PermissionError("model_unavailable") from None
        except Exception:
            raise PermissionError("model_unavailable") from None
        finally:
            try:
                if client is not None:
                    await self._dispose_client(client)
            finally:
                self._active_operations -= 1

    async def _stream(
        self,
        slot: TestSlot,
        work: WorkBudget,
        instructions: str | None,
        inputs: str | list[TResponseInputItem],
        settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff[Any, Any]],
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        if self._closed:
            raise PermissionError("model_unavailable")
        checked = self._settings(
            instructions,
            inputs,
            settings,
            tools,
            handoffs,
            previous_response_id,
            conversation_id,
            prompt,
        )
        client: AsyncOpenAI | None = None
        iterator: AsyncGenerator[TResponseStreamEvent, None] | None = None
        intent: AttemptIntent | None = None
        completed = False
        self._active_operations += 1
        try:
            await self.admission.check_output(
                slot.session_id, slot.owner_epoch, generation=slot.generation
            )
            remaining = work.remaining(utc(self.utc_now()))
            work.require(utc(self.utc_now()))
            async with asyncio.timeout(remaining):
                token = await self.credential.get_token("https://ai.azure.com/.default")
                if not token.token or token.expires_on <= work.work_deadline_utc.timestamp():
                    raise PermissionError("model_identity_unavailable")
                http = httpx2.AsyncClient(
                    transport=_BoundedTransport(self._transport),
                    verify=True,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx2.Timeout(remaining),
                )
                client = AsyncOpenAI(
                    api_key=token.token,
                    base_url=self.endpoint + "/openai/v1/",
                    max_retries=0,
                    http_client=http,
                    timeout=remaining,
                )
                self._clients.add(client)
                delegate = OpenAIResponsesModel(model=self.deployment, openai_client=client)
                intent = await self.admission.begin_attempt(
                    slot.session_id,
                    slot.owner_epoch,
                    kind="model",
                    binding_hash=self.binding_hash,
                    generation=slot.generation,
                    work=work,
                )
                iterator = cast(
                    AsyncGenerator[TResponseStreamEvent, None],
                    delegate.stream_response(
                        system_instructions=instructions,
                        input=inputs,
                        model_settings=checked,
                        tools=tools,
                        output_schema=output_schema,
                        handoffs=[],
                        tracing=ModelTracing.DISABLED,
                    ),
                )

                async def first() -> TResponseStreamEvent:
                    assert iterator is not None
                    return await anext(iterator)

                event = await self.admission.perform(intent, first, work=work)
                output_bytes = 0
                while True:
                    work.require(utc(self.utc_now()))
                    await self.admission.check_output(
                        slot.session_id, slot.owner_epoch, generation=slot.generation
                    )
                    if event.type == "response.output_text.delta":
                        output_bytes += len(event.delta.encode("utf-8"))
                        if output_bytes > 64 * 1024:
                            raise PermissionError("model_output_limit")
                    if event.type == "response.completed":
                        completed = True
                        await iterator.aclose()
                        await self._settle(intent)
                        if self._telemetry is not None:
                            usage = event.response.usage
                            self._telemetry.model_usage(
                                mode=slot.mode,
                                input_tokens=usage.input_tokens if usage is not None else None,
                                output_tokens=usage.output_tokens if usage is not None else None,
                            )
                        yield event
                        return
                    yield event
                    event = await anext(iterator)
        except AdmissionError as error:
            raise PermissionError(error.code) from None
        except (GeneratorExit, asyncio.CancelledError):
            raise
        except PermissionError:
            raise
        except APIStatusError as error:
            await self._settle_refusal(intent, error)
            raise PermissionError("model_unavailable") from None
        except Exception:
            raise PermissionError("model_unavailable") from None
        finally:
            try:
                if iterator is not None and not completed:
                    await iterator.aclose()
            except BaseException:
                self._disposal_unknown = True
                raise
            finally:
                try:
                    if client is not None:
                        await self._dispose_client(client)
                finally:
                    self._active_operations -= 1

    async def close(self) -> None:
        self._closed = True
        clients = tuple(self._clients)
        for client in clients:
            await self._dispose_client(client)


class _FoundryModel(Model):
    def __init__(self, adapter: FoundryTextAdapter, slot: TestSlot, work: WorkBudget) -> None:
        self._adapter, self._slot, self._work = adapter, slot, work

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff[Any, Any]],
        tracing: ModelTracing,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: ResponsePromptParam | None = None,
    ) -> ModelResponse:
        return await self._adapter._response(
            self._slot,
            self._work,
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            previous_response_id,
            conversation_id,
            prompt,
        )

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff[Any, Any]],
        tracing: ModelTracing,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: ResponsePromptParam | None = None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        return self._adapter._stream(
            self._slot,
            self._work,
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            previous_response_id,
            conversation_id,
            prompt,
        )
