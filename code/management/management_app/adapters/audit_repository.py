"""Idempotent metadata-only audit stores; Cosmos ownership stays in management."""

import asyncio
import json
from typing import Protocol

from azure.cosmos.exceptions import CosmosHttpResponseError

from management_app.adapters.configuration_repository import Document
from management_app.services.audit import AuditRecord
from management_app.services.configuration_validation import encoded


class AuditContainer(Protocol):
    async def create_item(self, *, body: Document, retry_write: int) -> Document: ...


class LocalAuditRepository:
    def __init__(self) -> None:
        self._records: dict[str, bytes] = {}

    @property
    def records(self) -> dict[str, Document]:
        return {identifier: json.loads(value) for identifier, value in self._records.items()}

    async def append(self, record: AuditRecord) -> bool:
        if record.id in self._records:
            return False
        self._records[record.id] = encoded(record.model_dump(mode="json", by_alias=True))
        return True


class CosmosAuditRepository:
    def __init__(self, *, container: AuditContainer) -> None:
        self.container = container

    async def append(self, record: AuditRecord) -> bool:
        try:
            async with asyncio.timeout(2):
                await self.container.create_item(
                    body=record.model_dump(mode="json", by_alias=True), retry_write=0
                )
            return True
        except CosmosHttpResponseError as error:
            if error.status_code == 409:
                return False
            raise RuntimeError("audit_store_unavailable") from None
        except Exception:
            raise RuntimeError("audit_store_unavailable") from None
