"""One-off release of test slots that cleanup could not prove free.

Two failures left a slot held for good, so the same manager got `manager_busy`:

- a model request that outlived its work budget was never settled (before the background drain);
- a voice session that failed during an agent change left a Live attempt whose closure the
  provider never confirmed to this runtime (before the fixes for speech agent changes).

This releases only slots that are fenced, expired at least five minutes ago, whose capability
attempts are settled and that hold an unsettled model or Live attempt. A Live socket cannot
outlive its session: the runtime closes it when the session ends and the provider closes it when
the runtime is gone. A provider-bound attempt is settled with a closure receipt marked by a
`live-recovered-` settlement reference, so the release keeps the store's evidence rules. An
unbound Live attempt never got a provider session (its start failed and closed the socket), so
no receipt can exist: such a slot is removed without one, which the store allows only here. It
never creates or deletes the authority item and never touches a slot that is still live.
Preview mode, the default, lists the candidates and writes nothing. The runtime applies the same
rule on its own, once a minute, to slots held only by a Live attempt: a voice start that failed
or a close GPT-Live never confirmed.

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
    ClosureReceipt,
    ResourceKind,
    TestAuthority,
    TestSlot,
    utc,
)

GRACE: Final = timedelta(minutes=5)
RECOVERY_PREFIX: Final = "recovery-"
SETTLED_KINDS: Final[tuple[ResourceKind, ...]] = ("capability",)


def _recovered(slot: TestSlot) -> bool:
    evidence = slot.cleanup_evidence
    return evidence is not None and evidence.disposal_ref.startswith(RECOVERY_PREFIX)


def _unsettled(slot: TestSlot, kind: ResourceKind) -> bool:
    return slot.resources[kind].attempted > slot.resources[kind].settled


def _live_releasable(slot: TestSlot) -> bool:
    # Only a provider-bound attempt can carry a closure receipt; an unbound one never started.
    attempt = slot.resources["live"].attempt
    return attempt is not None and attempt.provider_session_id is not None


def held_by_live(slot: TestSlot) -> bool:
    """A slot held only by a Live attempt, or whose release a recovery already began."""
    return not _unsettled(slot, "model") and (_unsettled(slot, "live") or _recovered(slot))


def candidates(
    authority: TestAuthority, now: datetime, *, live_only: bool = False
) -> list[TestSlot]:
    now = utc(now)
    found = []
    for slot in authority.slots:
        if (
            slot.terminal_fence
            and now >= min(slot.expires_at, slot.auth_expires_at, slot.idle_expires_at) + GRACE
            and not any(_unsettled(slot, kind) for kind in SETTLED_KINDS)
            and (_unsettled(slot, "model") or _unsettled(slot, "live") or _recovered(slot))
            and (not live_only or held_by_live(slot))
        ):
            found.append(slot)
    return found


def _settled(slot: TestSlot, now: datetime) -> dict[str, Any]:
    if _recovered(slot):
        return slot.model_dump(mode="json", by_alias=True)
    document = slot.model_dump(mode="json", by_alias=True)
    reference = ""
    closure = slot.closure_receipt
    if _unsettled(slot, "model"):
        reference = "model-recovered-" + uuid4().hex
        document["resources"]["model"].update(
            settled=slot.resources["model"].attempted, settlementRef=reference
        )
    attempt = slot.resources["live"].attempt
    if _unsettled(slot, "live") and attempt is not None and attempt.provider_session_id is not None:
        reference = "live-recovered-" + uuid4().hex
        # Recorded by recovery, not the provider: the `live-recovered-` reference says so.
        closure = ClosureReceipt(
            sessionId=slot.session_id,
            providerSessionId=attempt.provider_session_id,
            ownerId=slot.owner_id,
            ownerEpoch=slot.owner_epoch,
            generation=attempt.generation,
            observedAt=now,
            provenanceRef=attempt.provenance_ref,
            reason="expired",
            voiceSeconds=0,
            authenticated=True,
        )
        document["resources"]["live"].update(
            settled=slot.resources["live"].attempted, settlementRef=reference
        )
        document["closureReceipt"] = closure.model_dump(mode="json", by_alias=True)
        document["closedThroughGeneration"] = attempt.generation
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
            closure=closure if slot.live_ever_attempted else None,
        ).model_dump(mode="json", by_alias=True),
    )
    return document


async def release_stuck_slots(
    repository: TestAdmissionRepository,
    *,
    now: datetime,
    apply: bool,
    live_only: bool = False,
) -> list[str]:
    """Return the session ids that match; with `apply`, release them (idempotent).

    `live_only` limits it to slots held only by a Live attempt, which the runtime releases on
    its own.
    """
    now = utc(now)
    for _attempt in range(4):
        current = await repository.read(deadline=asyncio.get_running_loop().time() + 10)
        found = candidates(current.authority, now, live_only=live_only)
        released = [slot.session_id for slot in found]
        if not apply or not found:
            return released
        chosen = set(released)
        unbound = frozenset(
            slot.session_id
            for slot in found
            if _unsettled(slot, "live") and not _live_releasable(slot)
        )
        document = current.authority.model_dump(mode="json", by_alias=True)
        document["utcHighWater"] = max(current.authority.utc_high_water, now).isoformat()
        document["slots"] = [
            _settled(slot, now)
            if slot.session_id in chosen
            else slot.model_dump(mode="json", by_alias=True)
            for slot in current.authority.slots
            if slot.session_id not in unbound
        ]
        try:
            # A slot is removable only once it carries settled cleanup evidence; an unbound Live
            # attempt can never carry a receipt, so its slot goes in this first write.
            settled: AuthoritySnapshot = await repository.replace(
                current,
                TestAuthority.model_validate_json(encoded(document)),
                deadline=asyncio.get_running_loop().time() + 10,
                released=unbound,
            )
            if chosen - unbound:
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
        released = await release_stuck_slots(repository, now=datetime.now(UTC), apply=apply)
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
