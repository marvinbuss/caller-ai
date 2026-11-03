"""Multimodal qualification remains pending without explicit scoped live authorization."""

import asyncio
import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.parametrize("project", ["backend", "management"])
def test_service_alert_observation_contract_is_local_bounded_and_private(project):
    import os
    import subprocess

    root = Path(__file__).resolve().parents[3]
    directory = root / "code" / project
    python = directory / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    paths = ["tests/unit/test_observability.py"]
    if project == "management":
        paths.append("tests/integration/test_management_composition.py")
    result = subprocess.run(
        [
            str(python),
            "-B",
            "-m",
            "pytest",
            *paths,
            "-k",
            "test_alert_thresholds or alert_sink_failure or owned_timers or lifecycle_binds",
            "-q",
            "--tb=short",
            "--show-capture=no",
        ],
        cwd=directory,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]
    assert "4 passed" in result.stdout


@pytest.fixture
def browser_case(case):
    module, manifest, provider, now = case
    scope = module.BrowserScope(
        provider=provider,
        mode="device",
        foundry_evidence_digest="a" * 64,
        device_review_ref="device-approved",
        endurance_review_ref=None,
        browsers=(
            ("edge_windows", "130.0.0.1"),
            ("chrome_windows", "130.0.0.2"),
            ("chrome_linux", "130.0.0.3"),
        ),
        max_seconds=600,
    )
    return module, scope, now


def test_private_input_checker_reports_only_missing_schema_fields(case, tmp_path, capsys):
    module, _manifest, _provider, _now = case
    path = tmp_path / "qualification.toml"
    marker = "private-payload-fixture"
    path.write_text("[identity]\nclient_" + 'secret = "' + marker + '"\n[foundry]\n')
    report = module.inspect_qualification_inputs(path)
    assert report["mode"] == "offline_input_readiness"
    assert report["live_qualification"] == "pending"
    assert report["execution_started"] is False
    assert report["missing_inputs"]["provider_mapping"] == [
        "foundry.live_account_resource_id",
        "foundry.text_account_resource_id",
        "foundry.text_model",
        "foundry.text_model_version",
        "foundry.text_region",
        "foundry.text_sku",
    ]
    assert marker not in json.dumps(report)
    assert module.main(["--check-qualification-inputs", str(path)]) == 2
    output = capsys.readouterr()
    assert marker not in output.out + output.err
    assert json.loads(output.out)["missing_inputs"] == report["missing_inputs"]


def test_private_input_checker_invalid_toml_does_not_echo_values(case, tmp_path):
    module, _manifest, _provider, _now = case
    path = tmp_path / "qualification.toml"
    marker = "private-error-fixture"
    path.write_text('invalid = "' + marker)
    report = module.inspect_qualification_inputs(path)
    assert report["reason"] == "invalid_input_profile"
    assert report["execution_started"] is False
    assert marker not in json.dumps(report)


@pytest.mark.parametrize(
    "content", [b"#" * 65537, b"value = 1\nvalue = 2\n"], ids=["oversized", "duplicate-key"]
)
def test_private_input_checker_rejects_bounds_and_duplicate_toml(case, tmp_path, content):
    module, _manifest, _provider, _now = case
    path = tmp_path / "qualification.toml"
    path.write_bytes(content)
    report = module.inspect_qualification_inputs(path)
    assert report["reason"] == "invalid_input_profile"
    assert report["execution_started"] is False


