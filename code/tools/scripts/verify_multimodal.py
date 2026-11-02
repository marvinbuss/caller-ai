"""Deny-by-default GPT-Live qualification; metadata never grants provider access."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from io import TextIOBase
from pathlib import Path
from typing import Any, Literal, Never, Protocol, TypeGuard
from urllib.parse import urlsplit

CHECKS = (
    "existing_bindings",
    "workload_identity",
    "available_quota",
    "region_lifecycle",
    "privacy_retention",
    "reasoning_features",
    "text_without_live",
    "client_delegation",
    "confirmed_closure",
)
FEATURES = frozenset({"streaming", "function_tools", "storage_disabled", "structured_output"})


@dataclass(frozen=True, slots=True)
class MultimodalRequest:
    environment_ref: str
    identity_review_ref: str
    provider_review_ref: str
    quota_review_ref: str
    residency_review_ref: str
    lifecycle_review_ref: str
    retention_review_ref: str
    cost_review_ref: str
    live_endpoint: str
    text_endpoint: str
    text_deployment: str
    live_model: str
    live_model_version: str
    live_region: str
    available_live_sessions: int
    text_features: tuple[str, ...]
    max_seconds: int
    max_cost_usd: float
    max_observation_age_seconds: int

    def __post_init__(self) -> None:
        for descriptor in fields(self):
            if descriptor.name.endswith("_ref"):
                value = getattr(self, descriptor.name)
                if (
                    not isinstance(value, str)
                    or re.fullmatch(r"[a-z][a-z0-9-]{2,63}", value) is None
                ):
                    raise ValueError("invalid_multimodal_review")
        for endpoint in (self.live_endpoint, self.text_endpoint):
            if not isinstance(endpoint, str):
                raise ValueError("invalid_multimodal_provider")
            parts = urlsplit(endpoint)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or not parts.hostname.endswith((".openai.azure.com", ".services.ai.azure.com"))
                or parts.username is not None
                or parts.password is not None
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or parts.port not in {None, 443}
                or "\\" in endpoint
                or any(ord(character) < 33 for character in endpoint)
            ):
                raise ValueError("invalid_multimodal_provider")
        if (
            not isinstance(self.text_deployment, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.text_deployment) is None
            or self.text_deployment == "gpt-live-1"
            or self.live_model != "gpt-live-1"
            or self.live_model_version != "2026-09-10"
            or self.live_region not in {"eastus2", "centralus", "canadacentral", "swedencentral"}
            or not isinstance(self.text_features, tuple)
            or len(self.text_features) != len(FEATURES)
            or frozenset(self.text_features) != FEATURES
        ):
            raise ValueError("invalid_multimodal_binding")
        if (
            type(self.available_live_sessions) is not int
            or not 5 <= self.available_live_sessions <= 10000
            or type(self.max_seconds) is not int
            or not 1 <= self.max_seconds <= 600
            or type(self.max_observation_age_seconds) is not int
            or not 1 <= self.max_observation_age_seconds <= 600
            or type(self.max_cost_usd) not in (int, float)
            or not math.isfinite(self.max_cost_usd)
            or not 0 < self.max_cost_usd <= 100
        ):
            raise ValueError("invalid_multimodal_scope")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


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
                "authorization_required",
                "authorization_unavailable",
                "probe_unavailable",
                "invalid_observation",
                "deadline",
                "unknown_closure",
                "invalid_manifest",
                "not_observed",
                "cost_scope",
            }
        ):
            raise ValueError("invalid_multimodal_result")


@dataclass(frozen=True, slots=True)
class QualificationReport:
    fingerprint: str | None
    checks: tuple[CheckResult, ...]

    def to_dict(self) -> dict[str, Any]:
        if len(self.checks) != len(CHECKS) or {item.check for item in self.checks} != set(CHECKS):
            raise ValueError("incomplete_multimodal_report")
        outcomes = {item.outcome for item in self.checks}
        return {
            "schema_version": 1,
            "status": "failed"
            if "failed" in outcomes
            else "observed"
            if outcomes == {"passed"}
            else "pending",
            "live_qualification": "pending",
            "environment_fingerprint": self.fingerprint,
            "checks": {
                item.check: {"outcome": item.outcome, "reason": item.reason} for item in self.checks
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
    confirmed: bool = True
    cost_usd: float | None = None
    evidence_digest: str | None = None


class AuthorizationPort(Protocol):
    async def authorize(
        self, request: MultimodalRequest, *, deadline: float
    ) -> Authorization | None: ...


class ProbePort(Protocol):
    async def observe(
        self, check: str, request: MultimodalRequest, *, deadline: float, max_cost_usd: float
    ) -> Observation:
        """Honor exact reviewed bindings and budgets; never discover/provision resources."""
        ...


def pending(
    request: MultimodalRequest | None = None, *, reason: str = "authorization_required"
) -> QualificationReport:
    return QualificationReport(
        request.fingerprint if request is not None else None,
        tuple(CheckResult(check, "unavailable", reason) for check in CHECKS),
    )


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("invalid_multimodal_manifest")
        value[key] = item
    return value


def parse_request(content: bytes) -> MultimodalRequest:
    try:
        if not isinstance(content, bytes) or len(content) > 16384:
            raise ValueError("invalid_manifest")
        document = json.loads(content, object_pairs_hook=unique_object)
        if not isinstance(document, dict) or set(document) != {
            item.name for item in fields(MultimodalRequest)
        }:
            raise ValueError("invalid_manifest")
        if not isinstance(document["text_features"], list):
            raise ValueError("invalid_manifest")
        document["text_features"] = tuple(document["text_features"])
        return MultimodalRequest(**document)
    except Exception:
        raise ValueError("invalid_multimodal_manifest") from None


async def qualify(
    request: MultimodalRequest,
    *,
    authorization: AuthorizationPort | None = None,
    probe: ProbePort | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> QualificationReport:
    if not isinstance(request, MultimodalRequest):
        return pending(reason="invalid_manifest")
    if authorization is None or probe is None:
        return pending(request)
    deadline = asyncio.get_running_loop().time() + request.max_seconds
    checks: list[CheckResult] = []
    spent = 0.0
    previous: datetime | None = None

    def instant() -> datetime:
        nonlocal previous
        value = now()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise ValueError("untrusted_clock")
        if previous is not None and value < previous:
            raise ValueError("untrusted_clock")
        previous = value
        return value

    def allowed(grant: Authorization | None, at: datetime) -> bool:
        return (
            isinstance(grant, Authorization)
            and grant.fingerprint == request.fingerprint
            and isinstance(grant.expires_at, datetime)
            and grant.expires_at.tzinfo is not None
            and grant.expires_at.utcoffset() == UTC.utcoffset(grant.expires_at)
            and grant.expires_at > at
        )

    def finish(reason: str) -> QualificationReport:
        done = {item.check for item in checks}
        return QualificationReport(
            request.fingerprint,
            tuple(checks)
            + tuple(
                CheckResult(check, "unavailable", reason) for check in CHECKS if check not in done
            ),
        )

    class Discard(TextIOBase):
        def write(self, text: str) -> int:
            return len(text)

    @contextmanager
    def private_probe() -> Iterator[None]:
        disabled = logging.root.manager.disable
        try:
            logging.disable(logging.CRITICAL)
            with Discard() as sink, redirect_stdout(sink), redirect_stderr(sink):
                yield
        finally:
            logging.disable(disabled)

    try:
        async with asyncio.timeout_at(deadline):
            with private_probe():
                initial = await authorization.authorize(request, deadline=deadline)
            if not allowed(initial, instant()):
                return finish("authorization_required")
            for check in CHECKS:
                with private_probe():
                    grant = await authorization.authorize(request, deadline=deadline)
                current = instant()
                if grant is None or not allowed(grant, current):
                    return finish("authorization_required")
                if spent >= request.max_cost_usd:
                    return finish("cost_scope")
                boundary = min(
                    deadline,
                    asyncio.get_running_loop().time()
                    + (grant.expires_at - current).total_seconds(),
                )
                try:
                    async with asyncio.timeout_at(boundary):
                        with private_probe():
                            observation = await probe.observe(
                                check,
                                request,
                                deadline=deadline,
                                max_cost_usd=request.max_cost_usd - spent,
                            )
                except TimeoutError:
                    checks.append(CheckResult(check, "unavailable", "deadline"))
                    return finish("deadline")
                except Exception:
                    checks.append(CheckResult(check, "unavailable", "probe_unavailable"))
                    return finish("probe_unavailable")
                current = instant()
                if not allowed(grant, current) or not isinstance(observation, Observation):
                    checks.append(CheckResult(check, "unavailable", "invalid_observation"))
                    return finish("invalid_observation")
                if (
                    observation.check != check
                    or observation.fingerprint != request.fingerprint
                    or not isinstance(observation.observed_at, datetime)
                    or observation.observed_at.tzinfo is None
                    or observation.observed_at.utcoffset() != UTC.utcoffset(observation.observed_at)
                    or not 0
                    <= (current - observation.observed_at).total_seconds()
                    <= request.max_observation_age_seconds
                    or observation.outcome not in {"passed", "failed", "unavailable"}
                    or type(observation.confirmed) is not bool
                ):
                    checks.append(CheckResult(check, "unavailable", "invalid_observation"))
                    return finish("invalid_observation")
                if (
                    type(observation.cost_usd) not in (int, float)
                    or observation.cost_usd is None
                    or not math.isfinite(observation.cost_usd)
                    or observation.cost_usd < 0
                ):
                    checks.append(CheckResult(check, "unavailable", "cost_scope"))
                    return finish("cost_scope")
                spent += observation.cost_usd
                if spent > request.max_cost_usd:
                    checks.append(CheckResult(check, "failed", "cost_scope"))
                    return finish("cost_scope")
                if check == "confirmed_closure" and (
                    not observation.confirmed or observation.outcome == "unavailable"
                ):
                    checks.append(CheckResult(check, "unavailable", "unknown_closure"))
                    return finish("unknown_closure")
                if (
                    not observation.confirmed
                    or not isinstance(observation.evidence_digest, str)
                    or re.fullmatch(r"[a-f0-9]{64}", observation.evidence_digest) is None
                ):
                    checks.append(CheckResult(check, "unavailable", "invalid_observation"))
                    return finish("invalid_observation")
                checks.append(
                    CheckResult(
                        check,
                        observation.outcome,
                        "verified"
                        if observation.outcome == "passed"
                        else "denied"
                        if observation.outcome == "failed"
                        else "not_observed",
                    )
                )
                if observation.outcome != "passed":
                    return finish("not_observed")
        return QualificationReport(request.fingerprint, tuple(checks))
    except TimeoutError:
        return finish("deadline")
    except Exception:
        return finish("authorization_unavailable")


BROWSER_CHECKS = (
    "explicit_start",
    "denied_permission",
    "unanswered_permission",
    "mute",
    "autoplay",
    "unplug",
    "interrupt",
    "stop",
    "navigation",
    "logout",
    "connection_loss",
    "loopback_latency",
)
BROWSER_PLATFORMS = frozenset({"edge_windows", "chrome_windows", "chrome_linux"})


@dataclass(frozen=True, slots=True)
class BrowserScope:
    provider: MultimodalRequest
    mode: Literal["device", "live_endurance"]
    foundry_evidence_digest: str
    device_review_ref: str
    endurance_review_ref: str | None
    browsers: tuple[tuple[str, str], ...]
    max_seconds: int
    device_evidence_digest: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider, MultimodalRequest)
            or self.mode not in {"device", "live_endurance"}
            or re.fullmatch(r"[a-f0-9]{64}", self.foundry_evidence_digest) is None
            or re.fullmatch(r"[a-z][a-z0-9-]{2,63}", self.device_review_ref) is None
            or not isinstance(self.browsers, tuple)
            or len(self.browsers) != 3
            or any(not isinstance(item, tuple) or len(item) != 2 for item in self.browsers)
            or {item[0] for item in self.browsers} != BROWSER_PLATFORMS
            or any(
                re.fullmatch(r"\d{1,4}(?:\.\d{1,6}){3}", item[1]) is None for item in self.browsers
            )
            or type(self.max_seconds) is not int
            or not 1 <= self.max_seconds <= (600 if self.mode == "device" else 1800)
        ):
            raise ValueError("invalid_browser_scope")
        if self.mode == "live_endurance" and (
            not isinstance(self.endurance_review_ref, str)
            or re.fullmatch(r"[a-z][a-z0-9-]{2,63}", self.endurance_review_ref) is None
            or not isinstance(self.device_evidence_digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", self.device_evidence_digest) is None
            or self.max_seconds <= 600
        ):
            raise ValueError("explicit_endurance_scope_required")
        if self.mode == "device" and (
            self.endurance_review_ref is not None or self.device_evidence_digest is not None
        ):
            raise ValueError("invalid_browser_scope")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class BrowserObservation:
    check: str
    platform: str
    fingerprint: str
    observed_at: datetime
    evidence_digest: str
    confirmed_closure: bool
    cost_usd: float | None
    cutoff_seconds: float
    failure_seconds: float
    latency_seconds: tuple[float, ...]
    participant_seconds: tuple[float, ...]
    text_journeys: int
    speech_journeys: int
    publications: int
    publication_seconds: float
    next_action_denied: bool
    browser_version: str = ""
    stable_verified: bool = False


class BrowserAuthorizationPort(Protocol):
    async def authorize(self, scope: BrowserScope, *, deadline: float) -> Authorization | None: ...


class BrowserProbePort(Protocol):
    async def observe(
        self,
        check: str,
        scope: BrowserScope,
        *,
        platform: str,
        deadline: float,
        max_cost_usd: float,
    ) -> BrowserObservation: ...


async def qualify_browser(
    scope: BrowserScope,
    *,
    authorization: BrowserAuthorizationPort | None = None,
    probe: BrowserProbePort | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": scope.mode,
        "status": "pending",
        "live_qualification": "pending",
        "reason": "authorization_required",
        "environment_fingerprint": scope.fingerprint,
        "samples": [],
    }
    if authorization is None or probe is None:
        return report
    started, tick = now(), monotonic()
    deadline = asyncio.get_running_loop().time() + scope.max_seconds
    spent = 0.0

    def finite(value: object, maximum: float) -> TypeGuard[int | float]:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= maximum
        )

    class Discard(TextIOBase):
        def write(self, text: str) -> int:
            return len(text)

    @contextmanager
    def private_probe() -> Iterator[None]:
        previous = logging.root.manager.disable
        try:
            logging.disable(logging.CRITICAL)
            with Discard() as sink, redirect_stdout(sink), redirect_stderr(sink):
                yield
        finally:
            logging.disable(previous)

    checks = [
        (check, platform) for platform, _version in scope.browsers for check in BROWSER_CHECKS
    ]
    if scope.mode == "live_endurance":
        checks = [("endurance", "matrix")]
    try:
        async with asyncio.timeout_at(deadline):
            for check, platform in checks:
                current = now()
                elapsed = monotonic() - tick
                if (
                    current.utcoffset() != timedelta(0)
                    or started.utcoffset() != timedelta(0)
                    or not finite(elapsed, scope.max_seconds)
                    or abs((current - started).total_seconds() - elapsed) > 1
                ):
                    report["reason"] = "invalid_clock"
                    return report
                with private_probe():
                    grant = await authorization.authorize(scope, deadline=deadline)
                if (
                    not isinstance(grant, Authorization)
                    or grant.fingerprint != scope.fingerprint
                    or grant.expires_at.tzinfo is None
                    or grant.expires_at <= current
                ):
                    return report
                if spent >= scope.provider.max_cost_usd:
                    report["reason"] = "cost_scope"
                    return report
                boundary = min(
                    deadline,
                    asyncio.get_running_loop().time()
                    + (grant.expires_at - current).total_seconds(),
                )
                before = monotonic()
                async with asyncio.timeout_at(boundary):
                    with private_probe():
                        observation = await probe.observe(
                            check,
                            scope,
                            platform=platform,
                            deadline=boundary,
                            max_cost_usd=scope.provider.max_cost_usd - spent,
                        )
                current = now()
                elapsed = monotonic() - tick
                if (
                    not isinstance(observation, BrowserObservation)
                    or grant.expires_at <= current
                    or observation.check != check
                    or observation.platform != platform
                    or observation.fingerprint != scope.fingerprint
                    or observation.observed_at.utcoffset() != timedelta(0)
                    or not finite(elapsed, scope.max_seconds)
                    or abs((current - started).total_seconds() - elapsed) > 1
                    or not 0
                    <= (current - observation.observed_at).total_seconds()
                    <= scope.provider.max_observation_age_seconds
                    or re.fullmatch(r"[a-f0-9]{64}", observation.evidence_digest) is None
                    or observation.stable_verified is not True
                    or (
                        platform != "matrix"
                        and observation.browser_version != dict(scope.browsers)[platform]
                    )
                ):
                    report["reason"] = "invalid_observation"
                    return report
                if observation.confirmed_closure is not True:
                    report["reason"] = "unknown_closure"
                    return report
                if not finite(observation.cost_usd, scope.provider.max_cost_usd - spent):
                    report["reason"] = "cost_scope"
                    return report
                spent += float(observation.cost_usd)
                samples = observation.latency_seconds
                minimum_samples = 20 if check in {"loopback_latency", "endurance"} else 0
                if (
                    not isinstance(samples, tuple)
                    or not minimum_samples <= len(samples) <= 2048
                    or any(not finite(value, 600) for value in samples)
                    or not finite(observation.cutoff_seconds, 1)
                    or not finite(observation.failure_seconds, 10)
                ):
                    report.update(status="failed", reason="timing_limits")
                    return report
                p95 = (
                    sorted(samples)[math.ceil(len(samples) * 0.95) - 1]
                    if len(samples) >= 20
                    else None
                )
                if p95 is not None and p95 > 2:
                    report.update(status="failed", reason="timing_limits")
                    return report
                if check == "endurance" and (
                    monotonic() - before < 600
                    or (current - started).total_seconds() < 600
                    or not isinstance(observation.participant_seconds, tuple)
                    or len(observation.participant_seconds) != 5
                    or any(
                        not finite(value, 600) or value != 600
                        for value in observation.participant_seconds
                    )
                    or type(observation.text_journeys) is not int
                    or observation.text_journeys < 10
                    or type(observation.speech_journeys) is not int
                    or observation.speech_journeys < 10
                    or type(observation.publications) is not int
                    or observation.publications < 20
                    or not finite(observation.publication_seconds, 60)
                    or observation.next_action_denied is not True
                ):
                    report.update(status="failed", reason="insufficient_endurance")
                    return report
                report["samples"].append(
                    {
                        "check": check,
                        "platform": platform,
                        "browser_version": observation.browser_version
                        if platform != "matrix"
                        else None,
                        "sample_count": len(samples),
                        "p95_seconds": p95,
                        "cutoff_seconds": observation.cutoff_seconds,
                        "failure_seconds": observation.failure_seconds,
                        "evidence_digest": observation.evidence_digest,
                    }
                )
        report.update(status="observed", reason="verified")
    except TimeoutError:
        report["reason"] = "deadline"
    except Exception:
        report["reason"] = "probe_unavailable"
    return report


def parse_browser_scope(content: bytes) -> BrowserScope:
    try:
        if not isinstance(content, bytes) or len(content) > 16384:
            raise ValueError("invalid_browser_manifest")
        document = json.loads(content, object_pairs_hook=unique_object)
        if not isinstance(document, dict) or set(document) != {
            item.name for item in fields(BrowserScope)
        }:
            raise ValueError("invalid_browser_manifest")
        document["provider"] = parse_request(
            json.dumps(document["provider"], allow_nan=False).encode()
        )
        browsers = document["browsers"]
        if (
            not isinstance(browsers, list)
            or len(browsers) != 3
            or any(not isinstance(item, list) or len(item) != 2 for item in browsers)
        ):
            raise ValueError("invalid_browser_manifest")
        document["browsers"] = tuple(tuple(item) for item in browsers)
        return BrowserScope(**document)
    except Exception:
        raise ValueError("invalid_browser_manifest") from None


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("invalid_multimodal_arguments")


def main(argv: Sequence[str] | None = None) -> int:
    request = None
    browser_scope = None
    reason = "authorization_required"
    try:
        parser = SafeParser(add_help=False)
        parser.add_argument("--check-manifest", type=Path)
        parser.add_argument("--check-browser-manifest", type=Path)
        arguments = parser.parse_args(argv)
        if arguments.check_manifest is not None and arguments.check_browser_manifest is not None:
            raise ValueError("invalid_multimodal_arguments")
        if arguments.check_manifest is not None:
            path = arguments.check_manifest
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
                raise ValueError("invalid_manifest")
            request = parse_request(path.read_bytes())
        if arguments.check_browser_manifest is not None:
            path = arguments.check_browser_manifest
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
                raise ValueError("invalid_manifest")
            browser_scope = parse_browser_scope(path.read_bytes())
    except Exception:
        reason = "invalid_manifest"
    report = pending(request, reason=reason).to_dict()
    report["mode"] = "offline_manifest_only"
    if browser_scope is not None:
        report = {
            "schema_version": 1,
            "mode": "offline_browser_manifest_only",
            "status": "pending",
            "live_qualification": "pending",
            "reason": "authorization_required",
            "environment_fingerprint": browser_scope.fingerprint,
            "samples": [],
        }
    print(json.dumps(report, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
