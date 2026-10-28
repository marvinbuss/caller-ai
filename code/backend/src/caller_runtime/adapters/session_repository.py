"""Runtime-owned metadata repositories; no identity discovery or client construction."""

import asyncio
import copy
import math
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from azure.core import MatchConditions
from azure.core.exceptions import AzureError, HttpResponseError
from azure.cosmos.exceptions import CosmosBatchOperationError

from caller_runtime.domain.handover import HandoverRecord
from caller_runtime.domain.session import TRANSITIONS, CallSession, LifecycleState

BatchOperation = tuple[str, tuple[Any, ...]] | tuple[str, tuple[Any, ...], dict[str, Any]]


class ContainerPort(Protocol):
    async def execute_item_batch(
        self, *, batch_operations: list[BatchOperation], partition_key: str, **keywords: Any
    ) -> list[dict[str, Any]]: ...

    async def create_item(self, body: dict[str, Any], **keywords: Any) -> Mapping[str, Any]: ...

    async def read_item(
        self, item: str, partition_key: str, **keywords: Any
    ) -> Mapping[str, Any]: ...

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        *,
        etag: str,
        match_condition: MatchConditions,
        **keywords: Any,
    ) -> Mapping[str, Any]: ...


class SessionConflict(RuntimeError):
    """A conditional mutation lost authority; no sensitive storage details are exposed."""


def metadata_model(document: Mapping[str, Any]) -> CallSession:
    values = dict(document)
    identifier = values.pop("id", None)
    for field_name in ("_rid", "_self", "_attachments", "_ts"):
        values.pop(field_name, None)
    try:
        session = CallSession.model_validate(values)
    except ValueError:
        raise ValueError("invalid_session_metadata") from None
    if identifier is not None and identifier != session.session_id:
        raise ValueError("session_document_identity_mismatch")
    return session


def verify_mutation(
    current: CallSession | None, candidate: CallSession, session_id: str, *, handover: bool = False
) -> None:
    if current is None or candidate.session_id != session_id:
        raise SessionConflict("session_identity_conflict")
    if candidate.etag is None or candidate.etag != current.etag:
        raise SessionConflict("session_etag_conflict")
    if current.state == LifecycleState.ENDED:
        raise SessionConflict("terminal_session_immutable")
    if (
        candidate.physical_owner_id != current.physical_owner_id
        or candidate.connection_epoch != current.connection_epoch
    ):
        raise SessionConflict("session_owner_conflict")
    if (
        candidate.provider_call_ref != current.provider_call_ref
        or candidate.configuration_revision_id != current.configuration_revision_id
        or candidate.started_at != current.started_at
    ):
        raise SessionConflict("session_identity_conflict")
    if candidate.agent_epoch < current.agent_epoch:
        raise SessionConflict("session_agent_epoch_conflict")
    handover_transition = (
        handover
        and current.state
        in {
            LifecycleState.INTRODUCING,
            LifecycleState.ROUTING,
            LifecycleState.ACTIVE,
            LifecycleState.TRANSFERRING,
        }
        and candidate.state
        in {LifecycleState.ROUTING, LifecycleState.ACTIVE, LifecycleState.CLOSING}
    )
    if (
        candidate.state != current.state
        and candidate.state not in TRANSITIONS[current.state]
        and not handover_transition
    ):
        raise SessionConflict("invalid_session_transition")


