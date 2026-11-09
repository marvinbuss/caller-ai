"""Fake Azure ports: the stores keep the conditional-write behaviour the repositories rely on."""

import asyncio
import copy
from dataclasses import dataclass, field
from typing import Any

from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosBatchOperationError,
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)


@dataclass
class ConfigurationStore:
    """The management `configuration` container: Strong reads and atomic batches on one key."""

    items: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0

    async def read_item(self, *, item: str, partition_key: str, initial_headers: dict) -> Any:
        assert partition_key == "default"
        await asyncio.sleep(0)
        if item not in self.items:
            raise CosmosResourceNotFoundError(status_code=404, message="not found")
        return copy.deepcopy(self.items[item])

    async def execute_item_batch(
        self, *, batch_operations: list, partition_key: str, retry_write: int
    ) -> list[dict[str, Any]]:
        assert partition_key == "default" and retry_write == 0
        candidate = copy.deepcopy(self.items)
        responses = []
        for index, operation in enumerate(batch_operations):
            action, arguments = operation[:2]
            options = operation[2] if len(operation) == 3 else {}
            document = copy.deepcopy(arguments[0] if action == "create" else arguments[1])
            identifier = document["id"]
            status = 201 if action == "create" else 200
            if action == "create" and identifier in candidate:
                status = 409
            elif (
                action == "replace"
                and options.get("if_match_etag") != candidate[identifier]["_etag"]
            ):
                status = 412
            if status >= 400:
                raise CosmosBatchOperationError(
                    error_index=index, status_code=status, headers={}, message="rejected"
                )
            document["_etag"] = f'"e2e-{self.sequence + index + 1}"'
            candidate[identifier] = document
            responses.append({"statusCode": status, "eTag": document["_etag"]})
        self.items = candidate
        self.sequence += len(batch_operations)
        return responses


@dataclass
class AuditStore:
    records: list[dict[str, Any]] = field(default_factory=list)

    async def create_item(self, *, body: dict[str, Any], retry_write: int) -> dict[str, Any]:
        self.records.append(copy.deepcopy(body))
        return body


@dataclass
class RuntimeStore:
    """A runtime container with etag-conditioned replace (authority) or owner partitions."""

    partition_field: str | None = None
    records: dict[str, Any] = field(default_factory=dict)
    version: int = 0

    async def create_item(self, body: dict[str, Any], **keywords: Any) -> dict[str, Any]:
        if body["id"] in self.records:
            raise CosmosHttpResponseError(status_code=409)
        self.version += 1
        self.records[body["id"]] = {**copy.deepcopy(body), "_etag": f'"rt-{self.version}"'}
        return copy.deepcopy(self.records[body["id"]])

    async def read_item(self, item: str, partition_key: str, **keywords: Any) -> dict[str, Any]:
        stored = self.records.get(item)
        owned = self.partition_field is None or (
            stored is not None and stored.get(self.partition_field) == partition_key
        )
        if stored is None or not owned:
            raise CosmosHttpResponseError(status_code=404)
        return copy.deepcopy(stored)

    async def replace_item(
        self, item: str, body: dict[str, Any], *, etag: str, match_condition: Any, **keywords: Any
    ) -> dict[str, Any]:
        assert match_condition == MatchConditions.IfNotModified
        if self.records[item]["_etag"] != etag:
            raise CosmosHttpResponseError(status_code=412)
        self.version += 1
        self.records[item] = {**copy.deepcopy(body), "_etag": f'"rt-{self.version}"'}
        return copy.deepcopy(self.records[item])


class Cosmos:
    """The Cosmos client both apps build: containers by name, shared across restarts."""

    def __init__(self, containers: dict[str, Any]) -> None:
        self.containers = containers
        self.closed = 0

    def get_database_client(self, database: str) -> "Cosmos":
        return self

    def get_container_client(self, name: str) -> Any:
        return self.containers[name]

    async def close(self) -> None:
        self.closed += 1
