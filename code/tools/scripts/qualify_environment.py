"""Fail-closed environment observation harness; offline CLI cannot grant live approval."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Never, Protocol
from uuid import uuid4

CHECKS = (
    "terraform_deployment",
    "image_deployment",
    "workload_identity",
    "private_network",
    "storage_ownership",
    "model_provider",
    "quota_budget",
    "privacy_approval",
    "monitoring_delivery",
)


@dataclass(frozen=True, slots=True)
class EnvironmentRequest:
    environment_ref: str
    terraform_revision: str
    runtime_image: str
    management_image: str
    plan_review_ref: str
    privacy_review_ref: str
    provider_review_ref: str
    quota_review_ref: str
    max_observation_age_seconds: int

    def __post_init__(self) -> None:
        for value in (
            self.environment_ref,
            self.plan_review_ref,
            self.privacy_review_ref,
            self.provider_review_ref,
            self.quota_review_ref,
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", value):
                raise ValueError("invalid_environment_reference")
        if not isinstance(self.terraform_revision, str) or not re.fullmatch(
            r"[a-f0-9]{40}", self.terraform_revision
        ):
            raise ValueError("invalid_environment_revision")
        if any(
            not isinstance(image, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image)
            for image in (self.runtime_image, self.management_image)
        ):
            raise ValueError("immutable_environment_images_required")
        if (
            type(self.max_observation_age_seconds) is not int
            or not 1 <= self.max_observation_age_seconds <= 3600
        ):
            raise ValueError("invalid_observation_age")

    @property
    def fingerprint(self) -> str:
        content = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: str
    outcome: Literal["passed", "failed", "unavailable"]
    reason: str

    def __post_init__(self) -> None:
        if (
            self.check not in CHECKS
            or self.outcome not in {"passed", "failed", "unavailable"}
            or self.reason
            not in {
                "verified",
                "denied",
                "probe_unavailable",
                "invalid_observation",
                "authorization_required",
                "authorization_unavailable",
                "not_observed",
                "deadline",
                "live_probes_unavailable",
                "invalid_manifest",
            }
        ):
            raise ValueError("invalid_qualification_result")


@dataclass(frozen=True, slots=True)
class QualificationReport:
    fingerprint: str | None
    checks: tuple[CheckResult, ...]

    def to_dict(self) -> dict[str, Any]:
        if len(self.checks) != len(CHECKS) or {check.check for check in self.checks} != set(CHECKS):
            raise ValueError("incomplete_qualification_report")
        outcomes = {check.outcome for check in self.checks}
        status = (
            "failed"
            if "failed" in outcomes
            else "observed"
            if outcomes == {"passed"}
            else "pending"
        )
        return {
            "schema_version": 1,
            "status": status,
            "live_qualification": "pending",
            "environment_fingerprint": self.fingerprint,
            "checks": {
                check.check: {"outcome": check.outcome, "reason": check.reason}
                for check in self.checks
            },
        }


@dataclass(frozen=True, slots=True)
class Authorization:
    fingerprint: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Observation:
    check: str
    fingerprint: str
    observed_at: datetime
    outcome: Literal["passed", "failed", "unavailable"]


class AuthorizationPort(Protocol):
    async def authorize(
        self, request: EnvironmentRequest, *, deadline: float
    ) -> Authorization | None:
        """Authenticate approved read-only scope and exact reviewed environment binding."""
        ...


class ProbePort(Protocol):
    async def observe(
        self, check: str, request: EnvironmentRequest, *, deadline: float
    ) -> Observation:
        """Observe the approved fixed check without provisioning or exposing content/credentials."""
        ...


def pending(request: EnvironmentRequest | None = None, *, reason: str) -> QualificationReport:
    return QualificationReport(
        request.fingerprint if request is not None else None,
        tuple(CheckResult(check, "unavailable", reason) for check in CHECKS),
    )


def utc(value: datetime) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def bound(grant: Authorization | None, request: EnvironmentRequest, instant: datetime) -> bool:
    return (
        isinstance(grant, Authorization)
        and grant.fingerprint == request.fingerprint
        and utc(grant.expires_at)
        and utc(instant)
        and grant.expires_at > instant
    )


def observed(
    value: Observation, check: str, request: EnvironmentRequest, instant: datetime
) -> CheckResult:
    if (
        not isinstance(value, Observation)
        or value.check != check
        or value.fingerprint != request.fingerprint
        or not utc(value.observed_at)
        or not utc(instant)
        or not 0
        <= (instant - value.observed_at).total_seconds()
        <= request.max_observation_age_seconds
        or value.outcome not in {"passed", "failed", "unavailable"}
    ):
        return CheckResult(check, "unavailable", "invalid_observation")
    reason = (
        "verified"
        if value.outcome == "passed"
        else "denied"
        if value.outcome == "failed"
        else "probe_unavailable"
    )
    return CheckResult(check, value.outcome, reason)


def emit(report: QualificationReport) -> QualificationReport:
    status = report.to_dict()["status"]
    logger = logging.getLogger("caller-operations.events")
    logger.setLevel(logging.INFO)
    failed = status == "failed"
    logger.log(
        logging.ERROR if failed else logging.INFO,
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "severity": "ERROR" if failed else "INFO",
                "service": "operations-tool",
                "environment": "local",
                "event": "environment_observation",
                "outcome": status,
                "correlation_id": uuid4().hex,
            }
        ),
    )
    return report


async def qualify(
    request: EnvironmentRequest,
    *,
    authority: AuthorizationPort | None = None,
    probe: ProbePort | None = None,
    budget_seconds: float = 10,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> QualificationReport:
    """Observe one complete fixed profile over trusted ports; never mint live release approval."""
    if not isinstance(request, EnvironmentRequest):
        raise ValueError("invalid_environment_request")
    if (
        type(budget_seconds) not in (int, float)
        or not math.isfinite(budget_seconds)
        or not 0 < budget_seconds <= 10
    ):
        raise ValueError("invalid_qualification_budget")
    if not utc(now()):
        raise ValueError("invalid_qualification_clock")
    if authority is None:
        return emit(pending(request, reason="authorization_required"))
    results = {check: CheckResult(check, "unavailable", "not_observed") for check in CHECKS}
    deadline = asyncio.get_running_loop().time() + budget_seconds
    try:
        async with asyncio.timeout_at(deadline):
            grant = await authority.authorize(request, deadline=deadline)
            if not bound(grant, request, now()):
                return emit(pending(request, reason="authorization_required"))
            if probe is None:
                return emit(pending(request, reason="live_probes_unavailable"))
            for check in CHECKS:
                if not bound(grant, request, now()):
                    results[check] = CheckResult(check, "unavailable", "authorization_required")
                    break
                try:
                    value = await probe.observe(check, request, deadline=deadline)
                    if not bound(grant, request, now()):
                        results[check] = CheckResult(check, "unavailable", "authorization_required")
                        break
                    results[check] = observed(value, check, request, now())
                except TimeoutError:
                    raise
                except PermissionError:
                    results[check] = CheckResult(check, "failed", "denied")
                except Exception:
                    results[check] = CheckResult(check, "unavailable", "probe_unavailable")
    except TimeoutError:
        results = {
            check: CheckResult(check, "unavailable", "deadline")
            if result.reason == "not_observed"
            else result
            for check, result in results.items()
        }
    except Exception:
        return emit(pending(request, reason="authorization_unavailable"))
    return emit(QualificationReport(request.fingerprint, tuple(results.values())))


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_environment_field")
        value[key] = item
    return value


def parse_request(content: bytes) -> EnvironmentRequest:
    if not isinstance(content, bytes) or not 0 < len(content) <= 16384:
        raise ValueError("invalid_environment_manifest")
    try:
        value = json.loads(content, object_pairs_hook=unique_object)
        if not isinstance(value, dict) or set(value) != {
            field.name for field in fields(EnvironmentRequest)
        }:
            raise ValueError("invalid_environment_manifest")
        return EnvironmentRequest(**value)
    except (TypeError, ValueError):
        raise ValueError("invalid_environment_manifest") from None


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("invalid_qualification_arguments")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = SafeParser(
        description="Inspect nonsecret metadata offline; live qualification stays pending."
    )
    parser.add_argument("--check-manifest", type=Path)
    try:
        options = parser.parse_args(arguments)
        request = None
        if options.check_manifest is not None:
            with options.check_manifest.open("rb") as source:
                request = parse_request(source.read(16385))
        report = pending(request, reason="live_probes_unavailable")
    except Exception:
        report = pending(reason="invalid_manifest")
    value = report.to_dict() | {"mode": "offline_manifest_only"}
    print(json.dumps(value))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
