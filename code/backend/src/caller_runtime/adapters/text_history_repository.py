"""Cosmos-backed store for text conversation history; no client or identity construction.

Items are addressed by the owner's key (the partition) and the conversation id, so another
manager's request can only ever see "not found". Every write carries an item expiry and an
etag condition. Failures surface as fixed codes and never include storage or content detail.
"""

import asyncio
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError

from caller_runtime.domain.configuration import ETAG
from caller_runtime.domain.conversation import utc
from caller_runtime.domain.ports import ContainerPort
from caller_runtime.domain.text_history import TextConversation

COSMOS_METADATA = frozenset({"_etag", "_rid", "_self", "_attachments", "_ts"})
PROBE_ITEM = "history-store-probe"
MAX_DOCUMENT_BYTES = 512 * 1024
# 10 s plus rounding: on Windows loop.time() ticks every 15.6 ms, so (t + 10) - t can exceed 10.
MAX_BUDGET_SECONDS = 10.001


@dataclass(frozen=True, slots=True)
class StoredConversation:
    conversation: TextConversation = field(repr=False)
    etag: str


class TextHistoryRepository:
    def __init__(
        self,
        *,
        container: ContainerPort,
        ttl_seconds: int,
        utc_now: Callable[[], datetime],
    ) -> None:
        if container is None or utc_now is None or type(ttl_seconds) is not int or ttl_seconds < 1:
            raise ValueError("explicit_text_history_store_required")
        self._container, self._ttl, self._utc_now = container, ttl_seconds, utc_now

    @staticmethod
    def _budget(deadline: float) -> None:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise PermissionError("history_unavailable")
        if not 0 < deadline - asyncio.get_running_loop().time() <= MAX_BUDGET_SECONDS:
            raise PermissionError("history_unavailable")

    def _body(self, conversation: TextConversation) -> dict[str, Any]:
        body = conversation.model_dump(mode="json", by_alias=True)
        body["ttl"] = self._ttl
        return body

    @staticmethod
    def _stored(document: Mapping[str, Any]) -> StoredConversation:
        etag = document.get("_etag")
        if not isinstance(etag, str) or not ETAG.fullmatch(etag):
            raise PermissionError("history_unavailable")
        clean = {
            key: value
            for key, value in document.items()
            if key not in COSMOS_METADATA and key != "ttl"
        }
        content = json.dumps(clean, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(content) > MAX_DOCUMENT_BYTES:
            raise PermissionError("history_unavailable")
        return StoredConversation(TextConversation.model_validate_json(content), etag)

    async def load(
        self, owner_key: str, conversation_id: str, *, deadline: float
    ) -> StoredConversation | None:
        """Return the owner's conversation, or None when it is unknown, expired or foreign."""
        try:
            self._budget(deadline)
            async with asyncio.timeout_at(deadline):
                document = await self._container.read_item(
                    item=conversation_id, partition_key=owner_key
                )
            stored = self._stored(document)
        except HttpResponseError as error:
            if error.status_code == 404:
                return None
            raise PermissionError("history_unavailable") from None
        except Exception:
            raise PermissionError("history_unavailable") from None
        if stored.conversation.owner_key != owner_key:
            return None
        return stored

    async def create(
        self, conversation: TextConversation, *, deadline: float
    ) -> StoredConversation:
        try:
            self._budget(deadline)
            async with asyncio.timeout_at(deadline):
                document = await self._container.create_item(
                    self._body(conversation),
                    enable_automatic_id_generation=False,
                    retry_write=0,
                )
            return self._stored(document)
        except HttpResponseError as error:
            if error.status_code == 409:
                raise PermissionError("work_busy") from None
            raise PermissionError("history_unavailable") from None
        except Exception:
            raise PermissionError("history_unavailable") from None

    async def replace(
        self,
        current: StoredConversation,
        conversation: TextConversation,
        *,
        deadline: float,
    ) -> StoredConversation:
        """Write the next state only if nobody changed the conversation since it was loaded."""
        if (
            conversation.id != current.conversation.id
            or conversation.owner_key != current.conversation.owner_key
        ):
            raise PermissionError("history_unavailable")
        try:
            self._budget(deadline)
            async with asyncio.timeout_at(deadline):
                document = await self._container.replace_item(
                    item=conversation.id,
                    body=self._body(conversation),
                    etag=current.etag,
                    match_condition=MatchConditions.IfNotModified,
                    retry_write=0,
                )
            return self._stored(document)
        except HttpResponseError as error:
            if error.status_code in {404, 409, 412}:
                raise PermissionError("work_busy") from None
            raise PermissionError("history_unavailable") from None
        except Exception:
            raise PermissionError("history_unavailable") from None

    async def probe(self, *, deadline: float) -> bool:
        """Report whether the store answers; a missing probe item still proves reachability."""
        try:
            self._budget(deadline)
            async with asyncio.timeout_at(deadline):
                await self._container.read_item(item=PROBE_ITEM, partition_key=PROBE_ITEM)
            return True
        except HttpResponseError as error:
            return error.status_code == 404
        except Exception:
            return False

    def now(self) -> datetime:
        return utc(self._utc_now())
