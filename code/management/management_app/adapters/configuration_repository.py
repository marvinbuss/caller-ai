"""Cosmos persistence for configuration revisions, the permission authority and security audit.

Publication, capability admission and revocation each commit as one transactional batch in a single
partition, so readers never see a half-published revision or a revocation without its audit outbox
entry. Writes are ETag-conditioned and a conflict raises `PermissionConflict` or a rejected
publication instead of overwriting. The container
is injected by the reviewed composition; this module never builds a client or looks for credentials.
Storage errors surface as fixed codes without document content.
"""

import asyncio
import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from azure.cosmos.exceptions import CosmosBatchOperationError, CosmosHttpResponseError

from management_app.models.configuration import (
    ActiveConfiguration,
    ConfigurationRevision,
)
from management_app.models.permissions import (
    AUTHORITY_LIMIT,
    CapabilityAdmission,
    CapabilityAttempt,
    PermissionAuthority,
    PermissionConflict,
    RevocationRequest,
)
from management_app.services.audit import AuditRecord, revocation_record, validate_retention
from management_app.services.configuration_publication import (
    ConfigurationRejected as ConfigurationRejected,
)
from management_app.services.configuration_publication import (
    publication as publication,
)
from management_app.services.configuration_validation import (
    GRAPH_FIELDS,
    SNAPSHOT_LIMIT,
    ConfigurationValidator,
    encoded,
)

Document = dict[str, Any]
Publication = tuple[Document, str]
BatchOperation = tuple[str, tuple[Any, ...]] | tuple[str, tuple[Any, ...], dict[str, Any]]
COSMOS_METADATA = frozenset({"_rid", "_self", "_attachments", "_ts", "_etag"})


def security_audit_document(authority: Document) -> Document:
    record = revocation_record(authority).model_dump(mode="json")
    return {
        "id": record["id"],
        "organizationId": "default",
        "recordType": "security-audit-outbox-v1",
        "record": record,
        "ttl": -1,
    }


def security_audit_record(document: Document | None, identifier: str) -> Document:
    if document is None:
        raise RuntimeError("security_audit_unavailable")
    clean = {key: value for key, value in document.items() if key not in COSMOS_METADATA}
    if (
        set(clean) != {"id", "organizationId", "recordType", "record", "ttl"}
        or clean["id"] != identifier
        or clean["organizationId"] != "default"
        or clean["recordType"] != "security-audit-outbox-v1"
        or type(clean["ttl"]) is not int
        or (clean["ttl"] != -1 and not 3600 <= clean["ttl"] <= 2147483647)
    ):
        raise RuntimeError("security_audit_invalid")
    record = AuditRecord.model_validate_json(encoded(clean["record"]))
    if record.id != identifier or record.action != "security.revoke" or record.outcome != "success":
        raise RuntimeError("security_audit_invalid")
    return record.model_dump(mode="json")


class ContainerPort(Protocol):
    async def read_item(
        self, *, item: str, partition_key: str, initial_headers: dict[str, str]
    ) -> Document: ...

    async def execute_item_batch(
        self, *, batch_operations: list[BatchOperation], partition_key: str, retry_write: int
    ) -> list[Document]: ...


def authority_change(
    document: Document,
    *,
    if_match: str,
    actor_id: str,
    request: CapabilityAttempt | None = None,
    revocations: Document | None = None,
) -> tuple[Document, CapabilityAdmission | None]:
    if document.get("_etag") != if_match:
        raise PermissionConflict("permission_authority_conflict")
    clean = {key: value for key, value in document.items() if key not in COSMOS_METADATA}
    authority = PermissionAuthority.model_validate_json(encoded(clean))
    values = authority.model_dump(mode="json", by_alias=True)
    admission = None
    now = datetime.now(UTC)
    if request is not None:
        values["admissionSequence"] += 1
        admission = CapabilityAdmission.model_validate(
            request.model_dump(exclude={"inputs", "deadline_at"})
            | {
                "admission_id": "admission-" + uuid4().hex,
                "admission_sequence": values["admissionSequence"],
                "policy_version": authority.policy_version,
                "admitted_at": now,
                "actor_id": actor_id,
            }
        )
    elif revocations is not None:
        update = RevocationRequest.model_validate(revocations)
        values["disabledToolIds"] = sorted(
            set(authority.disabled_tool_ids) | set(update.disabled_tool_ids)
        )
        values["disabledConnectionIds"] = sorted(
            set(authority.disabled_connection_ids) | set(update.disabled_connection_ids)
        )
        revoked = {
            (item.agent_id, item.tool_id, item.action_name)
            for item in authority.revoked_assignments + update.revoked_assignments
        }
        values["revokedAssignments"] = [
            {"agentId": agent, "toolId": tool, "actionName": action}
            for agent, tool, action in sorted(revoked)
        ]
        values["policyVersion"] += 1
    else:
        raise ValueError("permission_mutation_required")
    values.update(updatedAt=now.isoformat(), updatedBy=actor_id)
    return PermissionAuthority.model_validate_json(encoded(values)).model_dump(
        mode="json", by_alias=True
    ), admission