def verify_handover(
    current: CallSession | None, candidate: CallSession, session_id: str, record: HandoverRecord
) -> None:
    verify_mutation(current, candidate, session_id, handover=record.outcome == "committed")
    assert current is not None
    if (
        record.outcome == "requested"
        or record.session_id != session_id
        or record.to_document()["id"] == session_id
        or record.configuration_revision_id != current.configuration_revision_id
        or record.physical_owner_id != current.physical_owner_id
        or record.connection_epoch != current.connection_epoch
        or record.source_agent_id != current.active_agent_id
        or record.source_agent_epoch != current.agent_epoch
    ):
        raise SessionConflict("handover_identity_conflict")
    if record.outcome == "committed":
        if (
            candidate.active_agent_id != record.destination_agent_id
            or candidate.agent_epoch != record.destination_agent_epoch
        ):
            raise SessionConflict("handover_destination_conflict")
    elif (
        candidate.active_agent_id != current.active_agent_id
        or candidate.agent_epoch != current.agent_epoch
        or candidate.active_community_id != current.active_community_id
        or (
            candidate.state != current.state
            and not (record.outcome == "canceled" and candidate.state == LifecycleState.ENDED)
        )
    ):
        raise SessionConflict("failed_handover_changed_owner")


class LocalSessionRepository:
    """Conditional session/shared-capacity stand-in; not production or Cosmos evidence."""

    def __init__(self, *, utc_now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._handovers: dict[tuple[str, str], dict[str, Any]] = {}
        self._capacity_calls: dict[str, tuple[str, int]] = {}
        self._capacity_models: dict[str, tuple[str, str, int]] = {}
        self._capacity_owners: dict[str, Literal["serving", "draining", "drained"]] = {}
        self._capacity_owner_leases: dict[str, datetime] = {}
        self._capacity_owner_periods: dict[str, float] = {}
        self._capacity_clock = utc_now
        self._capacity_last_at: datetime | None = None
        self._capacity_limits: tuple[int, int] | None = None
        self._capacity_model_quota: int | None = None
        self._version = 0
        self._lock = asyncio.Lock()

    def _capacity_now(self) -> datetime:
        instant = self._capacity_clock()
        if (
            not isinstance(instant, datetime)
            or instant.tzinfo is None
            or instant.utcoffset() != timedelta(0)
            or (self._capacity_last_at is not None and instant < self._capacity_last_at)
        ):
            raise RuntimeError("capacity_clock_unavailable")
        self._capacity_last_at = instant
        return instant

    @staticmethod
    def _capacity_period(lease_seconds: float) -> float:
        if (
            type(lease_seconds) not in (int, float)
            or not math.isfinite(lease_seconds)
            or not 0 < lease_seconds <= 300
        ):
            raise ValueError("invalid_owner_lease_period")
        return float(lease_seconds)

    async def register_capacity_owner(
        self, owner_id: str, *, lease_seconds: float
    ) -> Literal["serving", "draining", "drained"]:
        if not isinstance(owner_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", owner_id
        ):
            raise ValueError("invalid_capacity_owner")
        period = self._capacity_period(lease_seconds)
        async with self._lock:
            instant = self._capacity_now()
            if owner_id not in self._capacity_owners and len(self._capacity_owners) >= 1024:
                raise RuntimeError("owner_registry_full")
            if owner_id in self._capacity_owner_periods:
                if self._capacity_owner_periods[owner_id] != period:
                    raise ValueError("owner_lease_policy_conflict")
            else:
                self._capacity_owner_periods[owner_id] = period
                self._capacity_owner_leases[owner_id] = instant + timedelta(seconds=period)
            return self._capacity_owners.setdefault(owner_id, "serving")

    async def renew_capacity_owner(
        self, owner_id: str, *, lease_seconds: float
    ) -> Literal["serving", "draining", "drained"]:
        period = self._capacity_period(lease_seconds)
        async with self._lock:
            instant = self._capacity_now()
            state = self._capacity_owners.get(owner_id)
            if (
                state is None
                or state == "drained"
                or self._capacity_owner_leases[owner_id] <= instant
            ):
                raise PermissionError("owner_lease_lost")
            if self._capacity_owner_periods[owner_id] != period:
                raise ValueError("owner_lease_policy_conflict")
            deadline = instant + timedelta(seconds=period)
            updates = {}
            for session_id, (owner, epoch) in self._capacity_calls.items():
                if owner != owner_id:
                    continue
                document = self._records.get(session_id)
                if document is None:
                    raise RuntimeError("session_authority_unknown")
                current = metadata_model(document)
                if current.physical_owner_id != owner_id or current.connection_epoch != epoch:
                    raise PermissionError("session_owner_mismatch")
                if current.state != LifecycleState.ENDED:
                    if current.lease_until <= instant:
                        raise PermissionError("session_lease_lost")
                    updates[session_id] = current.model_copy(update={"lease_until": deadline})
            for session_id, current in updates.items():
                self._records[session_id] = self._snapshot(current)
            self._capacity_owner_leases[owner_id] = deadline
            return state

    async def capacity_owner_state(
        self, owner_id: str
    ) -> Literal["serving", "draining", "drained"]:
        async with self._lock:
            if owner_id not in self._capacity_owners:
                raise SessionConflict("capacity_owner_unknown")
            return self._capacity_owners[owner_id]

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
        async with self._lock:
            instant = self._capacity_now()
            counts = {owner: 0 for owner in owner_ids}
            for owner, _epoch in self._capacity_calls.values():
                if owner in counts:
                    counts[owner] += 1
            return tuple(
                owner
                for owner in owner_ids
                if self._capacity_owners.get(owner) == "serving"
                and self._capacity_owner_leases.get(owner, instant) > instant
                and (self._capacity_limits is None or counts[owner] < self._capacity_limits[1])
            )

    async def begin_capacity_drain(
        self, owner_id: str
    ) -> Literal["serving", "draining", "drained"]:
        async with self._lock:
            if owner_id not in self._capacity_owners:
                raise SessionConflict("capacity_owner_unknown")
            if self._capacity_owners[owner_id] == "serving":
                self._capacity_owners[owner_id] = "draining"
            return self._capacity_owners[owner_id]

    async def finish_capacity_drain(self, owner_id: str) -> Literal["postponed", "drained"]:
        async with self._lock:
            state = self._capacity_owners.get(owner_id)
            if state is None:
                raise SessionConflict("capacity_owner_unknown")
            if state == "serving":
                raise PermissionError("drain_not_started")
            if any(owner == owner_id for owner, _epoch in self._capacity_calls.values()) or any(
                owner == owner_id for _session, owner, _epoch in self._capacity_models.values()
            ):
                return "postponed"
            self._capacity_owners[owner_id] = "drained"
            return "drained"

    async def reserve_capacity(
        self,
        metadata: Mapping[str, Any],
        *,
        maximum_sessions: int,
        sessions_per_owner: int,
        model_session_quota: int,
    ) -> Literal["reserved", "duplicate", "unavailable"]:
        session = metadata_model(metadata)
        if (
            any(
                type(value) is not int or not 1 <= value <= 10000
                for value in (maximum_sessions, sessions_per_owner)
            )
            or type(model_session_quota) is not int
            or not 0 <= model_session_quota <= 10000
        ):
            raise ValueError("invalid_capacity_limits")
        async with self._lock:
            instant = self._capacity_now()
            if (
                self._capacity_owners.get(session.physical_owner_id) != "serving"
                or self._capacity_owner_leases[session.physical_owner_id] <= instant
            ):
                return "unavailable"
            limits = (maximum_sessions, sessions_per_owner)
            if self._capacity_limits is not None and self._capacity_limits != limits:
                raise ValueError("capacity_policy_conflict")
            self._capacity_limits = limits
            if self._capacity_model_quota is None:
                self._capacity_model_quota = model_session_quota
            if session.session_id in self._records or session.session_id in self._capacity_calls:
                return "duplicate"
            owned = sum(
                owner == session.physical_owner_id
                for owner, _epoch in self._capacity_calls.values()
            )
            if (
                len(self._capacity_calls) >= maximum_sessions
                or len(self._capacity_calls) + len(self._capacity_models)
                >= self._capacity_model_quota
                or owned >= sessions_per_owner
            ):
                return "unavailable"
            self._capacity_calls[session.session_id] = (
                session.physical_owner_id,
                session.connection_epoch,
            )
            return "reserved"

    async def release_capacity(self, session_id: str, *, physical_owner_id: str) -> None:
        async with self._lock:
            reservation = self._capacity_calls.get(session_id)
            if reservation is None:
                return
            document = self._records.get(session_id)
            current = metadata_model(document) if document is not None else None
            if (
                current is None
                or current.state != LifecycleState.ENDED
                or reservation != (physical_owner_id, current.connection_epoch)
                or current.physical_owner_id != physical_owner_id
            ):
                raise SessionConflict("capacity_closure_not_confirmed")
            del self._capacity_calls[session_id]

    async def reserve_model_capacity(
        self, session_id: str, *, physical_owner_id: str, reservation: str
    ) -> bool:
        if not isinstance(reservation, str) or not re.fullmatch(r"[a-f0-9]{32}", reservation):
            raise ValueError("invalid_model_reservation")
        async with self._lock:
            instant = self._capacity_now()
            document = self._records.get(session_id)
            current = metadata_model(document) if document is not None else None
            if (
                current is None
                or current.state == LifecycleState.ENDED
                or current.physical_owner_id != physical_owner_id
                or self._capacity_owner_leases.get(physical_owner_id, instant) <= instant
                or current.lease_until <= instant
                or self._capacity_calls.get(session_id)
                != (physical_owner_id, current.connection_epoch)
                or self._capacity_model_quota is None
            ):
                raise SessionConflict("model_capacity_not_owned")
            binding = (session_id, physical_owner_id, current.connection_epoch)
            if reservation in self._capacity_models:
                if self._capacity_models[reservation] != binding:
                    raise SessionConflict("model_capacity_not_owned")
                return True
            if len(self._capacity_calls) + len(self._capacity_models) >= self._capacity_model_quota:
                return False
            self._capacity_models[reservation] = binding
            return True

    async def release_model_capacity(
        self, reservation: str, *, session_id: str, physical_owner_id: str
    ) -> None:
        async with self._lock:
            binding = self._capacity_models.get(reservation)
            if binding is None:
                return
            document = self._records.get(session_id)
            current = metadata_model(document) if document is not None else None
            if (
                current is None
                or binding != (session_id, physical_owner_id, current.connection_epoch)
                or current.physical_owner_id != physical_owner_id
            ):
                raise SessionConflict("model_capacity_not_owned")
            del self._capacity_models[reservation]

    async def set_model_capacity_quota(self, quota: int) -> None:
        if type(quota) is not int or not 0 <= quota <= 10000:
            raise ValueError("invalid_model_session_quota")
        async with self._lock:
            self._capacity_model_quota = quota

    def _snapshot(self, session: CallSession) -> dict[str, Any]:
        self._version += 1
        return {**session.to_document(), "_etag": f'"local-{self._version}"'}

    async def create(self, metadata: Mapping[str, Any]) -> bool:
        session = metadata_model(metadata)
        async with self._lock:
            if session.session_id in self._records:
                return False
            reservation = self._capacity_calls.get(session.session_id)
            if reservation is not None:
                instant = self._capacity_now()
                expiry = self._capacity_owner_leases.get(session.physical_owner_id, instant)
                if (
                    reservation != (session.physical_owner_id, session.connection_epoch)
                    or expiry <= instant
                ):
                    raise PermissionError("owner_lease_lost")
                session = session.model_copy(
                    update={"lease_until": min(session.lease_until, expiry)}
                )
            self._records[session.session_id] = self._snapshot(session)
            return True

    async def read(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            document = self._records.get(session_id)
            return copy.deepcopy(document) if document is not None else None

    async def replace(self, session_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        candidate = metadata_model(metadata)
        async with self._lock:
            document = self._records.get(session_id)
            current = metadata_model(document) if document is not None else None
            verify_mutation(current, candidate, session_id)
            self._records[session_id] = self._snapshot(candidate)
            return copy.deepcopy(self._records[session_id])

    async def commit_handover(
        self, session_id: str, metadata: Mapping[str, Any], record: HandoverRecord
    ) -> dict[str, Any]:
        candidate = metadata_model(metadata)
        async with self._lock:
            document = self._records.get(session_id)
            current = metadata_model(document) if document is not None else None
            verify_handover(current, candidate, session_id, record)
            key = session_id, record.handover_id
            if key in self._handovers:
                raise SessionConflict("duplicate_handover")
            self._records[session_id] = self._snapshot(candidate)
            self._handovers[key] = record.to_document()
            return copy.deepcopy(self._records[session_id])

    async def handovers(self, session_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return copy.deepcopy(
                [record for (owner, _), record in self._handovers.items() if owner == session_id]
            )


class CosmosSessionRepository:
    """Async point operations against an explicitly injected runtime-owned container."""

    def __init__(self, container: ContainerPort) -> None:
        self._container = container

    async def create(self, metadata: Mapping[str, Any]) -> bool:
        session = metadata_model(metadata)
        try:
            await self._container.create_item(
                session.to_document(), enable_automatic_id_generation=False
            )
            return True
        except HttpResponseError as error:
            if error.status_code == 409:
                return False
            raise RuntimeError("session_store_unavailable") from None
        except AzureError:
            raise RuntimeError("session_store_unavailable") from None

    async def read(self, session_id: str) -> dict[str, Any] | None:
        try:
            document = await self._container.read_item(item=session_id, partition_key=session_id)
        except HttpResponseError as error:
            if error.status_code == 404:
                return None
            raise RuntimeError("session_store_unavailable") from None
        except AzureError:
            raise RuntimeError("session_store_unavailable") from None
        session = metadata_model(document)
        if session.session_id != session_id or session.etag is None:
            raise RuntimeError("session_store_response_invalid")
        return {**session.to_document(), "_etag": session.etag}

    async def replace(self, session_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        candidate = metadata_model(metadata)
        document = await self.read(session_id)
        current = metadata_model(document) if document is not None else None
        verify_mutation(current, candidate, session_id)
        assert candidate.etag is not None
        try:
            response = await self._container.replace_item(
                item=session_id,
                body=candidate.to_document(),
                etag=candidate.etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except HttpResponseError as error:
            if error.status_code in {404, 409, 412}:
                raise SessionConflict("session_etag_conflict") from None
            raise RuntimeError("session_store_unavailable") from None
        except AzureError:
            raise RuntimeError("session_store_unavailable") from None
        stored = metadata_model(response)
        if stored.etag is None:
            raise RuntimeError("session_store_response_invalid")
        return {**stored.to_document(), "_etag": stored.etag}

    async def commit_handover(
        self, session_id: str, metadata: Mapping[str, Any], record: HandoverRecord
    ) -> dict[str, Any]:
        candidate = metadata_model(metadata)
        document = await self.read(session_id)
        current = metadata_model(document) if document is not None else None
        verify_handover(current, candidate, session_id, record)
        operations: list[BatchOperation] = [
            ("replace", (session_id, candidate.to_document()), {"if_match_etag": candidate.etag}),
            ("create", (record.to_document(),)),
        ]
        try:
            responses = await self._container.execute_item_batch(
                batch_operations=operations, partition_key=session_id, retry_write=0
            )
        except CosmosBatchOperationError as error:
            if error.status_code in {404, 409, 412}:
                raise SessionConflict("handover_batch_conflict") from None
            raise RuntimeError("session_store_unavailable") from None
        except HttpResponseError as error:
            if error.status_code in {404, 409, 412}:
                raise SessionConflict("handover_batch_conflict") from None
            raise RuntimeError("session_store_unavailable") from None
        except AzureError:
            raise RuntimeError("session_store_unavailable") from None
        if (
            len(responses) != 2
            or not isinstance(responses[0].get("eTag"), str)
            or not responses[0]["eTag"]
        ):
            raise RuntimeError("session_store_response_invalid")
        return {**candidate.to_document(), "_etag": responses[0]["eTag"]}
