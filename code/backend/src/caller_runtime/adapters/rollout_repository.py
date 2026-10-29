"""Injected runtime-owned removal ledger; not cluster authorization or deletion enforcement."""

import asyncio
import json
import math
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Annotated, Any, Final, Literal, Self

from azure.core import MatchConditions
from azure.core.exceptions import AzureError, HttpResponseError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from caller_runtime.adapters.session_repository import ContainerPort, SessionConflict

LEDGER_ID: Final = "runtime-removal-ledger-v1"
MAXIMUM_LEDGER_BYTES = 512 * 1024
Reference = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
TargetName = Annotated[str, Field(strict=True, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")]
Phase = Literal["requested", "fenced", "submitted", "retired"]


class RemovalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class RemovalTarget(RemovalMetadata):
    cluster_ref: Reference
    namespace: TargetName
    workload: TargetName
    owner_id: Reference
    pod_name: TargetName
    pod_uid: Reference


class RemovalOperation(RemovalMetadata):
    operation_id: Reference
    intent: Literal["rollout", "scale_in"]
    target: RemovalTarget


class RemovalEntry(RemovalMetadata):
    request: RemovalOperation
    phase: Phase
    fence_reference: Reference | None

    @model_validator(mode="after")
    def require_phase_binding(self) -> Self:
        if (self.phase == "requested") != (self.fence_reference is None):
            raise ValueError("invalid_removal_fence_state")
        return self


class RemovalLedgerState(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, populate_by_name=True)

    id: Literal["runtime-removal-ledger-v1"] = LEDGER_ID
    session_id: Literal["runtime-removal-ledger-v1"] = Field(default=LEDGER_ID, alias="sessionId")
    schema_version: int = Field(default=1, strict=True, ge=1, le=1)
    ttl: int = Field(default=-1, strict=True, ge=-1, le=-1)
    observed_at: AwareDatetime
    operations: dict[Reference, RemovalEntry] = Field(default_factory=dict, max_length=1024)


class CosmosRemovalLedger:
    """One bounded conditional authority over an explicit runtime-owned container."""

    def __init__(
        self,
        container: ContainerPort,
        *,
        utc_now: Callable[[], datetime],
        operation_budget: float = 10,
    ) -> None:
        if (
            type(operation_budget) not in (int, float)
            or not math.isfinite(operation_budget)
            or not 0 < operation_budget <= 10
        ):
            raise ValueError("invalid_removal_ledger_budget")
        self._container, self._clock, self._budget = container, utc_now, operation_budget

    def _instant(self) -> datetime:
        instant = self._clock()
        if (
            not isinstance(instant, datetime)
            or instant.tzinfo is None
            or instant.utcoffset() != timedelta(0)
        ):
            raise RuntimeError("removal_clock_unavailable")
        return instant

    @staticmethod
    def _body(state: RemovalLedgerState) -> dict[str, Any]:
        body = state.model_dump(mode="json", by_alias=True)
        if len(json.dumps(body, separators=(",", ":")).encode("utf-8")) > MAXIMUM_LEDGER_BYTES:
            raise RuntimeError("removal_ledger_full")
        return body

    @classmethod
    def _decode(cls, response: Mapping[str, Any]) -> tuple[RemovalLedgerState, str]:
        try:
            if not isinstance(response, Mapping):
                raise ValueError
            values = dict(response)
            etag = values.pop("_etag", None)
            for name in ("_rid", "_self", "_attachments", "_ts"):
                values.pop(name, None)
            if not isinstance(etag, str) or not 1 <= len(etag) <= 256:
                raise ValueError
            if set(values) != {
                field.alias or name for name, field in RemovalLedgerState.model_fields.items()
            }:
                raise ValueError
            if (
                len(json.dumps(values, separators=(",", ":")).encode("utf-8"))
                > MAXIMUM_LEDGER_BYTES
            ):
                raise ValueError
            state = RemovalLedgerState.model_validate(values)
            if state.observed_at.utcoffset() != timedelta(0) or any(
                operation_id != entry.request.operation_id
                for operation_id, entry in state.operations.items()
            ):
                raise ValueError
            return state, etag
        except (ValueError, TypeError):
            raise RuntimeError("removal_store_response_invalid") from None

    async def _read(self) -> tuple[RemovalLedgerState, str, datetime]:
        try:
            response = await self._container.read_item(
                item=LEDGER_ID, partition_key=LEDGER_ID, consistency_level="Strong"
            )
        except AzureError:
            raise RuntimeError("removal_store_unavailable") from None
        state, etag = self._decode(response)
        instant = self._instant()
        if instant < state.observed_at:
            raise RuntimeError("removal_clock_unavailable")
        return state, etag, instant

    @staticmethod
    def _request(request: Mapping[str, Any]) -> RemovalOperation:
        try:
            return RemovalOperation.model_validate(request)
        except ValueError:
            raise ValueError("invalid_removal_request") from None

    @staticmethod
    def _entry(state: RemovalLedgerState, request: RemovalOperation) -> RemovalEntry:
        entry = state.operations.get(request.operation_id)
        if entry is None or entry.request != request:
            raise SessionConflict("removal_operation_binding_conflict")
        return entry

    async def _change[Result](self, mutation: Callable[[RemovalLedgerState], Result]) -> Result:
        async with asyncio.timeout(self._budget):
            for attempt in range(4):
                state, etag, instant = await self._read()
                result = mutation(state)
                completed = self._instant()
                if completed < instant:
                    raise RuntimeError("removal_clock_unavailable")
                state.observed_at = completed
                body = self._body(state)
                try:
                    response = await self._container.replace_item(
                        item=LEDGER_ID,
                        body=body,
                        etag=etag,
                        match_condition=MatchConditions.IfNotModified,
                        retry_write=0,
                    )
                except HttpResponseError as error:
                    if error.status_code == 412 and attempt < 3:
                        continue
                    raise RuntimeError("removal_store_unavailable") from None
                except AzureError:
                    raise RuntimeError("removal_store_unavailable") from None
                stored, stored_etag = self._decode(response)
                if stored_etag == etag or self._body(stored) != body:
                    raise RuntimeError("removal_store_response_invalid")
                return result
        raise RuntimeError("removal_store_unavailable")

    async def initialize(self) -> bool:
        """Explicit trusted empty-installation bootstrap, never recovery after authority loss."""
        state = RemovalLedgerState(observed_at=self._instant())
        async with asyncio.timeout(self._budget):
            try:
                response = await self._container.create_item(
                    self._body(state), enable_automatic_id_generation=False, retry_write=0
                )
            except HttpResponseError as error:
                if error.status_code == 409:
                    await self._read()
                    return False
                raise RuntimeError("removal_store_unavailable") from None
            except AzureError:
                raise RuntimeError("removal_store_unavailable") from None
            stored, _etag = self._decode(response)
            if self._body(stored) != self._body(state):
                raise RuntimeError("removal_store_response_invalid")
            return True

    async def inspect(self, request: Mapping[str, Any]) -> RemovalEntry:
        operation = self._request(request)
        async with asyncio.timeout(self._budget):
            state, _etag, _instant = await self._read()
            return self._entry(state, operation).model_copy(deep=True)

    async def reserve(self, request: Mapping[str, Any]) -> RemovalEntry:
        operation = self._request(request)

        def reserve(state: RemovalLedgerState) -> RemovalEntry:
            if operation.operation_id in state.operations:
                return self._entry(state, operation).model_copy(deep=True)
            if len(state.operations) >= 1024:
                raise RuntimeError("removal_ledger_full")
            entry = RemovalEntry(request=operation, phase="requested", fence_reference=None)
            state.operations[operation.operation_id] = entry
            return entry.model_copy(deep=True)

        return await self._change(reserve)

    async def record_fence(
        self, request: Mapping[str, Any], *, fence_reference: str
    ) -> RemovalEntry:
        operation = self._request(request)

        def fence(state: RemovalLedgerState) -> RemovalEntry:
            current = self._entry(state, operation)
            if current.phase != "requested":
                if current.fence_reference != fence_reference:
                    raise SessionConflict("removal_fence_binding_conflict")
                return current.model_copy(deep=True)
            try:
                entry = RemovalEntry(
                    request=operation, phase="fenced", fence_reference=fence_reference
                )
            except ValueError:
                raise ValueError("invalid_removal_fence_reference") from None
            state.operations[operation.operation_id] = entry
            return entry.model_copy(deep=True)

        return await self._change(fence)

    async def begin_retirement(
        self, request: Mapping[str, Any], *, fence_reference: str
    ) -> Literal["submit", "uncertain", "already_retired"]:
        operation = self._request(request)

        def submit(state: RemovalLedgerState) -> Literal["submit", "uncertain", "already_retired"]:
            entry = self._entry(state, operation)
            if entry.fence_reference != fence_reference or entry.phase == "requested":
                raise SessionConflict("removal_fence_binding_conflict")
            if entry.phase == "retired":
                return "already_retired"
            if entry.phase == "submitted":
                return "uncertain"
            state.operations[operation.operation_id] = entry.model_copy(
                update={"phase": "submitted"}
            )
            return "submit"

        return await self._change(submit)

    async def confirm_retired(self, request: Mapping[str, Any], *, fence_reference: str) -> None:
        """Trusted caller must independently verify exact guarded removal before confirmation."""
        operation = self._request(request)

        def confirm(state: RemovalLedgerState) -> None:
            entry = self._entry(state, operation)
            if (
                entry.phase not in {"submitted", "retired"}
                or entry.fence_reference != fence_reference
            ):
                raise SessionConflict("removal_confirmation_not_bound")
            state.operations[operation.operation_id] = entry.model_copy(update={"phase": "retired"})

        await self._change(confirm)