def test_private_input_checker_never_constructs_network_clients(case, tmp_path, monkeypatch):
    module, _manifest, _provider, _now = case
    path = tmp_path / "qualification.toml"
    path.write_text("[foundry]\n")

    def forbidden(*arguments, **keywords):
        pytest.fail("input_checker_network_forbidden")

    monkeypatch.setattr(module.httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(module.httpx, "Client", forbidden)
    report = module.inspect_qualification_inputs(path)
    assert report["reason"] == "missing_reference_inputs"
    assert report["execution_started"] is False


def test_browser_scope_requires_explicit_mode_and_reviewed_versions(case):
    module, manifest, provider, now = case
    with pytest.raises(ValueError):
        module.BrowserScope(
            provider=provider,
            mode="",
            foundry_evidence_digest="a" * 64,
            device_review_ref="device-approved",
            endurance_review_ref=None,
            browsers=(("bundled_chromium", "130.0.0.1"),),
            max_seconds=600,
        )


def test_browser_linux_deferral_is_explicit_bound_and_reported(browser_case):
    from dataclasses import asdict, replace

    module, scope, now = browser_case
    windows = tuple(item for item in scope.browsers if item[0] != "chrome_linux")
    with pytest.raises(ValueError):
        replace(scope, browsers=windows)
    deferred = replace(scope, browsers=windows, deferred_platforms=("chrome_linux",))
    assert deferred.fingerprint != scope.fingerprint
    parsed = module.parse_browser_scope(json.dumps(asdict(deferred)).encode())
    assert parsed == deferred
    report = asyncio.run(module.qualify_browser(deferred, now=lambda: now))
    assert report["deferred_platforms"] == ["chrome_linux"]
    assert report["status"] == report["live_qualification"] == "pending"
    assert report["samples"] == []
    with pytest.raises(ValueError):
        replace(scope, deferred_platforms=("edge_windows",))


def test_browser_full_platform_manifest_remains_compatible(browser_case):
    from dataclasses import asdict

    module, scope, _now = browser_case
    document = asdict(scope)
    document.pop("deferred_platforms", None)
    parsed = module.parse_browser_scope(json.dumps(document).encode())
    assert parsed.deferred_platforms == ()
    assert {platform for platform, _version in parsed.browsers} == module.BROWSER_PLATFORMS


def test_browser_and_endurance_default_never_invoke_devices_or_models(browser_case):
    module, scope, now = browser_case

    class Forbidden:
        async def observe(self, *arguments, **keywords):
            pytest.fail("unapproved_browser_probe")

    report = asyncio.run(module.qualify_browser(scope, probe=Forbidden(), now=lambda: now))
    assert report["status"] == report["live_qualification"] == "pending"
    assert report["reason"] == "authorization_required"


@pytest.mark.parametrize(
    "mode,short", [("device", False), ("live_endurance", True), ("live_endurance", False)]
)
def test_browser_observations_require_real_scope_closure_timing_and_full_endurance(
    browser_case, mode, short
):
    from dataclasses import replace

    module, scope, started = browser_case
    if mode == "live_endurance":
        scope = replace(
            scope,
            mode=mode,
            endurance_review_ref="endurance-approved",
            max_seconds=900,
            device_evidence_digest="c" * 64,
        )
    clock, ticks, calls = [started], [100.0], []

    class Authority:
        async def authorize(self, scope, *, deadline):
            return module.Authorization(scope.fingerprint, clock[0] + timedelta(seconds=1200))

    class Probe:
        async def observe(self, check, scope, *, platform, deadline, max_cost_usd):
            calls.append((check, platform, max_cost_usd))
            duration = 599.0 if short else 600.0
            if check == "endurance":
                ticks[0] += duration
                clock[0] += timedelta(seconds=duration)
            return module.BrowserObservation(
                check=check,
                platform=platform,
                fingerprint=scope.fingerprint,
                observed_at=clock[0],
                evidence_digest="b" * 64,
                browser_version=dict(scope.browsers).get(platform, ""),
                stable_verified=True,
                confirmed_closure=True,
                cost_usd=0.01,
                cutoff_seconds=0.5,
                failure_seconds=1.0,
                latency_seconds=(0.2,) * 20,
                participant_seconds=(duration,) * 5 if check == "endurance" else (),
                text_journeys=10 if check == "endurance" else 0,
                speech_journeys=10 if check == "endurance" else 0,
                publications=20 if check == "endurance" else 0,
                publication_seconds=40.0,
                next_action_denied=True,
            )

    report = asyncio.run(
        module.qualify_browser(
            scope,
            authorization=Authority(),
            probe=Probe(),
            now=lambda: clock[0],
            monotonic=lambda: ticks[0],
        )
    )
    assert report["live_qualification"] == "pending"
    assert report["status"] == ("failed" if short else "observed")
    assert calls and all(0 < budget <= scope.provider.max_cost_usd for _, _, budget in calls)
    assert not {"live_endpoint", "text_endpoint", "device_review_ref"} & set(report)
    if mode == "device":
        assert len(calls) == len(module.BROWSER_CHECKS) * 3 and len(report["samples"]) == len(calls)
    else:
        assert len(calls) == 1 and (report["reason"] == "insufficient_endurance") == short


@pytest.mark.parametrize(
    "fault", ["stable", "version", "closure", "cost", "latency", "cutoff", "clock"]
)
def test_browser_invalid_observations_never_report_acceptance(browser_case, fault):
    module, scope, started = browser_case
    ticks, clock, calls = [0.0], [started], []

    class Authority:
        async def authorize(self, scope, *, deadline):
            return module.Authorization(scope.fingerprint, started + timedelta(seconds=900))

    class Probe:
        async def observe(self, check, scope, *, platform, deadline, max_cost_usd):
            calls.append(check)
            if fault == "clock":
                ticks[0] += 5
            return module.BrowserObservation(
                check=check,
                platform=platform,
                fingerprint=scope.fingerprint,
                observed_at=clock[0],
                evidence_digest="b" * 64,
                stable_verified=fault != "stable",
                browser_version="0.0.0.0" if fault == "version" else dict(scope.browsers)[platform],
                confirmed_closure=fault != "closure",
                cost_usd=None if fault == "cost" else 0,
                cutoff_seconds=1.1 if fault == "cutoff" else 0.1,
                failure_seconds=1,
                latency_seconds=(2.1 if fault == "latency" else 0.2,) * 20,
                participant_seconds=(),
                text_journeys=0,
                speech_journeys=0,
                publications=0,
                publication_seconds=0,
                next_action_denied=False,
            )

    report = asyncio.run(
        module.qualify_browser(
            scope,
            authorization=Authority(),
            probe=Probe(),
            now=lambda: clock[0],
            monotonic=lambda: ticks[0],
        )
    )
    assert report["status"] != "observed" and report["live_qualification"] == "pending"
    assert len(calls) == 1


def test_browser_cli_manifest_stays_offline_and_cannot_execute(browser_case, tmp_path, capsys):
    from dataclasses import asdict

    module, scope, now = browser_case
    path = tmp_path / "browser-scope.json"
    path.write_text(json.dumps(asdict(scope)))
    assert module.main(["--check-browser-manifest", str(path)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == report["live_qualification"] == "pending"
    assert report["mode"] == "offline_browser_manifest_only"
    assert report["environment_fingerprint"] == scope.fingerprint
    assert module.main(["--live-endurance"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "pending"


@pytest.mark.parametrize(
    "fault",
    ["extra", "duplicate", "missing-mode", "endurance-without-approval", "version", "oversized"],
)
def test_browser_scope_parser_rejects_unreviewed_content_before_any_probe(browser_case, fault):
    from dataclasses import asdict

    module, scope, now = browser_case
    document = asdict(scope)
    if fault == "extra":
        document["recording"] = "synthetic-private-content"
    elif fault == "missing-mode":
        document.pop("mode")
    elif fault == "endurance-without-approval":
        document["mode"] = "live_endurance"
    elif fault == "version":
        document["browsers"] = (("bundled_chromium", "unknown"),) * 3
    encoded = json.dumps(document).encode()
    if fault == "duplicate":
        encoded = b'{"mode":"device","mode":"live_endurance"}'
    elif fault == "oversized":
        encoded = b" " * 16385
    with pytest.raises(ValueError, match="invalid_browser_manifest"):
        module.parse_browser_scope(encoded)


@pytest.mark.parametrize("fault", ["cost", "authorization"])
def test_browser_each_probe_reauthorizes_and_receives_only_remaining_cost(browser_case, fault):
    module, scope, started = browser_case
    calls, grants = [], []

    class Authority:
        async def authorize(self, scope, *, deadline):
            grants.append(scope.fingerprint)
            if fault == "authorization" and calls:
                return None
            return module.Authorization(scope.fingerprint, started + timedelta(seconds=900))

    class Probe:
        async def observe(self, check, scope, *, platform, deadline, max_cost_usd):
            calls.append(max_cost_usd)
            return module.BrowserObservation(
                check=check,
                platform=platform,
                fingerprint=scope.fingerprint,
                observed_at=started,
                evidence_digest="b" * 64,
                stable_verified=True,
                browser_version=dict(scope.browsers)[platform],
                confirmed_closure=True,
                cost_usd=0.5,
                cutoff_seconds=0.1,
                failure_seconds=1,
                latency_seconds=(),
                participant_seconds=(),
                text_journeys=0,
                speech_journeys=0,
                publications=0,
                publication_seconds=0,
                next_action_denied=False,
            )

    report = asyncio.run(
        module.qualify_browser(
            scope,
            authorization=Authority(),
            probe=Probe(),
            now=lambda: started,
            monotonic=lambda: 0.0,
        )
    )
    assert report["status"] == report["live_qualification"] == "pending"
    assert calls == ([1.0, 0.5] if fault == "cost" else [1.0])
    assert len(grants) == len(calls) + 1


@pytest.fixture
def case(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("verify_multimodal")
    manifest = {
        "environment_ref": "environment-fixture",
        "identity_review_ref": "identity-review-fixture",
        "provider_review_ref": "provider-review-fixture",
        "quota_review_ref": "quota-review-fixture",
        "residency_review_ref": "residency-review-fixture",
        "lifecycle_review_ref": "lifecycle-review-fixture",
        "retention_review_ref": "retention-review-fixture",
        "cost_review_ref": "cost-review-fixture",
        "live_endpoint": "https://approved-fixture.openai.azure.com",
        "text_endpoint": "https://approved-fixture.openai.azure.com",
        "text_deployment": "reasoning-fixture",
        "live_model": "gpt-live-1",
        "live_model_version": "2026-09-10",
        "live_region": "eastus2",
        "available_live_sessions": 5,
        "text_features": ["streaming", "function_tools", "storage_disabled", "structured_output"],
        "max_seconds": 30,
        "max_cost_usd": 1.0,
        "max_observation_age_seconds": 60,
    }
    request = module.parse_request(json.dumps(manifest).encode())
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    return module, manifest, request, now


@pytest.mark.parametrize(
    "region", ["eastus2", "centralus", "canadacentral", "swedencentral", "unreviewed-region"]
)
def test_provider_region_allowlist_preserves_qualification_gate(case, region):
    module, manifest, _request, now = case
    manifest["live_region"] = region
    if region == "unreviewed-region":
        with pytest.raises(ValueError):
            module.parse_request(json.dumps(manifest).encode())
        return
    request = module.parse_request(json.dumps(manifest).encode())
    assert request.live_region == region
    report = asyncio.run(module.qualify(request, now=lambda: now)).to_dict()
    assert report["status"] == report["live_qualification"] == "pending"
    assert all(value["outcome"] == "unavailable" for value in report["checks"].values())


def test_default_and_unapproved_runner_never_allocate_or_discover_credentials(case):
    module, manifest, request, now = case

    class Forbidden:
        async def observe(self, check, request, *, deadline):
            pytest.fail("unapproved_multimodal_probe_ran")

    report = asyncio.run(module.qualify(request, probe=Forbidden(), now=lambda: now)).to_dict()
    assert report["status"] == report["live_qualification"] == "pending"
    assert set(report["checks"]) == set(module.CHECKS)
    assert all(value["outcome"] == "unavailable" for value in report["checks"].values())
    assert "environment-fixture" not in json.dumps(report)
    assert not any("phone" in check or "hosting" in check for check in module.CHECKS)


@pytest.mark.parametrize(
    "fault",
    ["extra", "duplicate", "oversized", "public", "quota", "budget", "features", "voice-only-text"],
)
def test_unreviewed_or_content_bearing_manifests_reject_before_execution(case, fault):
    module, manifest, request, now = case
    if fault == "extra":
        manifest["credential"] = "synthetic-private-token"
    elif fault == "public":
        manifest["live_endpoint"] = "https://api.openai.com"
    elif fault == "quota":
        manifest["available_live_sessions"] = 4
    elif fault == "budget":
        manifest["max_seconds"] = True
    elif fault == "features":
        manifest["text_features"] = ["streaming"]
    elif fault == "voice-only-text":
        manifest["text_deployment"] = "gpt-live-1"
    content = json.dumps(manifest).encode()
    if fault == "duplicate":
        content = b'{"environment_ref":"one","environment_ref":"two"}'
    elif fault == "oversized":
        content = b" " * 16385
    with pytest.raises(ValueError):
        module.parse_request(content)


def test_cli_valid_manifest_is_not_authorization_or_live_success(case, tmp_path, capsys):
    module, manifest, request, now = case
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert module.main(["--check-manifest", str(path)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == report["live_qualification"] == "pending"
    assert report["mode"] == "offline_manifest_only"
    assert set(report["checks"]) == set(module.CHECKS)
    assert module.main([]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "pending"
    assert module.main(["--execute", "synthetic-private-token"]) == 2
    assert "synthetic-private" not in capsys.readouterr().out


@pytest.fixture
def observed(case):
    module, manifest, request, now = case

    class Authority:
        fingerprint = request.fingerprint
        expires_at = now + timedelta(seconds=60)
        calls = []

        async def authorize(self, request, *, deadline):
            self.calls.append(deadline)
            return module.Authorization(self.fingerprint, self.expires_at)

    class Probe:
        calls = []
        uncertain = False
        mismatch = False
        failure = False
        costly = False
        quota_denied = False
        budgets = []

        async def observe(self, check, request, *, deadline, max_cost_usd=None):
            self.calls.append((check, deadline))
            self.budgets.append(max_cost_usd)
            if self.failure:
                print("synthetic-private-provider-body")
                raise RuntimeError("synthetic-private-probe-error")
            return module.Observation(
                check,
                "b" * 64 if self.mismatch else request.fingerprint,
                now,
                "failed"
                if self.quota_denied and check == "available_quota"
                else "unavailable"
                if self.uncertain and check == "confirmed_closure"
                else "passed",
                confirmed=not (self.uncertain and check == "confirmed_closure"),
                cost_usd=2.0 if self.costly else 0.01,
                evidence_digest="a" * 64,
            )

    return module, request, now, Authority(), Probe()


@pytest.mark.parametrize("approval", ["absent", "wrong-scope", "expired", "approved"])
def test_provider_executor_authorizes_before_constructing_and_owns_cleanup(observed, approval):
    module, request, now, authority, probe = observed
    created, closed = [], []

    class Factory:
        async def create(self, scope, *, deadline, max_cost_usd):
            assert scope.fingerprint == request.fingerprint
            assert max_cost_usd == request.max_cost_usd
            created.append(deadline)
            return probe

        async def close(self, *, deadline):
            assert deadline > asyncio.get_running_loop().time()
            closed.append(True)
            return True

    if approval == "wrong-scope":
        authority.fingerprint = "b" * 64
    elif approval == "expired":
        authority.expires_at = now - timedelta(seconds=1)
    report = asyncio.run(
        module.execute_provider_scope(
            request,
            authorization=None if approval == "absent" else authority,
            factory=Factory(),
            now=lambda: now,
        )
    )
    assert report["live_qualification"] == "pending"
    if approval != "approved":
        assert not created and not closed and not probe.calls
        assert report["cleanup_status"] == "not_started"
        return
    assert report["status"] == "observed" and report["cleanup_status"] == "complete"
    assert len(created) == len(closed) == 1
    assert [check for check, _deadline in probe.calls] == list(module.CHECKS)
    assert set(authority.calls + created + [deadline for _, deadline in probe.calls]) == {
        created[0]
    }


@pytest.mark.parametrize(
    "fault",
    [
        "authorization-error",
        "construction-error",
        "stalled-construction",
        "revoked",
        "cleanup-error",
        "cleanup-unknown",
        "cancelled",
    ],
)
def test_provider_executor_failure_cleanup_and_private_output(observed, fault, capsys):
    module, request, now, authority, probe = observed
    effects = []

    class Factory:
        async def create(self, scope, *, deadline, max_cost_usd):
            effects.append("create")
            if fault == "construction-error":
                print("synthetic-private-executor-body")
                raise RuntimeError("synthetic-private-executor-error")
            if fault == "stalled-construction":
                await asyncio.Event().wait()
            if fault == "revoked":
                authority.expires_at = now - timedelta(seconds=1)
            if fault == "cancelled":
                raise asyncio.CancelledError
            return probe

        async def close(self, *, deadline):
            effects.append("close")
            if fault == "cleanup-error":
                print("synthetic-private-cleanup-body")
                raise RuntimeError("synthetic-private-cleanup-error")
            return fault != "cleanup-unknown"

    class BrokenAuthority:
        async def authorize(self, scope, *, deadline):
            print("synthetic-private-authority-body")
            raise RuntimeError("synthetic-private-authority-error")

    if fault == "stalled-construction":
        authority.expires_at = now + timedelta(seconds=0.03)

    async def scenario():
        started = asyncio.get_running_loop().time()
        report = await module.execute_provider_scope(
            request,
            authorization=BrokenAuthority() if fault == "authorization-error" else authority,
            factory=Factory(),
            now=lambda: now,
        )
        if fault == "stalled-construction":
            assert asyncio.get_running_loop().time() - started < 0.5
        return report

    if fault == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(scenario())
        assert effects == ["create", "close"]
    else:
        report = asyncio.run(scenario())
        assert report["status"] == report["live_qualification"] == "pending"
        expected = (
            "not_started"
            if fault == "authorization-error"
            else ("pending" if fault in {"cleanup-error", "cleanup-unknown"} else "complete")
        )
        assert report["cleanup_status"] == expected
        assert effects == ([] if fault == "authorization-error" else ["create", "close"])
        if fault in {
            "authorization-error",
            "construction-error",
            "stalled-construction",
            "revoked",
        }:
            assert not probe.calls
        assert "synthetic-private" not in json.dumps(report)
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err


def test_qualification_child_deadline_cannot_extend_original_scope(observed):
    module, request, now, authority, original = observed
    deadlines = []

    class Probe:
        async def observe(self, check, scope, *, deadline, max_cost_usd):
            deadlines.append(deadline)
            await asyncio.Event().wait()

    async def scenario():
        started = asyncio.get_running_loop().time()
        deadline = started + 0.03
        report = await module.qualify(
            request, authorization=authority, probe=Probe(), now=lambda: now, deadline=deadline
        )
        assert asyncio.get_running_loop().time() - started < 0.5
        assert deadlines == [deadline] and set(authority.calls) == {deadline}
        return report.to_dict()

    report = asyncio.run(scenario())
    assert report["checks"][module.CHECKS[0]]["reason"] == "deadline"
    assert report["live_qualification"] == "pending"


@pytest.fixture
def azure_metadata_case(case):
    from types import SimpleNamespace

    import httpx

    module, manifest, request, now = case
    account = (
        "/subscriptions/11111111-2222-4333-8444-555555555555/resourceGroups/fixture-group"
        "/providers/Microsoft.CognitiveServices/accounts/fixture-account"
    )
    live = module.AzureDeploymentBinding(
        account_resource_id=account,
        endpoint=request.live_endpoint,
        deployment="live-fixture",
        model=request.live_model,
        version=request.live_model_version,
        region=request.live_region,
        sku="GlobalStandard",
    )
    text = module.AzureDeploymentBinding(
        account_resource_id=account,
        endpoint=request.text_endpoint,
        deployment=request.text_deployment,
        model="reasoning-model-fixture",
        version="1",
        region=request.live_region,
        sku="GlobalStandard",
    )
    sent, grants, tokens = [], [], []

    class Authority:
        async def authorize(self, scope, *, deadline):
            assert scope.request_fingerprint == request.fingerprint
            assert scope.live == live and scope.text == text
            assert scope.fingerprint != request.fingerprint
            grants.append(scope.fingerprint)
            return module.Authorization(scope.fingerprint, now + timedelta(seconds=60))

    class Credential:
        async def get_token(self, scope):
            tokens.append(scope)
            return SimpleNamespace(token="synthetic-access", expires_on=now.timestamp() + 3600)

    def respond(outgoing):
        assert outgoing.method == "GET" and outgoing.url.host == "management.azure.com"
        assert dict(outgoing.url.params) == {"api-version": "2024-10-01"}
        assert outgoing.headers["authorization"] == "Bearer synthetic-access"
        sent.append(outgoing.url.path)
        if "/deployments/" not in outgoing.url.path:
            return httpx.Response(
                200,
                json={
                    "id": account,
                    "name": "fixture-account",
                    "type": "Microsoft.CognitiveServices/accounts",
                    "kind": "OpenAI",
                    "location": request.live_region,
                    "properties": {
                        "endpoint": request.live_endpoint,
                        "provisioningState": "Succeeded",
                    },
                },
            )
        binding = live if outgoing.url.path.endswith("/live-fixture") else text
        return httpx.Response(
            200,
            json={
                "id": account + "/deployments/" + binding.deployment,
                "name": binding.deployment,
                "type": "Microsoft.CognitiveServices/accounts/deployments",
                "sku": {"name": binding.sku},
                "properties": {
                    "provisioningState": "Succeeded",
                    "model": {
                        "format": "OpenAI",
                        "name": binding.model,
                        "version": binding.version,
                    },
                },
            },
        )

    probe = module.AzureDeploymentMetadataProbe(
        request,
        live=live,
        text=text,
        authorization=Authority(),
        credential=Credential(),
        transport=httpx.MockTransport(respond),
        now=lambda: now,
    )
    assert not sent and not grants and not tokens
    return SimpleNamespace(
        module=module,
        request=request,
        now=now,
        live=live,
        text=text,
        account=account,
        sent=sent,
        grants=grants,
        tokens=tokens,
        probe=probe,
        authority_type=Authority,
        credential_type=Credential,
        respond=respond,
    )


def test_azure_deployment_probe_reads_only_reviewed_exact_bindings(azure_metadata_case):
    fixture = azure_metadata_case
    request, probe = fixture.request, fixture.probe

    async def scenario():
        deadline = asyncio.get_running_loop().time() + 5
        observation = await probe.observe(
            "existing_bindings", request, deadline=deadline, max_cost_usd=request.max_cost_usd
        )
        unavailable = await probe.observe(
            "workload_identity", request, deadline=deadline, max_cost_usd=request.max_cost_usd
        )
        assert await probe.close(deadline=deadline) is True
        return observation, unavailable

    observation, unavailable = asyncio.run(scenario())
    assert observation.outcome == "passed" and observation.confirmed is True
    assert observation.fingerprint == request.fingerprint and observation.cost_usd == 0
    assert len(observation.evidence_digest) == 64
    assert unavailable.outcome == "unavailable" and unavailable.confirmed is False
    assert fixture.sent == [
        fixture.account,
        fixture.account + "/deployments/live-fixture",
        fixture.account,
        fixture.account + "/deployments/" + request.text_deployment,
    ]
    assert len(fixture.grants) == 9
    assert fixture.tokens == ["https://management.azure.com/.default"] * 4
    assert fixture.account not in str(observation) and request.live_endpoint not in str(observation)


@pytest.mark.parametrize(
    "fault",
    [
        "no-approval",
        "wrong-approval",
        "expired",
        "revoked-after-token",
        "wrong-id",
        "wrong-region",
        "wrong-model",
        "wrong-version",
        "wrong-sku",
        "not-ready",
        "foreign-endpoint",
        "forbidden",
        "redirect",
        "oversized",
        "duplicate",
        "compressed",
    ],
)
def test_azure_deployment_probe_denies_unknown_or_unreviewed_evidence(
    azure_metadata_case, fault, capsys
):
    import httpx

    fixture = azure_metadata_case
    module, request, now = fixture.module, fixture.request, fixture.now
    revoked, sent = [False], []

    class Authority:
        async def authorize(self, scope, *, deadline):
            if fault == "no-approval" or revoked[0]:
                return None
            return module.Authorization(
                "b" * 64 if fault == "wrong-approval" else scope.fingerprint,
                now + timedelta(seconds=-1 if fault == "expired" else 60),
            )

    class Credential(fixture.credential_type):
        async def get_token(self, scope):
            if fault == "revoked-after-token":
                revoked[0] = True
            return await super().get_token(scope)

    class OuterAuthority:
        async def authorize(self, scope, *, deadline):
            return module.Authorization(scope.fingerprint, now + timedelta(seconds=60))

    def respond(outgoing):
        sent.append(outgoing.url.path)
        if fault == "forbidden":
            return httpx.Response(403, json={"error": "synthetic-private-provider-body"})
        if fault == "redirect":
            return httpx.Response(302, headers={"Location": "https://foreign.example.invalid/"})
        if fault == "oversized":
            return httpx.Response(200, content=b" " * 65537)
        if fault == "duplicate":
            return httpx.Response(200, content=b'{"id":"one","id":"two"}')
        if fault == "compressed":
            return httpx.Response(200, content=b"", headers={"Content-Encoding": "gzip"})
        document = fixture.respond(outgoing).json()
        deployment = "/deployments/" in outgoing.url.path
        if fault == "wrong-id":
            document["id"] += "-foreign"
        elif fault == "wrong-region":
            document["location"] = "foreign-region"
        elif fault == "foreign-endpoint":
            document["properties"]["endpoint"] = "https://foreign.openai.azure.com"
        elif fault == "not-ready":
            document["properties"]["provisioningState"] = "Failed"
        elif deployment and fault == "wrong-model":
            document["properties"]["model"]["name"] = "different-model"
        elif deployment and fault == "wrong-version":
            document["properties"]["model"]["version"] = "different-version"
        elif deployment and fault == "wrong-sku":
            document["sku"]["name"] = "Standard"
        return httpx.Response(200, json=document)

    probe = module.AzureDeploymentMetadataProbe(
        request,
        live=fixture.live,
        text=fixture.text,
        authorization=Authority(),
        credential=Credential(),
        transport=httpx.MockTransport(respond),
        now=lambda: now,
    )
    report = asyncio.run(
        module.qualify(request, authorization=OuterAuthority(), probe=probe, now=lambda: now)
    ).to_dict()
    assert report["status"] == report["live_qualification"] == "pending"
    assert report["checks"]["existing_bindings"]["outcome"] == "unavailable"
    assert all(value["outcome"] == "unavailable" for value in report["checks"].values())
    if fault in {"no-approval", "wrong-approval", "expired", "revoked-after-token"}:
        assert not sent
    else:
        assert 1 <= len(sent) <= 2
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err + json.dumps(report)


def test_azure_metadata_deadline_is_capped_and_resource_paths_reject_before_io(azure_metadata_case):
    from dataclasses import replace

    import httpx

    fixture = azure_metadata_case
    module, request, deadlines = fixture.module, fixture.request, []

    class Authority(fixture.authority_type):
        async def authorize(self, scope, *, deadline):
            deadlines.append(deadline)
            return await super().authorize(scope, deadline=deadline)

    with pytest.raises(ValueError):
        replace(fixture.live, account_resource_id=fixture.account + "/../foreign")
    assert not fixture.sent and not fixture.tokens
    probe = module.AzureDeploymentMetadataProbe(
        request,
        live=fixture.live,
        text=fixture.text,
        authorization=Authority(),
        credential=fixture.credential_type(),
        transport=httpx.MockTransport(fixture.respond),
        now=lambda: fixture.now,
    )

    async def scenario():
        caller_deadline = asyncio.get_running_loop().time() + request.max_seconds + 100
        observation = await probe.observe(
            "existing_bindings",
            request,
            deadline=caller_deadline,
            max_cost_usd=request.max_cost_usd,
        )
        assert observation.outcome == "passed"
        assert deadlines and max(deadlines) < caller_deadline
        assert max(deadlines) <= asyncio.get_running_loop().time() + request.max_seconds
        count = len(fixture.sent)
        replay = await probe.observe(
            "existing_bindings",
            request,
            deadline=caller_deadline,
            max_cost_usd=request.max_cost_usd,
        )
        assert replay.outcome == "unavailable" and len(fixture.sent) == count
        assert await probe.close(deadline=caller_deadline) is True

    asyncio.run(scenario())


def test_authorized_observations_are_bound_bounded_and_not_blanket_live_qualification(observed):
    module, request, now, authority, probe = observed
    report = asyncio.run(
        module.qualify(request, authorization=authority, probe=probe, now=lambda: now)
    ).to_dict()
    assert report["status"] == "observed" and report["live_qualification"] == "pending"
    assert [check for check, deadline in probe.calls] == list(module.CHECKS)
    assert len(set(deadline for check, deadline in probe.calls)) == 1
    assert len(authority.calls) >= len(module.CHECKS)
    assert all(value["outcome"] == "passed" for value in report["checks"].values())


@pytest.mark.parametrize(
    "fault", ["wrong-approval", "expired", "mismatch", "unknown-closure", "failure", "cost"]
)
def test_invalid_or_uncertain_live_observation_never_qualifies_or_leaks(observed, fault, capsys):
    module, request, now, authority, probe = observed
    if fault == "wrong-approval":
        authority.fingerprint = "b" * 64
    elif fault == "expired":
        authority.expires_at = now - timedelta(seconds=1)
    elif fault == "mismatch":
        probe.mismatch = True
    elif fault == "unknown-closure":
        probe.uncertain = True
    elif fault == "failure":
        probe.failure = True
    elif fault == "cost":
        probe.costly = True
    value = asyncio.run(
        module.qualify(request, authorization=authority, probe=probe, now=lambda: now)
    ).to_dict()
    assert value["status"] != "observed" and value["live_qualification"] == "pending"
    assert "synthetic-private" not in json.dumps(value) + capsys.readouterr().out
    if fault in {"wrong-approval", "expired"}:
        assert not probe.calls
    if fault == "unknown-closure":
        assert value["checks"]["confirmed_closure"]["outcome"] == "unavailable"
    if fault == "cost":
        assert len(probe.calls) == 1


def test_actual_cli_process_stays_pending_and_never_loads_live_ports(case):
    import subprocess

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "verify_multimodal.py")],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)["status"] == "pending"


def test_quota_and_review_checks_precede_any_model_or_live_probe(observed):
    module, request, now, authority, probe = observed
    probe.quota_denied = True
    value = asyncio.run(
        module.qualify(request, authorization=authority, probe=probe, now=lambda: now)
    ).to_dict()
    assert value["status"] == "failed"
    names = [name for name, deadline in probe.calls]
    assert "available_quota" in names
    assert not {"reasoning_features", "text_without_live", "client_delegation"} & set(names)
    assert module.CHECKS.index("privacy_retention") < module.CHECKS.index("reasoning_features")
    assert module.CHECKS.index("region_lifecycle") < module.CHECKS.index("client_delegation")


def test_each_probe_receives_only_remaining_approved_cost_scope(observed):
    module, request, now, authority, probe = observed
    value = asyncio.run(
        module.qualify(request, authorization=authority, probe=probe, now=lambda: now)
    ).to_dict()
    assert value["status"] == "observed"
    assert probe.budgets[0] == request.max_cost_usd
    assert all(0 < budget <= request.max_cost_usd for budget in probe.budgets)
    assert all(
        later < earlier
        for earlier, later in zip(probe.budgets[:-1], probe.budgets[1:], strict=True)
    )


@pytest.mark.parametrize("fault", ["future", "old", "no-evidence", "unknown-cost", "unconfirmed"])
def test_stale_or_unverifiable_observation_is_not_qualification(observed, fault):
    module, request, now, authority, original = observed

    class Probe:
        async def observe(self, check, request, *, deadline, max_cost_usd):
            observed_at = (
                now + timedelta(seconds=1)
                if fault == "future"
                else now - timedelta(seconds=61)
                if fault == "old"
                else now
            )
            return module.Observation(
                check,
                request.fingerprint,
                observed_at,
                "passed",
                confirmed=fault != "unconfirmed",
                cost_usd=None if fault == "unknown-cost" else 0.01,
                evidence_digest=None if fault == "no-evidence" else "a" * 64,
            )

    report = asyncio.run(
        module.qualify(request, authorization=authority, probe=Probe(), now=lambda: now)
    ).to_dict()
    assert report["status"] == report["live_qualification"] == "pending"
    assert report["checks"][module.CHECKS[0]]["outcome"] == "unavailable"


def test_expiring_authorization_bounds_stalled_probe_without_resetting_execution_budget(observed):
    module, request, now, authority, original = observed
    authority.expires_at = now + timedelta(seconds=0.03)

    class Probe:
        calls = []

        async def observe(self, check, request, *, deadline, max_cost_usd):
            self.calls.append(deadline)
            await asyncio.Event().wait()

    probe = Probe()

    async def scenario():
        started = asyncio.get_running_loop().time()
        report = await module.qualify(
            request, authorization=authority, probe=probe, now=lambda: now
        )
        assert asyncio.get_running_loop().time() - started < 0.5
        return report.to_dict()

    report = asyncio.run(scenario())
    assert report["status"] == report["live_qualification"] == "pending"
    assert report["checks"][module.CHECKS[0]]["reason"] == "deadline"
    assert len(probe.calls) == 1 and len(set(authority.calls + probe.calls)) == 1
