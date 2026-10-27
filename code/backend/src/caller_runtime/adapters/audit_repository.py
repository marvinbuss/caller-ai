"""Runtime-owned metadata audit storage with no shared management access or credential discovery."""

import asyncio
import json
from typing import Any, Protocol

from azure.cosmos.exceptions import CosmosHttpResponseError

from caller_runtime.domain.audit import AuditRecord


class AuditContainer(Protocol):
    async def create_item(self, *, body: dict[str, Any], retry_write: int) -> dict[str, Any]: ...
    async def read_item(self, *, item: str, partition_key: str) -> dict[str, Any]: ...


def encoded(record: AuditRecord) -> bytes:
    validated = AuditRecord.model_validate_json(
        record.model_dump_json(by_alias=True, warnings=False)
    )
    return validated.model_dump_json(by_alias=True).encode("utf-8")


class LocalAuditRepository:
    def __init__(self) -> None:
        self._records: dict[str, bytes] = {}

    @property
    def records(self) -> dict[str, dict[str, Any]]:
        return {key: json.loads(value) for key, value in self._records.items()}

    async def append(self, record: AuditRecord) -> bool:
        content = encoded(record)
        identifier = record.day_bucket + "/" + record.id
        if identifier in self._records:
            if self._records[identifier] != content:
                raise ValueError("audit_identity_conflict")
            return False
        self._records[identifier] = content
        return True


class CosmosAuditRepository:
    def __init__(self, *, container: AuditContainer) -> None:
        self._container = container

    async def append(self, record: AuditRecord) -> bool:
        try:
            content = encoded(record)
            async with asyncio.timeout(2):
                try:
                    await self._container.create_item(body=json.loads(content), retry_write=0)
                    return True
                except CosmosHttpResponseError as error:
                    if error.status_code != 409:
                        raise
                    document = await self._container.read_item(
                        item=record.id, partition_key=record.day_bucket
                    )
                    metadata = {"_rid", "_self", "_etag", "_attachments", "_ts"}
                    saved = AuditRecord.model_validate_json(
                        json.dumps(
                            {key: value for key, value in document.items() if key not in metadata}
                        )
                    )
                    if encoded(saved) != encoded(record):
                        raise ValueError("audit_identity_conflict") from None
                    return False
        except Exception:
            raise RuntimeError("runtime_audit_store_unavailable") from None
