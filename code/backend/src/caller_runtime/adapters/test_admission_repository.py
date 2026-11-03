"""Strong fixed-partition browser authority; no clients, TTL reclaim or implicit reset."""

import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError

from caller_runtime.adapters.session_repository import ContainerPort
from caller_runtime.domain.configuration import ETAG
from caller_runtime.domain.conversation import TestAuthority

AUTHORITY_ID: Final = "browser-test-authority-v1"
COSMOS_METADATA = frozenset({"_etag", "_rid", "_self", "_attachments", "_ts"})


class AuthorityUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("test_authority_unavailable")


class AuthorityConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("test_authority_conflict")


@dataclass(frozen=True, slots=True)
class AuthoritySnapshot:
    etag: str
    _content: bytes = field(repr=False)

    @property
    def authority(self) -> TestAuthority:
        return TestAuthority.model_validate_json(self._content)


def encoded(document: object) -> bytes:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def snapshot(document: Mapping[str, Any]) -> AuthoritySnapshot:
    etag = document.get("_etag")
    if not isinstance(etag, str) or not ETAG.fullmatch(etag) or document.get("ttl") != -1:
        raise AuthorityUnavailable()
    clean = {key: value for key, value in document.items() if key not in COSMOS_METADATA}
    content = encoded(clean)
    if len(content) > 128 * 1024:
        raise AuthorityUnavailable()
    model = TestAuthority.model_validate_json(content)
    return AuthoritySnapshot(etag, model.model_dump_json(by_alias=True).encode("utf-8"))


def verify_transition(current: TestAuthority, candidate: TestAuthority) -> None:
    if candidate.utc_high_water < current.utc_high_water:
        raise AuthorityConflict()
    if any(
        expiry > candidate.utc_high_water and candidate.consumed_nonces.get(digest) != expiry
        for digest, expiry in current.consumed_nonces.items()
    ):
        raise AuthorityConflict()
    incoming = {slot.session_id: slot for slot in candidate.slots}
    immutable = (
        "actor_key",
        "request_id",
        "mode",
        "owner_id",
        "owner_epoch",
        "revision_id",
        "graph_hash",
        "policy_hash",
        "started_at",
        "admission_deadline_utc",
        "expires_at",
    )
    for previous in current.slots:
        updated = incoming.get(previous.session_id)
        if updated is None:
            if not previous.terminal_fence or previous.cleanup_evidence is None:
                raise AuthorityConflict()
            continue
        if (
            any(getattr(updated, key) != getattr(previous, key) for key in immutable)
            or updated.auth_expires_at > previous.auth_expires_at
            or updated.generation < previous.generation
            or updated.agent_epoch < previous.agent_epoch
            or updated.attempted_through < previous.attempted_through
            or updated.attempted_through > previous.attempted_through + 1
            or updated.settled_through < previous.settled_through
            or (previous.ready_at is not None and updated.ready_at != previous.ready_at)
            or (previous.terminal_fence and not updated.terminal_fence)
            or (previous.live_ever_attempted and not updated.live_ever_attempted)
            or updated.closed_through_generation < previous.closed_through_generation
        ):
            raise AuthorityConflict()
        if previous.terminal_fence and (
            updated.active_agent_id != previous.active_agent_id
            or updated.active_community_id != previous.active_community_id
            or updated.last_user_at != previous.last_user_at
            or updated.idle_expires_at != previous.idle_expires_at
        ):
            raise AuthorityConflict()
        for kind, old_progress in previous.resources.items():
            progress = updated.resources[kind]
            if (
                progress.attempted < old_progress.attempted
                or progress.settled < old_progress.settled
            ):
                raise AuthorityConflict()
            if progress.attempted > old_progress.attempted:
                if (
                    previous.terminal_fence
                    or old_progress.attempted != old_progress.settled
                    or progress.attempted != previous.attempted_through + 1
                    or progress.settled != old_progress.settled
                ):
                    raise AuthorityConflict()
            elif progress.attempt != old_progress.attempt:
                old_attempt, attempt = old_progress.attempt, progress.attempt
                if (
                    kind != "live"
                    or old_attempt is None
                    or attempt is None
                    or old_attempt.provider_session_id is not None
                    or attempt.provider_session_id is None
                    or attempt.model_copy(update={"provider_session_id": None}) != old_attempt
                ):
                    raise AuthorityConflict()
            if (
                progress.settled == old_progress.settled
                and progress.settlement_ref != old_progress.settlement_ref
            ):
                raise AuthorityConflict()


class TestAdmissionRepository:
    __test__ = False

    def __init__(self, *, container: ContainerPort) -> None:
        if container is None:
            raise ValueError("explicit_runtime_authority_container_required")
        self._container = container

    @staticmethod
    def _budget(deadline: float) -> None:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise AuthorityUnavailable()
        if not 0 < deadline - asyncio.get_running_loop().time() <= 10:
            raise AuthorityUnavailable()

    async def read(self, *, deadline: float) -> AuthoritySnapshot:
        try:
            self._budget(deadline)
            async with asyncio.timeout_at(deadline):
                document = await self._container.read_item(
                    item=AUTHORITY_ID,
                    partition_key=AUTHORITY_ID,
                    initial_headers={"x-ms-consistency-level": "Strong"},
                )
                return snapshot(document)
        except Exception:
            raise AuthorityUnavailable() from None

    async def initialize(self, *, now: datetime, deadline: float) -> AuthoritySnapshot:
        try:
            self._budget(deadline)
            model = TestAuthority(
                id=AUTHORITY_ID,
                sessionId=AUTHORITY_ID,
                schemaVersion="1.0.0",
                utcHighWater=now,
                slots=[],
                consumedNonces={},
            )
            async with asyncio.timeout_at(deadline):
                try:
                    document = await self._container.create_item(
                        model.model_dump(mode="json", by_alias=True),
                        enable_automatic_id_generation=False,
                        retry_write=0,
                    )
                except HttpResponseError as error:
                    if error.status_code != 409:
                        raise
                    return await self.read(deadline=deadline)
                return snapshot(document)
        except Exception:
            raise AuthorityUnavailable() from None

    async def replace(
        self, current: AuthoritySnapshot, candidate: TestAuthority, *, deadline: float
    ) -> AuthoritySnapshot:
        try:
            self._budget(deadline)
            checked = TestAuthority.model_validate_json(candidate.model_dump_json(by_alias=True))
            verify_transition(current.authority, checked)
            async with asyncio.timeout_at(deadline):
                document = await self._container.replace_item(
                    item=AUTHORITY_ID,
                    body=checked.model_dump(mode="json", by_alias=True),
                    etag=current.etag,
                    match_condition=MatchConditions.IfNotModified,
                    retry_write=0,
                )
                return snapshot(document)
        except AuthorityConflict:
            raise
        except HttpResponseError as error:
            if error.status_code in {404, 409, 412}:
                raise AuthorityConflict() from None
            raise AuthorityUnavailable() from None
        except Exception:
            raise AuthorityUnavailable() from None
