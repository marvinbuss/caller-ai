"""Local application-controlled retirement over trusted ports; CLI validates plans only."""

import argparse
import asyncio
import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Never, Protocol
from uuid import uuid4


def reference(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("invalid_rollout_reference")


@dataclass(frozen=True, slots=True)
class Target:
    cluster_ref: str
    namespace: str
    workload: str
    owner_id: str
    pod_name: str
    pod_uid: str

    def __post_init__(self) -> None:
        for value in (self.cluster_ref, self.owner_id, self.pod_uid):
            reference(value)
        for value in (self.namespace, self.workload, self.pod_name):
            if not isinstance(value, str) or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value
            ):
                raise ValueError("invalid_rollout_target")


@dataclass(frozen=True, slots=True)
class RemovalRequest:
    operation_id: str
    intent: Literal["rollout", "scale_in"]
    target: Target

    def __post_init__(self) -> None:
        reference(self.operation_id)
        if self.intent not in {"rollout", "scale_in"} or not isinstance(self.target, Target):
            raise ValueError("invalid_rollout_request")


@dataclass(frozen=True, slots=True)
class RemovalFence:
    request: RemovalRequest
    reference: str
    independent_deletions_blocked: bool


@dataclass(frozen=True, slots=True)
class RolloutResult:
    outcome: Literal["retired", "already_retired", "postponed", "blocked", "uncertain"]
    reason: str


class OperationsPort(Protocol):
    async def status(self, owner_id: str, *, deadline: float) -> Mapping[str, Any]: ...
    async def begin(self, owner_id: str, *, deadline: float) -> Mapping[str, Any]: ...
    async def complete(self, owner_id: str, *, deadline: float) -> Mapping[str, Any]: ...


class ClusterPort(Protocol):
    async def fence(self, request: RemovalRequest, *, deadline: float) -> RemovalFence:
        """Authenticate and persistently fence independent planned removal."""
        ...

    async def retire(
        self,
        request: RemovalRequest,
        fence: RemovalFence,
        drain: Mapping[str, Any],
        *,
        deadline: float,
    ) -> str:
        """Atomically enforce fence/UID/drain and deduplicate the bound operation ID."""
        ...


def snapshot(value: Mapping[str, Any], owner_id: str, phase: str) -> Mapping[str, Any]:
    fields = {"ownerId", "state", "canTerminate"} | ({"outcome"} if phase != "status" else set())
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or value["ownerId"] != owner_id
        or value["state"] not in {"serving", "draining", "drained"}
        or type(value["canTerminate"]) is not bool
        or (value["canTerminate"] and value["state"] != "drained")
    ):
        raise ValueError("invalid_drain_snapshot")
    if phase == "begin" and (
        value["state"] not in {"draining", "drained"} or value["outcome"] != value["state"]
    ):
        raise ValueError("invalid_drain_start")
    if phase == "complete" and value["outcome"] not in {"drained", "postponed"}:
        raise ValueError("invalid_drain_completion")
    return MappingProxyType(dict(value))


def result(
    outcome: Literal["retired", "already_retired", "postponed", "blocked", "uncertain"], reason: str
) -> RolloutResult:
    logger = logging.getLogger("caller-operations.events")
    logger.setLevel(logging.INFO)
    failed = outcome in {"blocked", "uncertain"}
    logger.log(
        logging.ERROR if failed else logging.INFO,
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "severity": "ERROR" if failed else "INFO",
                "service": "operations-tool",
                "environment": "local",
                "event": "drain_rollout",
                "outcome": outcome,
                "reason_code": reason,
                "correlation_id": uuid4().hex,
            }
        ),
    )
    return RolloutResult(outcome, reason)


async def orchestrate(
    request: RemovalRequest,
    *,
    operations: OperationsPort,
    cluster: ClusterPort,
    budget_seconds: float = 10,
) -> RolloutResult:
    """One bounded attempt, no polling/force: postponed and ambiguous work retains its fence."""
    if (
        type(budget_seconds) not in (int, float)
        or not math.isfinite(budget_seconds)
        or not 0 < budget_seconds <= 10
    ):
        raise ValueError("invalid_rollout_budget")
    if not isinstance(request, RemovalRequest) or operations is None or cluster is None:
        raise ValueError("explicit_rollout_ports_required")
    deadline = asyncio.get_running_loop().time() + budget_seconds
    attempted = False
    try:
        async with asyncio.timeout_at(deadline):
            owner = request.target.owner_id
            snapshot(await operations.status(owner, deadline=deadline), owner, "status")
            fence = await cluster.fence(request, deadline=deadline)
            if (
                not isinstance(fence, RemovalFence)
                or fence.request != request
                or fence.independent_deletions_blocked is not True
            ):
                return result("blocked", "removal_fence_required")
            reference(fence.reference)
            snapshot(await operations.begin(owner, deadline=deadline), owner, "begin")
            complete = snapshot(
                await operations.complete(owner, deadline=deadline), owner, "complete"
            )
            if complete["outcome"] == "postponed":
                return result("postponed", "drain_incomplete")
            if complete["state"] != "drained" or complete["canTerminate"] is not True:
                return result("blocked", "drain_not_confirmed")
            current = snapshot(await operations.status(owner, deadline=deadline), owner, "status")
            if current["state"] != "drained" or current["canTerminate"] is not True:
                return result("blocked", "drain_state_changed")
            attempted = True
            outcome = await cluster.retire(request, fence, current, deadline=deadline)
            if outcome in {"retired", "already_retired"}:
                return result(
                    "retired" if outcome == "retired" else "already_retired", "removal_confirmed"
                )
            if outcome == "postponed":
                return result("postponed", "cluster_condition_changed")
    except asyncio.CancelledError:
        result("uncertain" if attempted else "blocked", "operation_canceled")
        raise
    except Exception:
        return result(
            "uncertain" if attempted else "blocked",
            "removal_not_confirmed" if attempted else "control_unavailable",
        )
    return result("uncertain", "removal_not_confirmed")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_rollout_field")
        value[key] = item
    return value


def parse_request(content: bytes) -> RemovalRequest:
    if not 0 < len(content) <= 4096:
        raise ValueError("invalid_rollout_plan")
    value = json.loads(content, object_pairs_hook=unique_object)
    if (
        not isinstance(value, dict)
        or set(value) != {"operation_id", "intent", "target"}
        or not isinstance(value["target"], dict)
        or set(value["target"])
        != {"cluster_ref", "namespace", "workload", "owner_id", "pod_name", "pod_uid"}
    ):
        raise ValueError("invalid_rollout_plan")
    return RemovalRequest(value["operation_id"], value["intent"], Target(**value["target"]))


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("invalid_rollout_arguments")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = SafeParser(
        description="Validate a nonsecret local removal plan; no execution adapter is enabled."
    )
    parser.add_argument("--check-plan", type=Path, required=True)
    try:
        options = parser.parse_args(arguments)
        with options.check_plan.open("rb") as source:
            parse_request(source.read(4097))
    except Exception:
        print(json.dumps({"result": "rollout_plan_rejected", "mode": "validation_only"}))
        return 2
    print(json.dumps({"result": "rollout_plan_validated", "mode": "validation_only"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
