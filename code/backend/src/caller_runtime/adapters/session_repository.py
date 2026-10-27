"""Runtime-owned metadata repositories; no identity discovery or client construction."""

import asyncio
import copy
from collections.abc import Mapping
from typing import Any, Protocol

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
    """Conditional deterministic stand-in; not production persistence or Cosmos evidence."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._handovers: dict[tuple[str, str], dict[str, Any]] = {}
        self._version = 0
        self._lock = asyncio.Lock()

    def _snapshot(self, session: CallSession) -> dict[str, Any]:
        self._version += 1
        return {**session.to_document(), "_etag": f'"local-{self._version}"'}

    async def create(self, metadata: Mapping[str, Any]) -> bool:
        session = metadata_model(metadata)
        async with self._lock:
            if session.session_id in self._records:
                return False
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
