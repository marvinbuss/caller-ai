"""Environment qualification must remain distinct from local metadata validation."""

import asyncio
import importlib
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def case(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("qualify_environment")
    manifest = {
        "environment_ref": "environment-fixture",
        "terraform_revision": "a" * 40,
        "runtime_image": "sha256:" + "b" * 64,
        "management_image": "sha256:" + "c" * 64,
        "plan_review_ref": "plan-review-fixture",
        "privacy_review_ref": "privacy-review-fixture",
        "provider_review_ref": "provider-review-fixture",
        "quota_review_ref": "quota-review-fixture",
        "max_observation_age_seconds": 60,
    }
    request = module.parse_request(json.dumps(manifest).encode())
    now = datetime(2026, 10, 26, 12, tzinfo=UTC)
    return module, manifest, request, now


def test_default_is_pending_with_every_required_check_visible(case):
    module, manifest, request, now = case
    report = asyncio.run(module.qualify(request, now=lambda: now))
    value = report.to_dict()
    assert value["status"] == "pending" and value["live_qualification"] == "pending"
    assert set(value["checks"]) == set(module.CHECKS)
    assert all(check["outcome"] == "unavailable" for check in value["checks"].values())
    assert "environment-fixture" not in json.dumps(value)


def test_no_probe_can_run_without_explicit_authorization(case):
    module, manifest, request, now = case

    class ForbiddenProbe:
        async def observe(self, check, request, *, deadline):
            pytest.fail("probe_ran_without_authorization")

    report = asyncio.run(module.qualify(request, probe=ForbiddenProbe(), now=lambda: now))
    assert report.to_dict()["status"] == "pending"


@pytest.mark.parametrize("kind", ["unknown", "duplicate", "oversize", "boolean_age", "bad_image"])
def test_invalid_or_content_bearing_manifests_are_rejected(case, kind):
    module, manifest, request, now = case
    if kind == "unknown":
        manifest["credential"] = "synthetic-private-token"
    elif kind == "boolean_age":
        manifest["max_observation_age_seconds"] = True
    elif kind == "bad_image":
        manifest["runtime_image"] = "mutable-latest"
    content = json.dumps(manifest).encode()
    if kind == "duplicate":
        content = b'{"environment_ref":"one","environment_ref":"two"}'
    elif kind == "oversize":
        content = b" " * 16385
    with pytest.raises(ValueError):
        module.parse_request(content)


def test_cli_cannot_turn_a_valid_manifest_into_live_qualification(case, tmp_path, capsys):
    module, manifest, request, now = case
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert module.main(["--check-manifest", str(path)]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["mode"] == "offline_manifest_only"
    assert value["live_qualification"] == "pending"
    assert set(value["checks"]) == set(module.CHECKS)
    assert module.main(["--execute", "synthetic-private-token"]) == 2
    assert "synthetic-private" not in capsys.readouterr().out


def test_cli_without_environment_is_pending_not_empty_success(case, capsys):
    module, manifest, request, now = case
    assert module.main([]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "pending" and value["live_qualification"] == "pending"


def test_real_cli_process_preserves_pending_exit_code_and_complete_report(case):
    module, manifest, request, now = case
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "qualify_environment.py")],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2 and not result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "pending" and value["live_qualification"] == "pending"
    assert set(value["checks"]) == set(module.CHECKS)


@pytest.fixture
def observed(case):
    module, manifest, request, now = case
    deadlines = []

    class Authority:
        allowed = True
        mismatch = False
        expired = False

        async def authorize(self, request, *, deadline):
            deadlines.append(deadline)
            if not self.allowed:
                return None
            return module.Authorization(
                "wrong-binding" if self.mismatch else request.fingerprint,
                now + timedelta(seconds=-1 if self.expired else 300),
            )

    class Probe:
        called = []
        override = None
        failed_check = None
        denied_check = None
        unavailable_check = None
        block = False
        entered = asyncio.Event()

        async def observe(self, check, request, *, deadline):
            deadlines.append(deadline)
            self.called.append(check)
            if self.block:
                self.entered.set()
                await asyncio.Event().wait()
            if check == self.failed_check:
                raise RuntimeError("synthetic-private-probe-details")
            outcome = (
                "failed"
                if check == self.denied_check
                else "unavailable"
                if check == self.unavailable_check
                else "passed"
            )
            return self.override or module.Observation(check, request.fingerprint, now, outcome)

    return module, request, now, Authority(), Probe(), deadlines


def qualify(observed, **kwargs):
    module, request, now, authority, probe, deadlines = observed
    return asyncio.run(
        module.qualify(request, authority=authority, probe=probe, now=lambda: now, **kwargs)
    )


def test_complete_injected_observations_are_not_minted_live_approval(observed):
    module, request, now, authority, probe, deadlines = observed
    value = qualify(observed).to_dict()
    assert value["status"] == "observed" and value["live_qualification"] == "pending"
    assert set(probe.called) == set(module.CHECKS) and len(probe.called) == len(module.CHECKS)
    assert len(set(deadlines)) == 1
    assert all(check["outcome"] == "passed" for check in value["checks"].values())


@pytest.mark.parametrize("kind", ["denied", "expired", "foreign_binding"])
def test_denied_expired_or_foreign_authority_prevents_every_probe(observed, kind):
    module, request, now, authority, probe, deadlines = observed
    authority.allowed, authority.expired, authority.mismatch = (
        kind != "denied",
        kind == "expired",
        kind == "foreign_binding",
    )
    assert qualify(observed).to_dict()["status"] == "pending"
    assert not probe.called


@pytest.mark.parametrize(
    "kind", ["foreign_binding", "stale", "future", "naive", "wrong_check", "raw_outcome"]
)
def test_invalid_observation_never_becomes_passing_evidence(observed, kind):
    module, request, now, authority, probe, deadlines = observed
    probe.override = module.Observation(module.CHECKS[0], request.fingerprint, now, "passed")
    if kind == "foreign_binding":
        probe.override = replace(probe.override, fingerprint="wrong-binding")
    elif kind == "stale":
        probe.override = replace(probe.override, observed_at=now - timedelta(seconds=61))
    elif kind == "future":
        probe.override = replace(probe.override, observed_at=now + timedelta(seconds=1))
    elif kind == "naive":
        probe.override = replace(probe.override, observed_at=now.replace(tzinfo=None))
    elif kind == "wrong_check":
        probe.override = replace(probe.override, check="synthetic-private-check")
    else:
        probe.override = replace(probe.override, outcome="synthetic-private-outcome")
    value = qualify(observed).to_dict()
    assert value["checks"][module.CHECKS[0]]["outcome"] == "unavailable"
    assert value["live_qualification"] == "pending"
    assert "synthetic-private" not in json.dumps(value)


@pytest.mark.parametrize("kind", ["exception", "denied", "unavailable"])
def test_one_missing_or_failed_check_is_not_hidden_by_other_passes(observed, caplog, kind):
    module, request, now, authority, probe, deadlines = observed
    selected = module.CHECKS[3]
    if kind == "exception":
        probe.failed_check = selected
    elif kind == "denied":
        probe.denied_check = selected
    else:
        probe.unavailable_check = selected
    value = qualify(observed).to_dict()
    assert value["status"] == ("failed" if kind == "denied" else "pending")
    assert value["checks"][selected]["outcome"] != "passed"
    assert len(probe.called) == len(module.CHECKS)
    assert "synthetic-private" not in json.dumps(value) + caplog.text


def test_missing_probe_adapter_leaves_all_checks_pending(observed):
    module, request, now, authority, probe, deadlines = observed
    report = asyncio.run(module.qualify(request, authority=authority, now=lambda: now))
    assert report.to_dict()["status"] == "pending" and not probe.called


def test_shared_deadline_stops_blocked_probe_and_keeps_remaining_checks_visible(observed):
    module, request, now, authority, probe, deadlines = observed
    probe.block = True
    value = qualify(observed, budget_seconds=0.01).to_dict()
    assert value["status"] == "pending" and set(value["checks"]) == set(module.CHECKS)
    assert probe.called == [module.CHECKS[0]]


def test_cancellation_propagates_without_becoming_qualification(observed):
    module, request, now, authority, probe, deadlines = observed
    probe.block = True

    async def scenario():
        task = asyncio.create_task(
            module.qualify(request, authority=authority, probe=probe, now=lambda: now)
        )
        await probe.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


@pytest.mark.parametrize("budget", [0, -1, True, float("nan"), float("inf"), 11])
def test_invalid_budget_prevents_authorization_or_probe_effects(observed, budget):
    module, request, now, authority, probe, deadlines = observed
    with pytest.raises(ValueError, match="invalid_qualification_budget"):
        qualify(observed, budget_seconds=budget)
    assert not deadlines


def test_authority_failure_does_not_probe_or_reflect_raw_details(observed, caplog):
    module, request, now, authority, probe, deadlines = observed

    async def denied(request, *, deadline):
        raise RuntimeError("synthetic-private-authority-details")

    authority.authorize = denied
    value = qualify(observed).to_dict()
    assert value["status"] == "pending" and not probe.called
    assert all(check["reason"] == "authorization_unavailable" for check in value["checks"].values())
    assert "synthetic-private" not in json.dumps(value) + caplog.text


def test_authorization_expiry_during_probe_invalidates_that_evidence_and_stops(observed):
    module, request, now, authority, probe, deadlines = observed
    instant = [now]
    observe = probe.observe

    async def expired(check, request, *, deadline):
        value = await observe(check, request, deadline=deadline)
        instant[0] += timedelta(seconds=301)
        return value

    probe.observe = expired
    value = asyncio.run(
        module.qualify(request, authority=authority, probe=probe, now=lambda: instant[0])
    ).to_dict()
    assert value["status"] == "pending" and probe.called == [module.CHECKS[0]]
    assert all(check["outcome"] == "unavailable" for check in value["checks"].values())


def test_incomplete_or_duplicate_report_cannot_claim_all_checks_observed(case):
    module, manifest, request, now = case
    check = module.CheckResult(module.CHECKS[0], "passed", "verified")
    for checks in [(), (check,), (check,) * len(module.CHECKS)]:
        with pytest.raises(ValueError, match="incomplete_qualification_report"):
            module.QualificationReport(request.fingerprint, checks).to_dict()


def test_binding_is_stable_for_key_order_but_changes_with_any_image_or_review(case):
    module, manifest, request, now = case
    reordered = module.parse_request(json.dumps(dict(reversed(list(manifest.items())))).encode())
    assert reordered.fingerprint == request.fingerprint
    assert replace(request, runtime_image="sha256:" + "d" * 64).fingerprint != request.fingerprint
    assert (
        replace(request, privacy_review_ref="different-review").fingerprint != request.fingerprint
    )


def test_probe_permission_denial_is_a_failure_not_unavailable(observed):
    module, request, now, authority, probe, deadlines = observed

    async def denied(check, request, *, deadline):
        raise PermissionError("synthetic-private-denial-details")

    probe.observe = denied
    value = qualify(observed).to_dict()
    assert value["status"] == "failed"
    assert all(check["outcome"] == "failed" for check in value["checks"].values())


def test_cli_uses_no_network_process_or_environment_credential_fallback(case, monkeypatch, capsys):
    import socket
    import subprocess

    module, manifest, request, now = case

    def forbidden(*args, **kwargs):
        pytest.fail("qualification_cli_used_live_execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "synthetic-private-environment-secret")
    assert module.main([]) == 2
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err
