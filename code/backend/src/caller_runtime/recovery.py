"""One-off release of test slots held only by a model request that was never settled.

Before the background-drain fix, a model request that outlived its work budget was never settled,
so cleanup could not prove the slot free and the same manager got `manager_busy` for good. This
releases only slots that are fenced, expired at least five minutes ago and whose only unsettled
attempt is a model request; Live and capability attempts must already be settled. It never creates
or deletes the authority item and never touches a slot that is still live. Preview mode, the
default, lists the candidates and writes nothing.

Run inside the runtime container, where the managed identity can reach the private store:
`python -m caller_runtime.recovery` (preview) or `python -m caller_runtime.recovery --apply`.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import uuid4

from caller_runtime.adapters.admission_repository import (
    AuthorityConflict,
    AuthoritySnapshot,
    TestAdmissionRepository,
)
from caller_runtime.domain.configuration import encoded
from caller_runtime.domain.conversation import (
    CleanupEvidence,
    ResourceKind,
    TestAuthority,
    TestSlot,
    utc,
)

GRACE: Final = timedelta(minutes=5)
RECOVERY_PREFIX: Final = "recovery-"
SETTLED_KINDS: Final[tuple[ResourceKind, ...]] = ("capability", "live")


def _recovered(slot: TestSlot) -> bool:
    evidence = slot.cleanup_evidence
    return evidence is not None and evidence.disposal_ref.startswith(RECOVERY_PREFIX)


def candidates(authority: TestAuthority, now: datetime) -> list[TestSlot]:
    now = utc(now)
    found = []
    for slot in authority.slots:
        model = slot.resources["model"]
        if (
            slot.terminal_fence
            and now >= min(slot.expires_at, slot.auth_expires_at, slot.idle_expires_at) + GRACE
            and all(
                slot.resources[kind].attempted == slot.resources[kind].settled
                for kind in SETTLED_KINDS
            )
            and (model.attempted > model.settled or _recovered(slot))
        ):
            found.append(slot)
    return found


def _settled(slot: TestSlot, now: datetime) -> dict[str, Any]:
    if _recovered(slot):
        return slot.model_dump(mode="json", by_alias=True)
    reference = "model-recovered-" + uuid4().hex
    document = slot.model_dump(mode="json", by_alias=True)
    document["resources"]["model"].update(
        settled=slot.resources["model"].attempted, settlementRef=reference
    )
    document.update(
        phase="cleanup_pending",
        settledThrough=slot.attempted_through,
        cleanupEvidence=CleanupEvidence(
            kind="speech_session_closed" if slot.live_ever_attempted else "work_disposed",
            sessionId=slot.session_id,
            mode=slot.mode,
            ownerId=slot.owner_id,
            ownerEpoch=slot.owner_epoch,
            attemptWatermark=slot.attempted_through,
            observedAt=now,
            disposalRef=RECOVERY_PREFIX + uuid4().hex,
            settlementRef=reference,
            closure=slot.closure_receipt if slot.live_ever_attempted else None,
        ).model_dump(mode="json", by_alias=True),
    )
    return document


async def release_stuck_model_slots(
    repository: TestAdmissionRepository, *, now: datetime, apply: bool
) -> list[str]:
    """Return the session ids that match; with `apply`, release them (idempotent)."""
    now = utc(now)
    for _attempt in range(4):
        current = await repository.read(deadline=asyncio.get_running_loop().time() + 10)
        found = candidates(current.authority, now)
        released = [slot.session_id for slot in found]
        if not apply or not found:
            return released
        chosen = set(released)
        document = current.authority.model_dump(mode="json", by_alias=True)
        document["utcHighWater"] = max(current.authority.utc_high_water, now).isoformat()
        document["slots"] = [
            _settled(slot, now)
            if slot.session_id in chosen
            else slot.model_dump(mode="json", by_alias=True)
            for slot in current.authority.slots
        ]
        try:
            # A slot is removable only once it carries settled cleanup evidence.
            settled: AuthoritySnapshot = await repository.replace(
                current,
                TestAuthority.model_validate_json(encoded(document)),
                deadline=asyncio.get_running_loop().time() + 10,
            )
            remaining = settled.authority.model_dump(mode="json", by_alias=True)
            remaining["slots"] = [
                entry for entry in remaining["slots"] if entry["sessionId"] not in chosen
            ]
            await repository.replace(
                settled,
                TestAuthority.model_validate_json(encoded(remaining)),
                deadline=asyncio.get_running_loop().time() + 10,
            )
        except AuthorityConflict:
            continue
        return released
    raise AuthorityConflict()


async def _main(apply: bool) -> int:
    from caller_runtime.adapters.azure_clients import AzureClients
    from caller_runtime.settings import Settings

    clients = AzureClients(Settings().live())
    try:
        repository = TestAdmissionRepository(container=await clients.container("call-sessions"))
        released = await release_stuck_model_slots(repository, now=datetime.now(UTC), apply=apply)
    finally:
        await clients.close()
    action = "released" if apply else "would release"
    print(f"{action} {len(released)} slot(s): {', '.join(released) or '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m caller_runtime.recovery")
    parser.add_argument(
        "--apply", action="store_true", help="release the listed slots (default: preview only)"
    )
    arguments = parser.parse_args(argv)
    return asyncio.run(_main(arguments.apply))


if __name__ == "__main__":
    sys.exit(main())
