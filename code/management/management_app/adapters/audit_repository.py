"""Idempotent, metadata-only audit stores (in memory for local use, Cosmos for reviewed runs).

`append` returns True for a new record and False for an exact repeat; the same id with different
content raises `AuditIdentityConflict`, so a retry after an ambiguous write is safe. Cosmos items
carry a retention TTL and a day bucket partition, and a duplicate create (409) is confirmed with a
strongly consistent read. Records hold fixed metadata only, never configuration or conversation
content.
"""

import asyncio
import json
from typing import Protocol

from azure.cosmos.exceptions import CosmosHttpResponseError

from management_app.adapters.configuration_repository import COSMOS_METADATA, Document
from management_app.services.audit import AuditRecord, validate_retention
from management_app.services.configuration_validation import encoded


class AuditContainer(Protocol):
    async def create_item(self, *, body: Document, retry_write: int) -> Document: ...

    async def read_item(
        self, *, item: str, partition_key: str, initial_headers: dict[str, str]
    ) -> Document: ...


class AuditIdentityConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("audit_identity_conflict")


class LocalAuditRepository:
    def __init__(self) -> None:
        self._records: dict[str, bytes] = {}

    @property
    def records(self) -> dict[str, Document]:
        return {identifier: json.loads(value) for identifier, value in self._records.items()}

    async def append(self, record: AuditRecord) -> bool:
        record = AuditRecord.model_validate_json(record.model_dump_json(by_alias=True))
        content = encoded(record.model_dump(mode="json", by_alias=True))
        if record.id in self._records:
            if self._records[record.id] != content:
                raise AuditIdentityConflict()
            return False
        self._records[record.id] = content
        return True


class CosmosAuditRepository:
    def __init__(self, *, container: AuditContainer, retention_seconds: int) -> None:
        validate_retention(retention_seconds)
        self.container = container
        self._retention = retention_seconds

    async def append(self, record: AuditRecord) -> bool:
        try:
            record = AuditRecord.model_validate_json(record.model_dump_json(by_alias=True))
            day_bucket = record.timestamp.date().isoformat()
            document = record.model_dump(mode="json", by_alias=True) | {
                "dayBucket": day_bucket,
                "ttl": self._retention,
            }
            async with asyncio.timeout(2):
                try:
                    await self.container.create_item(body=document, retry_write=0)
                except CosmosHttpResponseError as error:
                    if error.status_code != 409:
                        raise
                    stored = await self.container.read_item(
                        item=record.id,
                        partition_key=day_bucket,
                        initial_headers={"x-ms-consistency-level": "Strong"},
                    )
                    clean = {
                        key: value for key, value in stored.items() if key not in COSMOS_METADATA
                    }
                    if clean != document:
                        raise AuditIdentityConflict() from None
                    return False
            return True
        except AuditIdentityConflict:
            raise
        except Exception:
            raise RuntimeError("audit_store_unavailable") from None
