"""Bounded conversation capability dispatch through the authenticated management authority."""

import asyncio
import hashlib
import json
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from jsonschema import Draft202012Validator

from caller_runtime.adapters.azure_realtime import CredentialPort
from caller_runtime.adapters.configuration_client import unique_object
from caller_runtime.domain.configuration import RevisionSnapshot
from caller_runtime.domain.conversation import AttemptIntent, TestSlot, WorkBudget
from caller_runtime.services.test_admission import SettlementObserver, TestAdmissionService


def reject_constant(value: str) -> Any:
    raise ValueError("nonfinite_capability_response")


class ConversationCapabilityClient:
    def __init__(
        self,
        *,
        endpoint: str,
        approved_hosts: frozenset[str],
        audience: str,
        credential: CredentialPort,
        admission: TestAdmissionService,
        utc_now: Callable[[], datetime],
        transport: httpx.AsyncBaseTransport | None = None,
        settlement_observer: SettlementObserver | None = None,
    ) -> None:
        try:
            parts = urlsplit(endpoint)
            if (
                parts.scheme != "https"
                or parts.hostname not in approved_hosts
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or parts.port not in {None, 443}
                or "\\" in endpoint
                or any(ord(character) < 33 for character in endpoint)
                or credential is None
                or admission is None
                or utc_now is None
            ):
                raise ValueError("unapproved_origin")
            self._audience = str(UUID(audience))
        except (ValueError, TypeError):
            raise ValueError("invalid_conversation_capability_binding") from None
        self._url = endpoint.rstrip("/") + "/v1/runtime/capabilities/execute"
        self._credential, self._admission, self._utc_now = credential, admission, utc_now
        self._transport = transport
        self._settlement_observer = settlement_observer

    async def _settle(self, intent: AttemptIntent, reference: str) -> None:
        if self._settlement_observer is not None:
            self._settlement_observer.record(intent, reference)
        await self._admission.settle_attempt(intent, settlement_ref=reference)

    @staticmethod
    def _select(
        snapshot: RevisionSnapshot,
        slot: TestSlot,
        tool_id: str,
        action_id: str,
        inputs: dict[str, Any],
    ) -> set[str]:
        try:
            graph = snapshot.for_runtime()
            agent = next(
                agent for agent in graph["agents"] if agent["agentId"] == slot.active_agent_id
            )
            if (
                agent["role"] != "ordinary"
                or agent["enabled"] is not True
                or not any(
                    community["communityId"] == slot.active_community_id
                    and community["enabled"] is True
                    for community in graph["communities"]
                )
            ):
                raise ValueError("denied")
            grant = next(
                grant
                for grant in agent["capabilityAssignments"]
                if grant["agentId"] == agent["agentId"]
                and grant["toolId"] == tool_id
                and action_id in grant["permittedActionNames"]
            )
            tool = next(tool for tool in graph["tools"] if tool["toolId"] == tool_id)
            if (
                tool["enabled"] is not True
                or tool["effect"] != "read_only"
                or tool["classification"] != "public"
            ):
                raise ValueError("denied")
            schemas = [tool["inputSchema"], grant["allowedInputConstraints"]]
            output = set(tool["outputAllowlist"])
            if tool["mcpConnectionId"] is not None:
                connection = next(
                    item
                    for item in graph["mcpConnections"]
                    if item["connectionId"] == tool["mcpConnectionId"] and item["enabled"] is True
                )
                action = next(
                    item
                    for item in connection["reviewedCapabilityCatalog"]
                    if item["actionName"] == action_id
                )
                schemas.append(action["inputSchema"])
                output &= set(action["outputAllowlist"])
            if not isinstance(inputs, dict) or not output:
                raise ValueError("denied")
            for schema in schemas:
                Draft202012Validator.check_schema(schema)
                if not Draft202012Validator(schema).is_valid(inputs):
                    raise ValueError("denied")
            return output
        except Exception:
            raise PermissionError("capability_not_allowed") from None

    async def execute(
        self,
        *,
        snapshot: RevisionSnapshot,
        slot: TestSlot,
        tool_id: str,
        action_id: str,
        inputs: dict[str, Any],
        work: WorkBudget,
    ) -> dict[str, Any]:
        try:
            output = self._select(snapshot, slot, tool_id, action_id, inputs)
            if slot.revision_id != snapshot.revision_id or slot.policy_hash != snapshot.policy_hash:
                raise PermissionError("capability_not_allowed")
            remaining = (work.work_deadline_utc - self._utc_now()).total_seconds()
            if remaining <= 0:
                raise TimeoutError("work_deadline_exceeded")
            deadline = asyncio.get_running_loop().time() + min(10, remaining)
            document = {
                "requestId": "capability-" + uuid4().hex,
                "sessionId": slot.session_id,
                "agentId": slot.active_agent_id,
                "agentEpoch": slot.agent_epoch,
                "pinnedRevisionId": slot.revision_id,
                "toolId": tool_id,
                "actionId": action_id,
                "inputs": inputs,
                "deadlineAt": work.work_deadline_utc.isoformat(),
                "singleAttempt": True,
            }
            content = json.dumps(document, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(content) > 64 * 1024:
                raise PermissionError("invalid_capability_input")
            async with asyncio.timeout_at(deadline):
                current = await self._admission.check_output(
                    slot.session_id, slot.owner_epoch, generation=slot.generation
                )
                if (
                    current.active_agent_id != slot.active_agent_id
                    or current.agent_epoch != slot.agent_epoch
                ):
                    raise PermissionError("stale_agent_epoch")
                if (
                    current.resources["capability"].attempted
                    != current.resources["capability"].settled
                ):
                    raise PermissionError("capability_outcome_unknown")
                token = await self._credential.get_token(f"api://{self._audience}/.default")
                if not token.token or token.expires_on <= work.work_deadline_utc.timestamp():
                    raise PermissionError("capability_token_expired")
                intent = await self._admission.begin_attempt(
                    slot.session_id,
                    slot.owner_epoch,
                    kind="capability",
                    binding_hash=hashlib.sha256(content).hexdigest(),
                    generation=slot.generation,
                    work=work,
                )

                async def dispatch() -> tuple[int, bytes]:
                    async with httpx.AsyncClient(
                        transport=self._transport,
                        verify=True,
                        trust_env=False,
                        follow_redirects=False,
                        timeout=httpx.Timeout(
                            max(0.001, deadline - asyncio.get_running_loop().time())
                        ),
                    ) as client:
                        async with client.stream(
                            "POST",
                            self._url,
                            content=content,
                            headers={
                                "Authorization": "Bearer " + token.token,
                                "Content-Type": "application/json",
                                "Accept-Encoding": "identity",
                            },
                        ) as response:
                            if response.headers.get("content-encoding", "identity") != "identity":
                                raise ValueError("compressed_capability_response")
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(body) + len(chunk) > 64 * 1024:
                                    raise ValueError("oversized_capability_response")
                                body.extend(chunk)
                            return response.status_code, bytes(body)

                status, body = await self._admission.perform(intent, dispatch, work=work)
                if status in {401, 403, 413, 422}:
                    await self._settle(intent, "denied-" + uuid4().hex)
                    raise PermissionError("capability_not_allowed")
                if status != 200:
                    raise RuntimeError("capability_outcome_unknown")
                result = json.loads(
                    body,
                    object_pairs_hook=unique_object,
                    parse_constant=reject_constant,
                )
                if (
                    not isinstance(result, dict)
                    or set(result) != {"requestId", "sessionId", "agentId", "agentEpoch", "result"}
                    or any(
                        result[key] != document[key]
                        for key in ("requestId", "sessionId", "agentId", "agentEpoch")
                    )
                    or not isinstance(result["result"], dict)
                    or not set(result["result"]) <= output
                    or any(
                        isinstance(value, (dict, list))
                        or isinstance(value, float)
                        and not math.isfinite(value)
                        for value in result["result"].values()
                    )
                ):
                    raise ValueError("invalid_capability_response")
                await self._settle(intent, "completed-" + uuid4().hex)
                if self._utc_now() >= work.work_deadline_utc:
                    raise TimeoutError("work_deadline_exceeded")
                await self._admission.check_output(
                    slot.session_id, slot.owner_epoch, generation=slot.generation
                )
                return dict(result["result"])
        except PermissionError:
            raise
        except Exception:
            raise RuntimeError("capability_unavailable") from None