def graph_document(revision: Document) -> Document:
    return {key: copy.deepcopy(value) for key, value in revision.items() if key in GRAPH_FIELDS}


def verified_revision(document: Document) -> Document:
    clean = {key: value for key, value in document.items() if key not in COSMOS_METADATA}
    model = ConfigurationRevision.model_validate_json(encoded(clean))
    snapshot = model.model_dump(mode="json", by_alias=True)
    if len(encoded(snapshot)) > SNAPSHOT_LIMIT:
        raise ValueError("invalid_snapshot")
    # A later field default changes the normalized graph; the graph as stored also counts.
    if model.graph_hash not in {
        hashlib.sha256(encoded(graph_document(snapshot))).hexdigest(),
        hashlib.sha256(encoded(graph_document(clean))).hexdigest(),
    }:
        raise ValueError("invalid_snapshot")
    return snapshot


class LocalConfigurationRepository:
    def __init__(self, *, validator: ConfigurationValidator) -> None:
        self.validator = validator
        self._lock = asyncio.Lock()
        self._state: tuple[bytes, bytes, bytes] | None = None
        self._revisions: dict[str, bytes] = {}
        self._admissions: list[bytes] = []
        self._security_audits: dict[str, bytes] = {}

    async def read_active(self) -> Publication | None:
        state = self._state
        if state is None:
            return None
        return json.loads(state[0]), json.loads(state[1])["_etag"]

    async def read_authority(self) -> Document:
        if self._state is None:
            raise RuntimeError("configuration_not_initialized")
        authority: Document = json.loads(self._state[2])
        return authority

    def _commit(self, payload: Document, actor: str, actor_name: str = "") -> Publication:
        previous = json.loads(self._state[0]) if self._state else None
        authority = json.loads(self._state[2]) if self._state else None
        snapshot, pointer, permission = publication(
            self.validator,
            payload,
            actor=actor,
            previous=previous,
            authority=authority,
            actor_name=actor_name,
        )
        pointer["_etag"] = '"local-' + uuid4().hex + '"'
        permission["_etag"] = '"local-' + uuid4().hex + '"'
        self._state = encoded(snapshot), encoded(pointer), encoded(permission)
        self._revisions[snapshot["id"]] = self._state[0]
        return copy.deepcopy(snapshot), pointer["_etag"]

    async def read_revision(self, revision_id: str) -> Document | None:
        document = self._revisions.get(revision_id)
        return json.loads(document) if document is not None else None

    async def admit_capability(
        self, request: CapabilityAttempt, *, if_match: str, actor_id: str
    ) -> CapabilityAdmission:
        async with self._lock:
            document, admission = authority_change(
                await self.read_authority(), if_match=if_match, actor_id=actor_id, request=request
            )
            assert admission is not None and self._state is not None
            document["_etag"] = '"local-' + uuid4().hex + '"'
            self._state = self._state[0], self._state[1], encoded(document)
            self._admissions.append(encoded(admission.model_dump(mode="json", by_alias=True)))
            return admission

    async def revoke_capabilities(
        self, revocations: Document, *, if_match: str, actor_id: str
    ) -> Document:
        async with self._lock:
            document, _ = authority_change(
                await self.read_authority(),
                if_match=if_match,
                actor_id=actor_id,
                revocations=revocations,
            )
            assert self._state is not None
            audit = security_audit_document(document)
            document["_etag"] = '"local-' + uuid4().hex + '"'
            self._state = self._state[0], self._state[1], encoded(document)
            self._security_audits[audit["id"]] = encoded(audit)
            return copy.deepcopy(document)

    async def read_revocation_audit(self, identifier: str) -> Document:
        if not re.fullmatch(r"audit-security-[1-9][0-9]{0,18}", identifier):
            raise ValueError("invalid_security_audit_reference")
        body = self._security_audits.get(identifier)
        return security_audit_record(json.loads(body) if body is not None else None, identifier)

    async def acknowledge_revocation_audit(
        self, identifier: str, *, retention_seconds: int
    ) -> None:
        validate_retention(retention_seconds)
        async with self._lock:
            await self.read_revocation_audit(identifier)
            body = json.loads(self._security_audits[identifier])
            if body["ttl"] not in {-1, retention_seconds}:
                raise ValueError("security_audit_retention_binding_conflict")
            if body["ttl"] == -1:
                body["ttl"] = retention_seconds
                self._security_audits[identifier] = encoded(body)

    async def initialize(self, payload: Document, *, actor_id: str) -> Publication:
        async with self._lock:
            current = await self.read_active()
            return current if current is not None else self._commit(payload, actor_id)

    async def publish(
        self, payload: Document, *, if_match: str, actor_id: str, actor_name: str = ""
    ) -> Publication | None:
        async with self._lock:
            current = await self.read_active()
            if current is None or current[1] != if_match:
                return None
            return self._commit(payload, actor_id, actor_name)


