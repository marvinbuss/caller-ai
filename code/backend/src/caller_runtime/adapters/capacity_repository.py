"""Explicit Cosmos capacity authority; injected storage only, never live qualification."""

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Annotated, Any, Final, Literal

from azure.core import MatchConditions
from azure.core.exceptions import AzureError, HttpResponseError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from caller_runtime.adapters.session_repository import (
    ContainerPort,
    CosmosSessionRepository,
    SessionConflict,
    metadata_model,
)
from caller_runtime.domain.session import (
    CallSession,
    CapacityRecoveryBinding,
    LifecycleState,
    OpaqueId,
    PhysicalConnectionClaim,
)

AUTHORITY_ID: Final = "runtime-capacity-authority-v1"
MAXIMUM_AUTHORITY_BYTES = 512 * 1024
OwnerState = Literal["serving", "draining", "drained"]
OwnerId = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
CapacityLimit = Annotated[int, Field(strict=True, ge=1, le=10000)]
ModelQuota = Annotated[int, Field(strict=True, ge=0, le=10000)]
ModelToken = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{32}$")]


class CapacityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, hide_input_in_errors=True)

    @field_validator("*", mode="after")
    @classmethod
    def require_utc(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.utcoffset() != timedelta(0):
            raise ValueError("capacity_clock_unavailable")
        return value


class CapacityOwner(CapacityMetadata):
    state: OwnerState
    lease_until: AwareDatetime
    lease_seconds: float = Field(strict=True, gt=0, le=300, allow_inf_nan=False)


class CapacityCall(CapacityMetadata):
    owner_id: OwnerId
    connection_epoch: int = Field(strict=True, ge=1)
    lease_until: AwareDatetime
    identity_hash: str = Field(strict=True, pattern=r"^[a-f0-9]{64}$")


class CapacityModel(CapacityMetadata):
    session_id: OpaqueId
    owner_id: OwnerId
    connection_epoch: int = Field(strict=True, ge=1)


class CapacityAuthority(CapacityMetadata):
    id: Literal["runtime-capacity-authority-v1"] = AUTHORITY_ID
    session_id: Literal["runtime-capacity-authority-v1"] = Field(
        default=AUTHORITY_ID, alias="sessionId"
    )
    schema_version: int = Field(default=2, strict=True, ge=2, le=2)
    ttl: int = Field(default=-1, strict=True, ge=-1, le=-1)
    maximum_sessions: CapacityLimit
    sessions_per_owner: CapacityLimit
    model_session_quota: ModelQuota
    observed_at: AwareDatetime
    owners: dict[OwnerId, CapacityOwner] = Field(default_factory=dict, max_length=1024)
    calls: dict[OpaqueId, CapacityCall] = Field(default_factory=dict, max_length=10000)
    models: dict[ModelToken, CapacityModel] = Field(default_factory=dict, max_length=10000)
    connections: dict[OpaqueId, PhysicalConnectionClaim] = Field(
        default_factory=dict, max_length=10000
    )


class CosmosCapacityRepository(CosmosSessionRepository):
    """Bounded same-partition CAS authority with no process-local reservation cache."""

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
            raise ValueError("invalid_capacity_operation_budget")
        super().__init__(container)
        self._utc_now = utc_now
        self._operation_budget = operation_budget

    def _instant(self) -> datetime:
        instant = self._utc_now()
        if (
            not isinstance(instant, datetime)
            or instant.tzinfo is None
            or instant.utcoffset() != timedelta(0)
        ):
            raise RuntimeError("capacity_clock_unavailable")
        return instant

    @staticmethod
    def _body(authority: CapacityAuthority) -> dict[str, Any]:
        body = authority.model_dump(mode="json", by_alias=True)
        if len(json.dumps(body, separators=(",", ":")).encode("utf-8")) > MAXIMUM_AUTHORITY_BYTES:
            raise RuntimeError("capacity_authority_full")
        return body

    @classmethod
    def _decode(cls, response: Mapping[str, Any]) -> tuple[CapacityAuthority, str]:
        if not isinstance(response, Mapping):
            raise RuntimeError("capacity_store_response_invalid")
        values = dict(response)
        etag = values.pop("_etag", None)
        for field in ("_rid", "_self", "_attachments", "_ts"):
            values.pop(field, None)
        try:
            if not isinstance(etag, str) or not 1 <= len(etag) <= 256:
                raise ValueError("missing_capacity_etag")
            if set(values) != {
                field.alias or name for name, field in CapacityAuthority.model_fields.items()
            }:
                raise ValueError("incomplete_capacity_authority")
            if (
                len(json.dumps(values, separators=(",", ":")).encode("utf-8"))
                > MAXIMUM_AUTHORITY_BYTES
            ):
                raise ValueError("capacity_authority_oversized")
            authority = CapacityAuthority.model_validate(values)
            cls._body(authority)
            if any(call.owner_id not in authority.owners for call in authority.calls.values()):
                raise ValueError("unknown_capacity_owner")
            if len(authority.calls) > authority.maximum_sessions:
                raise ValueError("capacity_limit_exceeded")
            for model in authority.models.values():
                if model.owner_id not in authority.owners:
                    raise ValueError("unknown_capacity_owner")
                call = authority.calls.get(model.session_id)
                if call is not None and (
                    call.owner_id != model.owner_id
                    or call.connection_epoch != model.connection_epoch
                ):
                    raise ValueError("invalid_model_capacity_binding")
            for session_id, claim in authority.connections.items():
                if (
                    claim.session_id != session_id
                    or claim.physical_owner_id not in authority.owners
                ):
                    raise ValueError("invalid_physical_connection_binding")
                call = authority.calls.get(session_id)
                if call is not None and (
                    call.owner_id != claim.physical_owner_id
                    or call.connection_epoch != claim.connection_epoch
                ):
                    raise ValueError("invalid_physical_connection_binding")
            return authority, etag
        except (ValueError, TypeError):
            raise RuntimeError("capacity_store_response_invalid") from None

    async def _read_authority(self) -> tuple[CapacityAuthority, str, datetime]:
        try:
            response = await self._container.read_item(
                item=AUTHORITY_ID, partition_key=AUTHORITY_ID, consistency_level="Strong"
            )
        except AzureError:
            raise RuntimeError("capacity_store_unavailable") from None
        authority, etag = self._decode(response)
        instant = self._instant()
        if instant < authority.observed_at:
            raise RuntimeError("capacity_clock_unavailable")
        return authority, etag, instant

    async def _change[Result](
        self, mutation: Callable[[CapacityAuthority, datetime], Awaitable[Result]]
    ) -> Result:
        async with asyncio.timeout(self._operation_budget):
            for attempt in range(4):
                authority, etag, instant = await self._read_authority()
                result = await mutation(authority, instant)
                completed = self._instant()
                if completed < instant:
                    raise RuntimeError("capacity_clock_unavailable")
                authority.observed_at = completed
                body = self._body(authority)
                try:
                    response = await self._container.replace_item(
                        item=AUTHORITY_ID,
                        body=body,
                        etag=etag,
                        match_condition=MatchConditions.IfNotModified,
                        retry_write=0,
                    )
                except HttpResponseError as error:
                    if error.status_code == 412 and attempt < 3:
                        continue
                    raise RuntimeError("capacity_store_unavailable") from None
                except AzureError:
                    raise RuntimeError("capacity_store_unavailable") from None
                stored, stored_etag = self._decode(response)
                if self._body(stored) != body or stored_etag == etag:
                    raise RuntimeError("capacity_store_response_invalid")
                return result
        raise RuntimeError("capacity_store_unavailable")

    async def initialize_capacity_authority(
        self, *, maximum_sessions: int, sessions_per_owner: int, model_session_quota: int
    ) -> bool:
        """Explicit trusted bootstrap only; normal operations never recreate missing state."""
        try:
            authority = CapacityAuthority(
                maximum_sessions=maximum_sessions,
                sessions_per_owner=sessions_per_owner,
                model_session_quota=model_session_quota,
                observed_at=self._instant(),
            )
        except ValidationError:
            raise ValueError("invalid_capacity_limits") from None
        async with asyncio.timeout(self._operation_budget):
            try:
                response = await self._container.create_item(
                    self._body(authority), enable_automatic_id_generation=False, retry_write=0
                )
            except HttpResponseError as error:
                if error.status_code == 409:
                    existing, _etag, _instant = await self._read_authority()
                    if (existing.maximum_sessions, existing.sessions_per_owner) != (
                        maximum_sessions,
                        sessions_per_owner,
                    ):
                        raise ValueError("capacity_policy_conflict") from None
                    return False
                raise RuntimeError("capacity_store_unavailable") from None
            except AzureError:
                raise RuntimeError("capacity_store_unavailable") from None
            stored, _etag = self._decode(response)
            if self._body(stored) != self._body(authority):
                raise RuntimeError("capacity_store_response_invalid")
            return True

    async def register_capacity_owner(self, owner_id: str, *, lease_seconds: float) -> OwnerState:
        if not isinstance(owner_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", owner_id
        ):
            raise ValueError("invalid_capacity_owner")
        if (
            type(lease_seconds) not in (int, float)
            or not math.isfinite(lease_seconds)
            or not 0 < lease_seconds <= 300
        ):
            raise ValueError("invalid_owner_lease_period")

        async def register(authority: CapacityAuthority, instant: datetime) -> OwnerState:
            owner = authority.owners.get(owner_id)
            if owner is not None:
                if owner.lease_seconds != lease_seconds:
                    raise ValueError("owner_lease_policy_conflict")
                return owner.state
            if len(authority.owners) >= 1024:
                raise RuntimeError("owner_registry_full")
            authority.owners[owner_id] = CapacityOwner(
                state="serving",
                lease_until=instant + timedelta(seconds=lease_seconds),
                lease_seconds=lease_seconds,
            )
            return "serving"

        return await self._change(register)

    async def reserve_capacity(
        self,
        metadata: Mapping[str, Any],
        *,
        maximum_sessions: int,
        sessions_per_owner: int,
        model_session_quota: int,
    ) -> Literal["reserved", "duplicate", "unavailable"]:
        session = metadata_model(metadata)
        if session.session_id == AUTHORITY_ID:
            raise ValueError("reserved_capacity_identity")
        if (
            any(
                type(value) is not int or not 1 <= value <= 10000
                for value in (maximum_sessions, sessions_per_owner)
            )
            or type(model_session_quota) is not int
            or not 0 <= model_session_quota <= 10000
        ):
            raise ValueError("invalid_capacity_limits")

        async def reserve(
            authority: CapacityAuthority, instant: datetime
        ) -> Literal["reserved", "duplicate", "unavailable"]:
            if (authority.maximum_sessions, authority.sessions_per_owner) != (
                maximum_sessions,
                sessions_per_owner,
            ):
                raise ValueError("capacity_policy_conflict")
            if session.session_id in authority.calls:
                return "duplicate"
            owner = authority.owners.get(session.physical_owner_id)
            owned = sum(
                call.owner_id == session.physical_owner_id for call in authority.calls.values()
            )
            if (
                owner is None
                or owner.state != "serving"
                or owner.lease_until <= instant
                or session.lease_until <= instant
                or session.started_at > instant
                or len(authority.calls) >= authority.maximum_sessions
                or len(authority.calls) + len(authority.models) >= authority.model_session_quota
                or owned >= authority.sessions_per_owner
            ):
                return "unavailable"
            authority.calls[session.session_id] = CapacityCall(
                owner_id=session.physical_owner_id,
                connection_epoch=session.connection_epoch,
                lease_until=min(session.lease_until, owner.lease_until),
                identity_hash=self._identity_hash(session),
            )
            return "reserved"

        return await self._change(reserve)

    @staticmethod
    def _identity_hash(session: CallSession) -> str:
        document = session.to_document()
        values = {
            key: document[key]
            for key in (
                "sessionId",
                "providerCallRef",
                "configurationRevisionId",
                "physicalOwnerId",
                "connectionEpoch",
                "startedAt",
            )
        }
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _owner(authority: CapacityAuthority, owner_id: str) -> CapacityOwner:
        owner = authority.owners.get(owner_id)
        if owner is None:
            raise SessionConflict("capacity_owner_unknown")
        return owner

    @classmethod
    def _binding(cls, authority: CapacityAuthority, session: CallSession) -> CapacityCall:
        call = authority.calls.get(session.session_id)
        if (
            call is None
            or call.owner_id != session.physical_owner_id
            or call.connection_epoch != session.connection_epoch
            or call.identity_hash != cls._identity_hash(session)
        ):
            raise SessionConflict("capacity_session_not_owned")
        return call

    async def serving_capacity_owners(self, owner_ids: tuple[str, ...]) -> tuple[str, ...]:
        if (
            not isinstance(owner_ids, tuple)
            or len(owner_ids) > 1024
            or any(
                not isinstance(owner, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", owner)
                for owner in owner_ids
            )
        ):
            raise ValueError("invalid_capacity_owner_query")
        async with asyncio.timeout(self._operation_budget):
            authority, _etag, instant = await self._read_authority()
            return tuple(
                owner_id
                for owner_id in owner_ids
                if owner_id in authority.owners
                and authority.owners[owner_id].state == "serving"
                and authority.owners[owner_id].lease_until > instant
                and sum(call.owner_id == owner_id for call in authority.calls.values())
                < authority.sessions_per_owner
            )

    async def capacity_owner_state(self, owner_id: str) -> OwnerState:
        async with asyncio.timeout(self._operation_budget):
            authority, _etag, _instant = await self._read_authority()
            return self._owner(authority, owner_id).state

    async def claim_connection(self, claim: PhysicalConnectionClaim) -> bool:
        async def reserve(authority: CapacityAuthority, instant: datetime) -> bool:
            if claim.closed or claim.session_id not in authority.calls:
                return False
            document = await super(CosmosCapacityRepository, self).read(claim.session_id)
            if document is None:
                return False
            current = metadata_model(document)
            call = self._binding(authority, current)
            owner = self._owner(authority, call.owner_id)
            completed = self._instant()
            if (
                completed < instant
                or current.state == LifecycleState.ENDED
                or call.owner_id != claim.physical_owner_id
                or call.connection_epoch != claim.connection_epoch
                or owner.state == "drained"
                or min(owner.lease_until, call.lease_until) <= completed
            ):
                return False
            existing = authority.connections.get(claim.session_id)
            if existing is not None:
                return existing == claim and not existing.closed
            if len(authority.connections) >= 10000:
                raise RuntimeError("connection_registry_full")
            authority.connections[claim.session_id] = claim
            return True

        return await self._change(reserve)

    async def connection_claims(self, owner_id: str) -> tuple[PhysicalConnectionClaim, ...]:
        async with asyncio.timeout(self._operation_budget):
            authority, _etag, _instant = await self._read_authority()
            self._owner(authority, owner_id)
            return tuple(
                claim.model_copy()
                for claim in authority.connections.values()
                if claim.physical_owner_id == owner_id
            )

    async def connection_claim_current(self, claim: PhysicalConnectionClaim) -> bool:
        async with asyncio.timeout(self._operation_budget):
            authority, _etag, instant = await self._read_authority()
            owner = self._owner(authority, claim.physical_owner_id)
            call = authority.calls.get(claim.session_id)
            return (
                not claim.closed
                and authority.connections.get(claim.session_id) == claim
                and call is not None
                and min(owner.lease_until, call.lease_until) > instant
            )

    async def close_connection_claim(self, claim: PhysicalConnectionClaim) -> None:
        async def close(authority: CapacityAuthority, instant: datetime) -> None:
            existing = authority.connections.get(claim.session_id)
            if existing is None:
                return
            if existing.model_copy(update={"closed": False}) != claim.model_copy(
                update={"closed": False}
            ):
                raise SessionConflict("physical_connection_not_owned")
            document = await super(CosmosCapacityRepository, self).read(claim.session_id)
            if document is None:
                raise RuntimeError("session_authority_unknown")
            current = metadata_model(document)
            if (
                current.physical_owner_id != claim.physical_owner_id
                or current.connection_epoch != claim.connection_epoch
            ):
                raise SessionConflict("physical_connection_not_owned")
            if current.state == LifecycleState.ENDED:
                del authority.connections[claim.session_id]
            else:
                authority.connections[claim.session_id] = existing.model_copy(
                    update={"closed": True}
                )

        await self._change(close)

    async def capacity_recovery_bindings(
        self, owner_id: str
    ) -> tuple[CapacityRecoveryBinding, ...]:
        async with asyncio.timeout(self._operation_budget):
            authority, _etag, _instant = await self._read_authority()
            self._owner(authority, owner_id)
            bindings = [
                CapacityRecoveryBinding(
                    session_id=session_id,
                    physical_owner_id=call.owner_id,
                    connection_epoch=call.connection_epoch,
                )
                for session_id, call in authority.calls.items()
                if call.owner_id == owner_id
            ]
            bindings.extend(
                CapacityRecoveryBinding(
                    session_id=model.session_id,
                    physical_owner_id=model.owner_id,
                    connection_epoch=model.connection_epoch,
                    model_reservation=token,
                )
                for token, model in authority.models.items()
                if model.owner_id == owner_id
            )
            return tuple(
                sorted(
                    bindings,
                    key=lambda binding: (binding.session_id, binding.model_reservation or ""),
                )
            )

    async def begin_capacity_drain(self, owner_id: str) -> OwnerState:
        async def begin(authority: CapacityAuthority, instant: datetime) -> OwnerState:
            owner = self._owner(authority, owner_id)
            if owner.state == "serving":
                owner.state = "draining"
            return owner.state

        return await self._change(begin)

    async def finish_capacity_drain(self, owner_id: str) -> Literal["postponed", "drained"]:
        async def finish(
            authority: CapacityAuthority, instant: datetime
        ) -> Literal["postponed", "drained"]:
            owner = self._owner(authority, owner_id)
            if owner.state == "serving":
                raise PermissionError("drain_not_started")
            if (
                any(call.owner_id == owner_id for call in authority.calls.values())
                or any(model.owner_id == owner_id for model in authority.models.values())
                or any(
                    claim.physical_owner_id == owner_id and not claim.closed
                    for claim in authority.connections.values()
                )
            ):
                return "postponed"
            owner.state = "drained"
            return "drained"

        return await self._change(finish)

    async def create(self, metadata: Mapping[str, Any]) -> bool:
        session = metadata_model(metadata)
        if session.session_id == AUTHORITY_ID:
            raise ValueError("reserved_capacity_identity")
        async with asyncio.timeout(self._operation_budget):
            authority, _etag, instant = await self._read_authority()
            call = self._binding(authority, session)
            owner = self._owner(authority, call.owner_id)
            if owner.state == "drained" or min(owner.lease_until, call.lease_until) <= instant:
                raise PermissionError("owner_lease_lost")
            bounded = session.model_copy(update={"lease_until": call.lease_until})
            return await super().create(bounded.to_document())

    async def read(self, session_id: str) -> dict[str, Any] | None:
        if session_id == AUTHORITY_ID:
            raise ValueError("reserved_capacity_identity")
        async with asyncio.timeout(self._operation_budget):
            document = await super().read(session_id)
            if document is None:
                return None
            session = metadata_model(document)
            if session.state == LifecycleState.ENDED:
                return document
            authority, _etag, _instant = await self._read_authority()
            call = self._binding(authority, session)
            return {**document, "leaseUntil": call.lease_until.isoformat()}

    async def renew_capacity_owner(self, owner_id: str, *, lease_seconds: float) -> OwnerState:
        if (
            type(lease_seconds) not in (int, float)
            or not math.isfinite(lease_seconds)
            or not 0 < lease_seconds <= 300
        ):
            raise ValueError("invalid_owner_lease_period")

        async def renew(authority: CapacityAuthority, instant: datetime) -> OwnerState:
            owner = self._owner(authority, owner_id)
            if owner.lease_seconds != lease_seconds:
                raise ValueError("owner_lease_policy_conflict")
            if owner.state == "drained" or owner.lease_until <= instant:
                raise PermissionError("owner_lease_lost")
            active = []
            for session_id, call in authority.calls.items():
                if call.owner_id != owner_id:
                    continue
                document = await super(CosmosCapacityRepository, self).read(session_id)
                if document is None:
                    raise RuntimeError("session_authority_unknown")
                current = metadata_model(document)
                self._binding(authority, current)
                if current.state != LifecycleState.ENDED:
                    active.append(call)
            completed = self._instant()
            if completed < instant:
                raise RuntimeError("capacity_clock_unavailable")
            if owner.lease_until <= completed or any(
                call.lease_until <= completed for call in active
            ):
                raise PermissionError("owner_lease_lost")
            deadline = completed + timedelta(seconds=lease_seconds)
            owner.lease_until = deadline
            for call in active:
                call.lease_until = deadline
            return owner.state

        return await self._change(renew)

    async def release_capacity(self, session_id: str, *, physical_owner_id: str) -> None:
        async def release(authority: CapacityAuthority, instant: datetime) -> None:
            call = authority.calls.get(session_id)
            if call is None:
                return
            document = await super(CosmosCapacityRepository, self).read(session_id)
            if document is None:
                raise SessionConflict("capacity_closure_not_confirmed")
            current = metadata_model(document)
            self._binding(authority, current)
            if current.state != LifecycleState.ENDED or call.owner_id != physical_owner_id:
                raise SessionConflict("capacity_closure_not_confirmed")
            del authority.calls[session_id]
            claim = authority.connections.get(session_id)
            if claim is not None and claim.closed:
                del authority.connections[session_id]

        await self._change(release)

    async def reserve_model_capacity(
        self, session_id: str, *, physical_owner_id: str, reservation: str
    ) -> bool:
        if not isinstance(reservation, str) or not re.fullmatch(r"[a-f0-9]{32}", reservation):
            raise ValueError("invalid_model_reservation")

        async def reserve(authority: CapacityAuthority, instant: datetime) -> bool:
            document = await super(CosmosCapacityRepository, self).read(session_id)
            if document is None:
                raise SessionConflict("model_capacity_not_owned")
            current = metadata_model(document)
            call = self._binding(authority, current)
            owner = self._owner(authority, physical_owner_id)
            completed = self._instant()
            if (
                completed < instant
                or current.state == LifecycleState.ENDED
                or call.owner_id != physical_owner_id
                or owner.state == "drained"
                or min(owner.lease_until, call.lease_until) <= completed
            ):
                raise SessionConflict("model_capacity_not_owned")
            binding = CapacityModel(
                session_id=session_id,
                owner_id=physical_owner_id,
                connection_epoch=call.connection_epoch,
            )
            existing = authority.models.get(reservation)
            if existing is not None:
                if existing != binding:
                    raise SessionConflict("model_capacity_not_owned")
                return True
            if len(authority.calls) + len(authority.models) >= authority.model_session_quota:
                return False
            authority.models[reservation] = binding
            return True

        return await self._change(reserve)

    async def release_model_capacity(
        self, reservation: str, *, session_id: str, physical_owner_id: str
    ) -> None:
        async def release(authority: CapacityAuthority, instant: datetime) -> None:
            binding = authority.models.get(reservation)
            if binding is None:
                return
            document = await super(CosmosCapacityRepository, self).read(session_id)
            if document is None:
                raise SessionConflict("model_capacity_not_owned")
            current = metadata_model(document)
            if (
                binding.session_id != session_id
                or binding.owner_id != physical_owner_id
                or current.physical_owner_id != physical_owner_id
                or binding.connection_epoch != current.connection_epoch
            ):
                raise SessionConflict("model_capacity_not_owned")
            del authority.models[reservation]

        await self._change(release)

    async def set_model_capacity_quota(self, quota: int) -> None:
        if type(quota) is not int or not 0 <= quota <= 10000:
            raise ValueError("invalid_model_session_quota")

        async def change(authority: CapacityAuthority, instant: datetime) -> None:
            authority.model_session_quota = quota

        await self._change(change)
