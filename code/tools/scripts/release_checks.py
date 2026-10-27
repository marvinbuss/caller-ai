"""Protected release orchestration over separately qualified trusted ports; CLI is blocked."""

import argparse
import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Never, Protocol


@dataclass(frozen=True, slots=True)
class ReleaseRequest:
    operation_id: str
    commit: str
    runtime_digest: str
    management_digest: str
    environment_ref: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{40}", self.commit):
            raise ValueError("immutable_release_commit_required")
        if any(
            not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
            for digest in (self.runtime_digest, self.management_digest)
        ):
            raise ValueError("immutable_release_digests_required")
        if any(
            not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", value)
            for value in (self.operation_id, self.environment_ref)
        ):
            raise ValueError("invalid_release_reference")


class ReleaseAuthority(Protocol):
    async def authorize(self, request: ReleaseRequest, *, deadline: float) -> bool:
        """Verify protected scope, fresh bound qualification and required passing gates."""
        ...


class ReleasePort(Protocol):
    async def reviewed_plan(self, request: ReleaseRequest, *, deadline: float) -> bool:
        """Generate and review a protected remote-state plan; never apply or expose raw secrets."""
        ...

    async def publish(
        self, request: ReleaseRequest, *, deadline: float
    ) -> Literal["published", "already_published"]:
        """Idempotently publish exact immutable digests under current protected authority."""
        ...


async def release(
    request: ReleaseRequest, *, authority: ReleaseAuthority, port: ReleasePort
) -> str:
    attempted = False
    deadline = asyncio.get_running_loop().time() + 10
    try:
        async with asyncio.timeout_at(deadline):
            if await authority.authorize(request, deadline=deadline) is not True:
                return "blocked"
            if await port.reviewed_plan(request, deadline=deadline) is not True:
                return "blocked"
            if await authority.authorize(request, deadline=deadline) is not True:
                return "blocked"
            attempted = True
            result = await port.publish(request, deadline=deadline)
            return result if result in {"published", "already_published"} else "uncertain"
    except Exception:
        return "uncertain" if attempted else "blocked"


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("invalid_release_arguments")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = SafeParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", required=True)
    try:
        parser.parse_args(arguments)
    except Exception:
        print(json.dumps({"status": "blocked", "reason": "invalid_release_arguments"}))
        return 2
    print(
        json.dumps(
            {
                "status": "blocked",
                "reason": "qualified_release_adapters_and_external_evidence_required",
                "plan_applied": False,
                "images_published": False,
            }
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