class CosmosConfigurationRepository:
    def __init__(self, *, container: ContainerPort, validator: ConfigurationValidator) -> None:
        self.container = container
        self.validator = validator

    async def _read(self, identifier: str) -> Document | None:
        try:
            return await self.container.read_item(
                item=identifier,
                partition_key="default",
                initial_headers={"x-ms-consistency-level": "Strong"},
            )
        except CosmosHttpResponseError as error:
            if error.status_code == 404:
                return None
            raise

    async def _active(self) -> Publication | None:
        pointer = await self._read("active")
        if pointer is None:
            return None
        clean = {
            key: value
            for key, value in pointer.items()
            if key not in COSMOS_METADATA or key == "_etag"
        }
        active = ActiveConfiguration.model_validate_json(encoded(clean))
        document = await self._read(active.revision_id)
        if document is None:
            raise ValueError("invalid_active_revision")
        snapshot = verified_revision(document)
        if snapshot["revisionNumber"] != active.revision_number:
            raise ValueError("invalid_active_revision")
        return snapshot, active.etag

    async def read_active(self) -> Publication | None:
        try:
            async with asyncio.timeout(5):
                return await self._active()
        except Exception:
            raise RuntimeError("configuration_store_unavailable") from None

    async def _authority(self) -> Document:
        document = await self._read("security-authority")
        if document is None or not isinstance(document.get("_etag"), str):
            raise ValueError("invalid_permission_authority")
        clean = {key: value for key, value in document.items() if key not in COSMOS_METADATA}
        if len(encoded(clean)) > AUTHORITY_LIMIT:
            raise ValueError("invalid_permission_authority")
        authority = PermissionAuthority.model_validate_json(encoded(clean)).model_dump(
            mode="json", by_alias=True
        )
        if authority["id"] != "security-authority" or authority["organizationId"] != "default":
            raise ValueError("invalid_permission_authority")
        return {**authority, "_etag": document["_etag"]}

    async def read_authority(self) -> Document:
        try:
            async with asyncio.timeout(5):
                return await self._authority()
        except Exception:
            raise RuntimeError("configuration_store_unavailable") from None

    async def read_revision(self, revision_id: str) -> Document | None:
        try:
            async with asyncio.timeout(5):
                document = await self._read(revision_id)
                return verified_revision(document) if document is not None else None
        except Exception:
            raise RuntimeError("configuration_store_unavailable") from None

    async def _mutate_authority(
        self,
        *,
        if_match: str,
        actor_id: str,
        request: CapabilityAttempt | None = None,
        revocations: Document | None = None,
    ) -> tuple[Document, CapabilityAdmission | None]:
        try:
            async with asyncio.timeout(5):
                document, admission = authority_change(
                    await self._authority(),
                    if_match=if_match,
                    actor_id=actor_id,
                    request=request,
                    revocations=revocations,
                )
                operations: list[BatchOperation] = [
                    ("replace", ("security-authority", document), {"if_match_etag": if_match})
                ]
                if admission is not None:
                    body = admission.model_dump(mode="json", by_alias=True)
                    operations.append(
                        (
                            "create",
                            ({"id": admission.admission_id, "organizationId": "default", **body},),
                        )
                    )
                if revocations is not None:
                    operations.append(("create", (security_audit_document(document),)))
                responses = await self.container.execute_item_batch(
                    batch_operations=operations, partition_key="default", retry_write=0
                )
                etag = responses[0].get("eTag")
                if len(responses) != len(operations) or not isinstance(etag, str) or not etag:
                    raise ValueError("invalid_authority_response")
                return {**document, "_etag": etag}, admission
        except PermissionConflict:
            raise
        except CosmosBatchOperationError as error:
            if error.status_code == 412:
                raise PermissionConflict("permission_authority_conflict") from None
            raise RuntimeError("permission_authority_unavailable") from None
        except Exception:
            raise RuntimeError("permission_authority_unavailable") from None

    async def admit_capability(
        self, request: CapabilityAttempt, *, if_match: str, actor_id: str
    ) -> CapabilityAdmission:
        _, admission = await self._mutate_authority(
            if_match=if_match, actor_id=actor_id, request=request
        )
        assert admission is not None
        return admission

    async def revoke_capabilities(
        self, revocations: Document, *, if_match: str, actor_id: str
    ) -> Document:
        document, _ = await self._mutate_authority(
            if_match=if_match, actor_id=actor_id, revocations=revocations
        )
        return document

    async def read_revocation_audit(self, identifier: str) -> Document:
        if not re.fullmatch(r"audit-security-[1-9][0-9]{0,18}", identifier):
            raise ValueError("invalid_security_audit_reference")
        try:
            async with asyncio.timeout(5):
                return security_audit_record(await self._read(identifier), identifier)
        except Exception:
            raise RuntimeError("security_audit_unavailable") from None

    async def acknowledge_revocation_audit(
        self, identifier: str, *, retention_seconds: int
    ) -> None:
        validate_retention(retention_seconds)
        if not re.fullmatch(r"audit-security-[1-9][0-9]{0,18}", identifier):
            raise ValueError("invalid_security_audit_reference")
        try:
            async with asyncio.timeout(5):
                stored = await self._read(identifier)
                security_audit_record(stored, identifier)
                assert stored is not None
                if stored["ttl"] not in {-1, retention_seconds}:
                    raise ValueError("security_audit_retention_binding_conflict")
                if stored["ttl"] == retention_seconds:
                    return
                if not isinstance(stored.get("_etag"), str):
                    raise ValueError("security_audit_etag_required")
                body = {key: value for key, value in stored.items() if key not in COSMOS_METADATA}
                body["ttl"] = retention_seconds
                responses = await self.container.execute_item_batch(
                    batch_operations=[
                        ("replace", (identifier, body), {"if_match_etag": stored["_etag"]})
                    ],
                    partition_key="default",
                    retry_write=0,
                )
                if len(responses) != 1 or not isinstance(responses[0].get("eTag"), str):
                    raise ValueError("invalid_security_audit_acknowledgement")
        except Exception:
            raise RuntimeError("security_audit_unavailable") from None

    async def initialize(self, payload: Document, *, actor_id: str) -> Publication:
        try:
            async with asyncio.timeout(5):
                current = await self._active()
                if current is not None:
                    return current
                snapshot, pointer, authority = publication(
                    self.validator, payload, actor=actor_id, previous=None, authority=None
                )
                operations: list[BatchOperation] = [
                    ("create", (snapshot,)),
                    ("create", (pointer,)),
                    ("create", (authority,)),
                ]
                try:
                    responses = await self.container.execute_item_batch(
                        batch_operations=operations, partition_key="default", retry_write=0
                    )
                except CosmosBatchOperationError as error:
                    if error.status_code != 409:
                        raise
                    current = await self._active()
                    if current is None:
                        raise ValueError("invalid_initialization") from None
                    return current
                return copy.deepcopy(snapshot), str(responses[1]["eTag"])
        except ConfigurationRejected:
            raise
        except Exception:
            raise RuntimeError("configuration_store_unavailable") from None

    async def publish(
        self, payload: Document, *, if_match: str, actor_id: str, actor_name: str = ""
    ) -> Publication | None:
        try:
            async with asyncio.timeout(5):
                for _attempt in range(3):
                    current = await self._active()
                    if current is None or current[1] != if_match:
                        return None
                    authority = await self._authority()
                    snapshot, pointer, permission = publication(
                        self.validator,
                        payload,
                        actor=actor_id,
                        previous=current[0],
                        authority=authority,
                        actor_name=actor_name,
                    )
                    operations: list[BatchOperation] = [
                        ("create", (snapshot,)),
                        ("replace", ("active", pointer), {"if_match_etag": if_match}),
                        (
                            "replace",
                            ("security-authority", permission),
                            {"if_match_etag": authority["_etag"]},
                        ),
                    ]
                    try:
                        responses = await self.container.execute_item_batch(
                            batch_operations=operations, partition_key="default", retry_write=0
                        )
                    except CosmosBatchOperationError as error:
                        if error.status_code == 412 and error.error_index == 2:
                            continue
                        if error.status_code == 412 and error.error_index == 1:
                            return None
                        raise
                    return copy.deepcopy(snapshot), str(responses[1]["eTag"])
                raise ValueError("authority_conflict_exhausted")
        except ConfigurationRejected:
            raise
        except Exception:
            raise RuntimeError("configuration_store_unavailable") from None
