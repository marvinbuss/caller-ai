"""Regression tests for portable, offline repository checks."""

import secrets
import shlex
import string
import subprocess
import sys
import tomllib
from pathlib import Path

import httpx
import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def approved_mirror_gap(monkeypatch, tmp_path):
    from datetime import UTC, datetime

    monkeypatch.syspath_prepend(str(SCRIPTS))
    import audit_dependencies

    instant = datetime(2026, 11, 3, 12, tzinfo=UTC)

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is None else instant.astimezone(tz)

    monkeypatch.setattr(audit_dependencies, "datetime", FrozenClock)
    policy = {
        "schema_version": 1,
        "exceptions": [
            {
                "ecosystem": "npm",
                "package": "minimatch",
                "version": "10.2.6",
                "advisory": "GHSA-g3hv-75wg-m249",
                "fixed_version": "11.0.1",
                "owner": "project-owner",
                "reason": "Approved mirror does not yet carry the fixed release.",
                "verified_at": "2026-11-03T00:00:00Z",
                "expires_at": "2026-11-10T00:00:00Z",
            }
        ],
    }
    path = tmp_path / "mirror-gaps.yaml"
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    return audit_dependencies, path, policy, instant


def test_approved_mirror_gap_preserves_finding_and_reports_exception(approved_mirror_gap):
    audits, path, _, now = approved_mirror_gap
    gaps = audits.load_mirror_gaps(path, now=now)
    lock = path.parent / "bun.lock"
    lock.write_text('{"packages":{"minimatch":["minimatch@10.2.6","",{},""]}}')
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=(
                    {"results": [{"vulns": [{"id": "GHSA-g3hv-75wg-m249"}]}]}
                    if request.method == "POST"
                    else {
                        "id": "GHSA-g3hv-75wg-m249",
                        "database_specific": {"severity": "MODERATE"},
                    }
                ),
            )
        )
    ) as client:
        result = audits.audit_frontend(lock, client, gaps=gaps)
    assert result == {"packages": 1, "vulnerabilities": 1, "approved_mirror_gaps": 1}


@pytest.mark.parametrize("field,value", [("version", "10.2.7"), ("advisory", "CVE-2026-1")])
def test_approved_mirror_gap_does_not_cover_other_findings(approved_mirror_gap, field, value):
    audits, path, policy, now = approved_mirror_gap
    policy["exceptions"][0][field] = value
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    gaps = audits.load_mirror_gaps(path, now=now)
    lock = path.parent / "bun.lock"
    lock.write_text('{"packages":{"minimatch":["minimatch@10.2.6","",{},""]}}')
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"results": [{"vulns": [{"id": "GHSA-g3hv-75wg-m249"}]}]}
            )
        )
    ) as client:
        with pytest.raises(RuntimeError, match="frontend_vulnerabilities_detected"):
            audits.audit_frontend(lock, client, gaps=gaps)


@pytest.mark.parametrize(
    "field,value",
    [
        ("expires_at", "2026-11-03T12:00:00Z"),
        ("expires_at", "2026-12-03T00:00:00Z"),
        ("verified_at", "2026-11-04T00:00:00Z"),
        ("owner", ""),
        ("package", "*"),
        ("unexpected", True),
    ],
)
def test_approved_mirror_gap_rejects_invalid_or_expired_policy(approved_mirror_gap, field, value):
    audits, path, policy, now = approved_mirror_gap
    policy["exceptions"][0][field] = value
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    with pytest.raises(RuntimeError, match="mirror_gap_policy_invalid"):
        audits.load_mirror_gaps(path, now=now)


@pytest.mark.parametrize("status,available", [(200, True), (404, False)])
def test_approved_mirror_gap_revalidation_detects_fixed_artifact(
    approved_mirror_gap, status, available
):
    audits, path, _, now = approved_mirror_gap
    gaps = audits.load_mirror_gaps(path, now=now)
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(status, json={"versions": {"11.0.1": {}}} if available else {})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = audits.revalidate_mirror_gaps(
            gaps, client, {"NPM_CONFIG_REGISTRY": "https://mirror.example.invalid/npm/"}
        )
    assert result == [
        {"package": "minimatch", "fixed_version": "11.0.1", "gap_confirmed": not available}
    ]
    assert len(seen) == 1
    assert seen[0].url.host == "mirror.example.invalid"


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL", "unknown"])
def test_approved_mirror_gap_does_not_allow_high_or_unknown_severity(approved_mirror_gap, severity):
    audits, path, _, now = approved_mirror_gap
    gaps = audits.load_mirror_gaps(path, now=now)
    lock = path.parent / "bun.lock"
    lock.write_text('{"packages":{"minimatch":["minimatch@10.2.6","",{},""]}}')

    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"results": [{"vulns": [{"id": gaps[0].advisory}]}]})
        return httpx.Response(
            200, json={"id": gaps[0].advisory, "database_specific": {"severity": severity}}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RuntimeError, match="mirror_gap_advisory_not_eligible"):
            audits.audit_frontend(lock, client, gaps=gaps)


@pytest.mark.parametrize(
    "content",
    [
        "schema_version: 1\nschema_version: 1\nexceptions: []\n",
        "schema_version: 1\nexceptions: &items [*items]\n",
        "schema_version: true\nexceptions: []\n",
    ],
)
def test_approved_mirror_gap_rejects_duplicate_keys_and_aliases(approved_mirror_gap, content):
    audits, path, _, now = approved_mirror_gap
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeError, match="mirror_gap_policy_invalid"):
        audits.load_mirror_gaps(path, now=now)


@pytest.mark.parametrize("status", [302, 401, 403, 429, 503])
def test_approved_mirror_gap_lookup_errors_are_not_absence(approved_mirror_gap, status):
    audits, path, _, now = approved_mirror_gap
    gaps = audits.load_mirror_gaps(path, now=now)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status))
    ) as client:
        with pytest.raises(RuntimeError, match="mirror_gap_revalidation_failed"):
            audits.revalidate_mirror_gaps(
                gaps, client, {"NPM_CONFIG_REGISTRY": "https://mirror.example.invalid/npm/"}
            )


def test_approved_mirror_gap_python_findings_remain_visible(approved_mirror_gap, monkeypatch):
    import json
    from types import SimpleNamespace

    audits, path, policy, now = approved_mirror_gap
    policy["exceptions"][0].update(
        ecosystem="PyPI", package="fixture", version="1.0", fixed_version="1.1"
    )
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    gaps = audits.load_mirror_gaps(path, now=now)
    monkeypatch.setattr(audits, "run", lambda *arguments: None)

    def execute(arguments, **keywords):
        report = Path(arguments[arguments.index("--output") + 1])
        report.write_text(
            json.dumps(
                {
                    "dependencies": [
                        {
                            "name": "fixture",
                            "version": "1.0",
                            "vulns": [{"id": gaps[0].advisory, "fix_versions": ["1.1"]}],
                        }
                    ]
                }
            )
        )
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(audits.subprocess, "run", execute)
    original = httpx.Client
    monkeypatch.setattr(
        audits.httpx,
        "Client",
        lambda **keywords: original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"id": gaps[0].advisory, "database_specific": {"severity": "MODERATE"}},
                )
            )
        ),
    )
    result = audits.audit_python("tools", path.parent, gaps=gaps)
    assert result == {
        "project": "tools",
        "packages": 1,
        "vulnerabilities": 1,
        "approved_mirror_gaps": 1,
    }


def test_ci_registry_defaults_are_public_and_local_overrides_are_not_hardcoded(
    monkeypatch, tmp_path
):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import ci
    from ci import environment

    monkeypatch.setattr(ci, "ROOT", tmp_path)
    for name in ("UV_DEFAULT_INDEX", "NPM_CONFIG_REGISTRY", "BUN_CONFIG_DEFAULT_REGISTRY"):
        monkeypatch.delenv(name, raising=False)
    values = environment()
    assert values["UV_DEFAULT_INDEX"] == "https://pypi.org/simple"
    assert values["NPM_CONFIG_REGISTRY"] == "https://registry.npmjs.org/"
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://mirror.example.invalid/simple")
    monkeypatch.setenv("NPM_CONFIG_REGISTRY", "https://mirror.example.invalid/npm/")
    values = environment()
    assert values["UV_DEFAULT_INDEX"] == "https://mirror.example.invalid/simple"
    assert values["NPM_CONFIG_REGISTRY"] == "https://mirror.example.invalid/npm/"
    assert values["BUN_CONFIG_DEFAULT_REGISTRY"] == values["NPM_CONFIG_REGISTRY"]
    monkeypatch.setenv("BUN_CONFIG_DEFAULT_REGISTRY", "https://explicit.example.invalid/bun/")
    assert environment()["BUN_CONFIG_DEFAULT_REGISTRY"] == "https://explicit.example.invalid/bun/"


@pytest.mark.parametrize("continuous_integration", [False, True])
def test_local_feed_settings_load_automatically_except_in_ci(
    monkeypatch, tmp_path, continuous_integration
):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import ci

    monkeypatch.setattr(ci, "ROOT", tmp_path)
    for key in (
        "UV_DEFAULT_INDEX",
        "NPM_CONFIG_REGISTRY",
        "BUN_CONFIG_DEFAULT_REGISTRY",
        "CI",
        "GITHUB_ACTIONS",
    ):
        monkeypatch.delenv(key, raising=False)
    if continuous_integration:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    settings = tmp_path / "code/.local/feeds.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '[environment]\nUV_DEFAULT_INDEX = "https://mirror.example.invalid/simple"\n'
        'NPM_CONFIG_REGISTRY = "https://mirror.example.invalid/npm/"\n'
    )
    values = ci.environment()
    assert values["UV_DEFAULT_INDEX"] == (
        "https://pypi.org/simple"
        if continuous_integration
        else "https://mirror.example.invalid/simple"
    )
    assert values["NPM_CONFIG_REGISTRY"] == (
        "https://registry.npmjs.org/"
        if continuous_integration
        else "https://mirror.example.invalid/npm/"
    )
    assert values["BUN_CONFIG_DEFAULT_REGISTRY"] == values["NPM_CONFIG_REGISTRY"]
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://explicit.example.invalid/simple")
    assert ci.environment()["UV_DEFAULT_INDEX"] == "https://explicit.example.invalid/simple"


def test_malformed_local_feed_settings_fail_without_value_disclosure(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import ci

    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    settings = tmp_path / "code/.local/feeds.toml"
    settings.parent.mkdir(parents=True)
    candidate = "not-a-url-fixture"
    settings.write_text('[environment]\nUV_DEFAULT_INDEX = "' + candidate + '"\n')
    with pytest.raises(ValueError, match="local_feed_settings_invalid") as rejected:
        ci.environment()
    assert candidate not in str(rejected.value)


def test_local_prepare_uses_hashed_mirror_install_without_rewriting_locks(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import ci

    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setattr(
        ci,
        "local_feed_settings",
        lambda values: {"UV_DEFAULT_INDEX": "https://mirror.example.invalid/simple"},
    )
    commands = []
    monkeypatch.setattr(ci, "run", lambda *arguments: commands.append(arguments))
    for name in ("backend", "management", "tools"):
        project = tmp_path / "code" / name
        project.mkdir(parents=True)
        (project / ".python-version").write_text("3.12.15\n")
        (project / "uv.lock").write_text("unchanged-lock\n")
    ci.prepare()
    assert not any(command[:2] == ("uv", "sync") for command in commands)
    exports = [command for command in commands if command[:2] == ("uv", "export")]
    installs = [command for command in commands if command[:3] == ("uv", "pip", "sync")]
    assert len(exports) == len(installs) == 3
    assert all("--locked" in command and "--no-emit-project" in command for command in exports)
    assert all("--require-hashes" in command and "--python" in command for command in installs)
    assert all(
        (tmp_path / "code" / name / "uv.lock").read_text() == "unchanged-lock\n"
        for name in ("backend", "management", "tools")
    )
    assert len([command for command in commands if "--editable" in command]) == 2


@pytest.fixture
def publication(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import check_publication

    return check_publication


def test_publication_rejects_private_feed_without_echoing_url(publication, tmp_path, capsys):
    candidate = "https://" + "feedproxy" + ".example.invalid/npm/"
    source = tmp_path / "settings.txt"
    source.write_text(candidate)
    assert publication.check_paths([source], tmp_path) == 1
    output = capsys.readouterr()
    assert candidate not in output.out + output.err
    assert "settings.txt" in output.err


@pytest.mark.parametrize("indexed_private", [False, True])
def test_publication_move_ignores_missing_worktree_paths_but_keeps_index_gate(
    publication, monkeypatch, tmp_path, indexed_private
):
    from types import SimpleNamespace

    document = tmp_path / "docs/setup.md"
    document.parent.mkdir()
    document.write_text("# Setup\n")
    seen = []
    monkeypatch.setattr(publication, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_publication.py", "check"])
    monkeypatch.setattr(
        publication.subprocess,
        "run",
        lambda *arguments, **keywords: SimpleNamespace(
            stdout=b"code/docs/setup.md\0docs/setup.md\0"
        ),
    )
    monkeypatch.setattr(
        publication,
        "check_git",
        lambda root: seen.append("index-and-history") or int(indexed_private),
    )
    original = publication.check_paths

    def paths(values, root):
        assert values == [document]
        return original(values, root)

    monkeypatch.setattr(publication, "check_paths", paths)
    assert publication.main() == int(indexed_private)
    assert seen == ["index-and-history"]


def test_publication_accepts_public_package_origins(publication, tmp_path):
    source = tmp_path / "settings.txt"
    source.write_text("https://pypi.org/simple https://registry.npmjs.org/")
    assert publication.check_paths([source], tmp_path) == 0


def test_publication_accepts_literal_url_validation_regex(publication, tmp_path):
    source = tmp_path / "policy.txt"
    source.write_text('regex("^https://[A-Za-z0-9.-]+/allowed$", value)')
    assert publication.check_paths([source], tmp_path) == 0


@pytest.mark.parametrize("surface", ["clean", "index", "history"])
def test_publication_checks_index_and_history_without_url_output(
    publication, monkeypatch, tmp_path, capsys, surface
):
    from types import SimpleNamespace

    def execute(command, **options):
        history = command[1] == "log"
        matched = surface == ("history" if history else "index")
        return SimpleNamespace(
            returncode=0 if matched or history else 1,
            stdout=b"object-reference\n" if matched else b"",
        )

    monkeypatch.setattr(publication.subprocess, "run", execute)
    assert publication.check_git(tmp_path) == (0 if surface == "clean" else 1)
    output = capsys.readouterr()
    assert "https://" not in output.out + output.err


def test_frontend_lock_relocation_preserves_version_and_integrity(publication):
    import base64
    import json

    integrity = "sha512-" + base64.b64encode(b"x" * 64).decode()
    original = {
        "packages": {
            "fixture": [
                "fixture@1.0.0",
                "https://mirror.example.invalid/fixture-1.0.0.tgz",
                {},
                integrity,
            ]
        }
    }
    result = json.loads(publication.relocate_frontend(json.dumps(original)))
    assert result["packages"]["fixture"] == ["fixture@1.0.0", "", {}, integrity]


def test_frontend_cleanup_command_needs_no_metadata(publication, monkeypatch, tmp_path):
    import base64
    import json

    monkeypatch.setattr(publication, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_publication.py", "frontend"])
    lock = tmp_path / "code/management/reflex.lock/bun.lock"
    lock.parent.mkdir(parents=True)
    integrity = "sha512-" + base64.b64encode(b"x" * 64).decode()
    lock.write_text(
        json.dumps(
            {
                "packages": {
                    "fixture": [
                        "fixture@1.0.0",
                        "https://mirror.example.invalid/fixture-1.0.0.tgz",
                        {},
                        integrity,
                    ]
                }
            }
        )
    )
    assert publication.main() == 0
    assert json.loads(lock.read_text())["packages"]["fixture"] == [
        "fixture@1.0.0",
        "",
        {},
        integrity,
    ]


@pytest.mark.parametrize("failed", [False, True])
def test_frontend_commands_always_restore_public_lock(monkeypatch, tmp_path, failed):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from types import SimpleNamespace

    import ci

    monkeypatch.setattr(ci, "ROOT", tmp_path)
    lock = tmp_path / "code/management/reflex.lock/bun.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("{}")
    commands = []

    def execute(command, **options):
        commands.append(command)
        if len(commands) == 1 and failed:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ci.subprocess, "run", execute)
    if failed:
        with pytest.raises(subprocess.CalledProcessError):
            ci.run("uv", "run", "reflex", "export")
    else:
        ci.run("uv", "run", "reflex", "export")
    assert len(commands) == 2
    assert commands[1][-1] == "frontend"
    assert "--no-sync" in commands[1]


@pytest.mark.parametrize("digest", ["a" * 64, "b" * 64])
def test_python_lock_relocation_requires_matching_public_hash(publication, digest):
    original = (
        'version = 1\n[[package]]\nname = "fixture"\nversion = "1.0"\n'
        'source = { registry = "https://mirror.example.invalid/simple" }\n'
        'wheels = [{ url = "https://mirror.example.invalid/fixture.whl", '
        'hash = "sha256:' + "a" * 64 + '", size = 10 }]\n'
    )
    metadata = {
        "fixture==1.0": {
            "urls": [
                {
                    "filename": "fixture.whl",
                    "url": "https://files.pythonhosted.org/fixture.whl",
                    "digests": {"sha256": digest},
                    "size": 10,
                }
            ]
        }
    }
    if digest != "a" * 64:
        with pytest.raises(ValueError, match="public_artifact_mismatch"):
            publication.relocate_python(original, metadata)
    else:
        result = tomllib.loads(publication.relocate_python(original, metadata))
        package = result["package"][0]
        assert package["version"] == "1.0"
        assert package["source"]["registry"] == "https://pypi.org/simple"
        assert package["wheels"][0]["hash"] == "sha256:" + "a" * 64
        assert package["wheels"][0]["url"] == "https://files.pythonhosted.org/fixture.whl"


def invoke(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_whitespace_failure_and_explicit_recovery(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_bytes(b"name = 1  \r\nother = 2")
    assert invoke("check_whitespace.py", str(source)).returncode == 1
    assert source.read_bytes() == b"name = 1  \r\nother = 2"
    assert invoke("check_whitespace.py", "--fix", str(source)).returncode == 0
    assert source.read_bytes() == b"name = 1\r\nother = 2\r\n"
    assert invoke("check_whitespace.py", str(source)).returncode == 0


def test_empty_text_is_valid(tmp_path: Path) -> None:
    source = tmp_path / "empty.txt"
    source.touch()
    assert invoke("check_whitespace.py", str(source)).returncode == 0


def test_management_build_stage_requires_patched_curl_and_libcurl() -> None:
    source = (SCRIPTS.parents[1] / "management/Dockerfile").read_text(encoding="utf-8")
    commands = [
        shlex.split(line.removeprefix("RUN "))
        for line in source.splitlines()
        if line.startswith("RUN ")
    ]
    installs = [command for command in commands if command[:3] == ["apk", "add", "--no-cache"]]
    assert len(installs) == 1
    assert {"curl>=8.23.0-r0", "libcurl>=8.23.0-r0"} <= set(installs[0][3:])


@pytest.mark.parametrize(
    "package,vulnerable,fixed",
    [
        ("virtualenv", "21.14.7", "21.14.9"),
        ("filelock", "4.2.0", "4.2.1"),
    ],
)
def test_tools_security_floors_and_locked_versions_do_not_regress(package, vulnerable, fixed):
    project = SCRIPTS.parent
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    constraints = {
        value.name: value.specifier
        for value in (
            Requirement(entry) for entry in metadata["tool"]["uv"]["constraint-dependencies"]
        )
    }
    assert Version(vulnerable) not in constraints[package]
    assert Version(fixed) in constraints[package]
    locked = tomllib.loads((project / "uv.lock").read_text(encoding="utf-8"))
    selected = [entry for entry in locked["package"] if entry["name"] == package]
    assert len(selected) == 1 and Version(selected[0]["version"]) in constraints[package]
    assert selected[0]["source"]["registry"] == "https://pypi.org/simple"


def test_secret_rejected_without_value_disclosure(tmp_path: Path) -> None:
    source = tmp_path / "fixture.py"
    candidate = "AKIA" + "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16)
    )
    source.write_text(f"aws_access_key_id = {candidate!r}\n", encoding="utf-8")
    result = invoke("check_secrets.py", str(source))
    assert result.returncode == 1
    assert candidate not in result.stdout + result.stderr
    assert str(source) in result.stderr


def test_safe_file_passes_and_missing_file_fails(tmp_path: Path) -> None:
    source = tmp_path / "safe.py"
    source.write_text("safe_name = 1\n", encoding="utf-8")
    assert invoke("check_secrets.py", str(source)).returncode == 0
    assert invoke("check_secrets.py", str(tmp_path / "missing.py")).returncode == 1


def test_workflow_has_unprivileged_native_jobs() -> None:
    import re

    root = SCRIPTS.parents[2]
    source = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == ["main"]
    native = workflow["jobs"]["native"]
    assert set(native["strategy"]["matrix"]["os"]) == {"windows-2022", "ubuntu-24.04"}
    for step in native["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[a-zA-Z0-9_/-]+@[0-9a-f]{40}", step["uses"])
        if "run" in step:
            assert step["run"].startswith(
                "uv run --project code/tools --locked python code/tools/scripts/"
            )
    assert native["steps"][0]["with"]["persist-credentials"] == "false"
    native_commands = [step["run"] for step in native["steps"] if "run" in step]
    assert (
        "uv run --project code/tools --locked python code/tools/scripts/ci.py frontend"
        in native_commands
    )
    assert (
        "uv run --project code/tools --locked python code/tools/scripts/ci.py test"
        in native_commands
    )
    images = workflow["jobs"]["images"]
    assert images["runs-on"] == "ubuntu-24.04"
    assert any(
        step.get("run")
        == "uv run --project code/tools --locked python code/tools/scripts/test_images.py"
        for step in images["steps"]
    )
    for job in workflow["jobs"].values():
        node = next(
            step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-node@")
        )
        assert node["with"]["node-version"] == "24.22.0"
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[a-zA-Z0-9_/-]+@[0-9a-f]{40}", step["uses"])
            if "run" in step:
                assert step["run"].startswith(
                    "uv run --project code/tools --locked python code/tools/scripts/"
                )
            assert not step.get("uses", "").startswith("actions/setup-python@")
    assert "secrets." not in source
    assert "id-token" not in source


def test_default_ci_no_longer_uploads_temporary_compiler_diagnostic() -> None:
    root = SCRIPTS.parents[2]
    workflow = yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["native"]["steps"]
    assert not any(step.get("uses", "").startswith("actions/upload-artifact@") for step in steps)
    assert not any(step.get("id") == "compiler_diagnostic" for step in steps)
    assert not any(step.get("run", "").endswith("ci.py diagnostics") for step in steps)


@pytest.mark.parametrize("scanner_fails", [False, True])
def test_compiler_artifact_requires_scanners_and_contains_only_compiler_output(
    monkeypatch, tmp_path, scanner_fails
):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import ci

    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "Windows")
    source = tmp_path / "code/management/test-results/editor-host.log"
    source.parent.mkdir(parents=True)
    source.write_text(
        "Creating Production Build failed with exit code 1\nsynthetic compiler diagnostic\n"
    )
    calls = []

    def scan(*arguments):
        calls.append(arguments)
        if scanner_fails:
            raise subprocess.CalledProcessError(1, arguments)

    monkeypatch.setattr(ci, "run", scan)
    target = tmp_path / "code/tools/test-results/diagnostics/editor-build.log"
    if scanner_fails:
        with pytest.raises(subprocess.CalledProcessError):
            ci.collect_editor_diagnostic()
        assert not target.exists()
    else:
        ci.collect_editor_diagnostic()
        assert target.read_text() == source.read_text()
    assert len(calls) == 1 and "check_secrets.py" in str(calls[0])


@pytest.mark.parametrize("rejection", ["profile", "oversized", "runtime_log", "private_origin"])
def test_compiler_artifact_rejects_unapproved_inputs(monkeypatch, tmp_path, rejection):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import ci

    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "Windows")
    source = tmp_path / "code/management/test-results/editor-host.log"
    source.parent.mkdir(parents=True)
    content = "Creating Production Build failed with exit code 1\n"
    if rejection == "profile":
        monkeypatch.delenv("GITHUB_ACTIONS")
    elif rejection == "oversized":
        content += "x" * 262145
    elif rejection == "runtime_log":
        content = "Application runtime output is not approved\n"
    else:
        content += "https://" + "feedproxy" + ".example.invalid/npm/"
    source.write_text(content)
    with pytest.raises(RuntimeError, match="compiler_diagnostic_"):
        ci.collect_editor_diagnostic()
    assert not (tmp_path / "code/tools/test-results/diagnostics/editor-build.log").exists()


def test_whitespace_exception_is_limited_to_framework_manifest() -> None:
    import re

    configuration = yaml.safe_load((SCRIPTS.parents[1] / ".pre-commit-config.yaml").read_text())
    hooks = {hook["id"]: hook for hook in configuration["repos"][0]["hooks"]}
    exception = hooks["whitespace"]["exclude"]
    assert re.fullmatch(exception, "code/management/reflex.lock/package.json")
    assert not re.fullmatch(exception, "code/management/management_app/health.py")
    assert not re.fullmatch(exception, "code/management/reflex.lock/bun.lock")
    assert "exclude" not in hooks["secrets"]


@pytest.fixture
def audits(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    monkeypatch.syspath_prepend(str(SCRIPTS))
    specification = importlib.util.spec_from_file_location(
        "caller_audits",
        SCRIPTS / "audit_dependencies.py",
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_frontend_audit_uses_resolved_override(audits, tmp_path: Path) -> None:
    import json

    lock = tmp_path / "bun.lock"
    lock.write_text(
        '{"workspaces":{"":{"devDependencies":{"vite":"8.3.2"}}},'
        '"packages":{"vite":["vite@8.3.3","https://registry.npmjs.org/vite/-/vite-8.3.3.tgz",{},"sha512-fixture"],},}'
    )

    def response(request: httpx.Request) -> httpx.Response:
        queries = json.loads(request.content)["queries"]
        assert queries == [{"package": {"name": "vite", "ecosystem": "npm"}, "version": "8.3.3"}]
        return httpx.Response(200, json={"results": [{}]})

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        assert audits.audit_frontend(lock, client)["packages"] == 1


@pytest.mark.parametrize(
    "status,payload,error",
    [
        (503, {}, httpx.HTTPStatusError),
        (200, {"results": []}, RuntimeError),
        (200, {"results": [{"vulns": [{"id": "GHSA-test-fixture"}]}]}, RuntimeError),
    ],
)
def test_advisory_failures_and_findings_are_not_clean(
    audits, tmp_path: Path, status, payload, error
) -> None:
    lock = tmp_path / "bun.lock"
    lock.write_text('{"packages":{"vite":["vite@8.3.3","https://example.invalid",{},"hash"]}}')
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json=payload),
        )
    ) as client:
        with pytest.raises(error):
            audits.audit_frontend(lock, client)


def test_python_audit_failure_reports_safe_identifiers(
    audits, tmp_path: Path, monkeypatch, capsys
) -> None:
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(audits, "run", lambda *arguments: None)

    def execute(arguments, **keywords):
        report = Path(arguments[arguments.index("--output") + 1])
        report.write_text(
            json.dumps(
                {
                    "dependencies": [
                        {
                            "name": "vulnerable-fixture",
                            "version": "1.0",
                            "vulns": [
                                {
                                    "id": "GHSA-fixture",
                                    "fix_versions": ["1.1"],
                                    "description": "must-not-be-reported",
                                }
                            ],
                        }
                    ]
                }
            )
        )
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(audits.subprocess, "run", execute)
    with pytest.raises(RuntimeError):
        audits.audit_python("tools", tmp_path)
    output = capsys.readouterr().out
    assert "vulnerable-fixture" in output and "GHSA-fixture" in output
    assert "must-not-be-reported" not in output


def test_failure_gate_detects_and_restores_real_health_assertion() -> None:
    import json

    source = SCRIPTS.parents[1] / "backend/tests/contract/test_health.py"
    before = source.read_bytes()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "verify_failure_gate.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["baseline_exit"] == 0
    assert report["broken_exit"] == 1
    assert report["restored_exit"] == 0
    assert report["fixture_cleaned"] is True
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    "text,valid",
    [
        ("# Valid\n\n[local](target.md)\n```mermaid\nflowchart LR\n A --> B\n```\n", True),
        ("# Invalid\n\n[missing](absent.md)\n", False),
        ("No document title\n", False),
    ],
)
def test_documentation_structure_and_links(tmp_path: Path, monkeypatch, text, valid) -> None:
    import importlib.util

    monkeypatch.syspath_prepend(str(SCRIPTS))
    specification = importlib.util.spec_from_file_location(
        "caller_docs", SCRIPTS / "verify_docs.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    document = tmp_path / "document.md"
    document.write_text(text)
    (tmp_path / "target.md").write_text("# Target\n")
    if valid:
        diagrams = module.validate_documents([document], tmp_path)
        assert len(diagrams) == 1 and "A --> B" in diagrams[0][1]
    else:
        with pytest.raises(ValueError):
            module.validate_documents([document], tmp_path)


@pytest.mark.parametrize(
    "broken", [None, "link", "image", "asset", "fragment", "local-fragment", "escape"]
)
def test_root_document_links_assets_and_fragments_are_verified(tmp_path, monkeypatch, broken):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import verify_docs

    docs = tmp_path / "docs"
    docs.mkdir()
    target = docs / "target file.md"
    target.write_text("# Target\n\n## Exact Heading `Name`\n\n## Repeated\n\n## Repeated\n")
    (docs / "asset.png").write_bytes(b"synthetic-image")
    (docs / "data.json").write_text("{}")
    reference = {
        None: "[target](target%20file.md#exact-heading-name) ![image](asset.png) "
        "[asset](data.json) [duplicate](target%20file.md#repeated-1) [self](#local-heading)",
        "link": "[missing](missing.md)",
        "image": "![missing](missing.png)",
        "asset": "[missing](missing.json)",
        "fragment": "[missing](target%20file.md#absent)",
        "local-fragment": "[missing](#absent)",
        "escape": "![escape](../../outside.png)",
    }[broken]
    document = docs / "index.md"
    document.write_text(
        "# Root Docs\n\n## Local Heading\n\n"
        + reference
        + "\nHistorical literal code/docs/acceptance/result.md is not a navigable link.\n"
    )
    if broken is None:
        assert verify_docs.validate_documents([document], tmp_path) == []
        assert "Historical literal code/docs" in document.read_text()
    else:
        with pytest.raises(ValueError):
            verify_docs.validate_documents([document], tmp_path)


def test_ci_file_scan_covers_root_docs_navigation_and_feature_artifacts(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(SCRIPTS))
    import ci

    commands, scans = [], []
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    for name in ("docs/setup.md", "README.md", "specs/002-feature/spec.md"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Synthetic\n")
    monkeypatch.setattr(ci, "run", lambda *arguments: commands.append(arguments))
    monkeypatch.setattr(ci, "environment", lambda: {})

    def scan(command, **options):
        scans.append(command)
        return SimpleNamespace(stdout="docs/setup.md\nREADME.md\nspecs/002-feature/spec.md\n")

    monkeypatch.setattr(ci.subprocess, "run", scan)
    ci.check()
    assert len(scans) == 1 and {"code", "docs", "README.md", "specs", ".github/workflows"} <= set(
        scans[0]
    )
    checked = next(command for command in commands if "pre-commit" in command)
    assert {"docs/setup.md", "README.md", "specs/002-feature/spec.md"} <= set(checked)


def test_native_ci_collects_complete_apps_docs_and_no_live_endurance(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import ci

    commands = []
    monkeypatch.setattr(ci, "run", lambda *arguments: commands.append(arguments))
    monkeypatch.setattr(sys, "argv", ["ci.py", "test"])
    ci.main()
    assert len(commands) == 2
    assert {command[command.index("--directory") + 1] for command in commands} == {
        "code/backend",
        "code/management",
    }
    assert all("pytest" in command and "--locked" in command for command in commands)
    assert all(
        command[command.index("--locked") + 1 : command.index("--locked") + 4]
        == ("python", "-m", "pytest")
        for command in commands
    )
    assert all(
        not {"-k", "--ignore", "--live-endurance", "--execute"} & set(command)
        for command in commands
    )
    workflow = yaml.load(
        (SCRIPTS.parents[2] / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    native = workflow["jobs"]["native"]
    assert set(native["strategy"]["matrix"]["os"]) == {"windows-2022", "ubuntu-24.04"}
    executed = [step["run"] for step in native["steps"] if "run" in step]
    assert any("verify_docs.py" in command for command in executed)
    assert any("audit_dependencies.py" in command for command in executed)
    assert workflow["permissions"] == {"contents": "read"}
    assert not any("paths" in value for value in workflow["on"].values() if isinstance(value, dict))


def test_root_document_discovery_covers_contracts_specs_and_rejects_old_tree(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import verify_docs

    expected = [
        "docs/setup.md",
        "docs/runbooks/check.md",
        "code/contracts/README.md",
        "specs/002-feature/spec.md",
        "README.md",
    ]
    for name in expected:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Synthetic\n")
    assert {
        str(path.relative_to(tmp_path)).replace("\\", "/")
        for path in verify_docs.discover_documents(tmp_path)
    } == set(expected)
    old = tmp_path / "code/docs/setup.md"
    old.parent.mkdir(parents=True)
    old.write_text("# Duplicate Maintained Docs\n")
    with pytest.raises(ValueError, match="duplicate_maintained_documentation"):
        verify_docs.discover_documents(tmp_path)


@pytest.mark.parametrize(
    "diagnostic,category",
    [
        ("No usable sandbox!", "browser_sandbox_unavailable"),
        ("libnss3.so: cannot open shared object file", "browser_dependency_unavailable"),
        ("npm ERR! code E404", "package_fetch_failed"),
        ("Parse error on line 3", "diagram_syntax_invalid"),
        ("Prerender:" + chr(27) + "[0m Request failed for /", "frontend_prerender_failed"),
        ("UnicodeDecodeError: synthetic process output", "text_encoding_failed"),
        ("TypeError: synthetic Python build failure", "build_type_error"),
        ("FileNotFoundError: synthetic build path", "build_file_missing"),
        ("unexpected external diagnostic", "unclassified"),
    ],
)
def test_diagram_renderer_reports_only_safe_failure_category(
    tmp_path: Path, monkeypatch, diagnostic, category
):
    import importlib.util
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(SCRIPTS))
    specification = importlib.util.spec_from_file_location(
        "caller_docs", SCRIPTS / "verify_docs.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    child = SimpleNamespace(wait=lambda **options: 1)
    stopped = []
    candidate = "sensitive-fixture-must-not-leak"

    def spawn(*arguments, **options):
        options["stdout"].write(diagnostic + "\n" + candidate)
        return child

    monkeypatch.setattr(module.psutil, "Popen", spawn)
    monkeypatch.setattr(module, "stop", lambda processes: stopped.extend(processes))
    with pytest.raises(RuntimeError, match="diagram_renderer_failed:" + category) as rejected:
        module.run_renderer(["renderer"], {}, tmp_path / "renderer.log")
    assert candidate not in str(rejected.value)
    assert diagnostic not in str(rejected.value)
    assert stopped == [child]


@pytest.mark.parametrize(
    "github,platform,available,reported,error",
    [
        (False, "linux", False, "unused", None),
        (False, "win32", False, "unused", None),
        (True, "win32", True, "143.0.7499.10\n", None),
        (True, "win32", False, "unused", "ci_sandboxed_browser_unavailable"),
        (True, "win32", True, "unexpected-version-output", "ci_sandboxed_browser_version_invalid"),
        (True, "linux", True, "Google Chrome 143.0.7499.10\n", None),
        (True, "linux", False, "unused", "ci_sandboxed_browser_unavailable"),
        (True, "linux", True, "unexpected-version-output", "ci_sandboxed_browser_version_invalid"),
    ],
)
def test_renderer_selects_sandboxed_ci_chrome_and_records_version(
    monkeypatch, github, platform, available, reported, error
):
    import importlib.util
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(SCRIPTS))
    specification = importlib.util.spec_from_file_location(
        "caller_docs", SCRIPTS / "verify_docs.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    monkeypatch.setattr(module.sys, "platform", platform)
    monkeypatch.setattr(module, "Path", lambda path: SimpleNamespace(is_file=lambda: available))
    commands = []

    def execute(command, **options):
        commands.append(command)
        assert options["timeout"] == (30 if platform == "win32" else 10)
        return SimpleNamespace(stdout=reported, returncode=0)

    monkeypatch.setattr(module.subprocess, "run", execute)
    if error:
        with pytest.raises(RuntimeError, match=error):
            module.renderer_browser("prepared-chromium", {"GITHUB_ACTIONS": "true"})
        assert len(commands) == int(available)
        return
    selected, version = module.renderer_browser(
        "prepared-chromium", {"GITHUB_ACTIONS": "true" if github else "false"}
    )
    if github:
        assert version == "143.0.7499.10"
        if platform == "linux":
            assert selected == "/opt/google/chrome/chrome"
            assert commands == [[selected, "--version"]]
        else:
            assert selected == r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            assert len(commands) == 1 and commands[0][-1] == selected
            assert "chromium_sandbox=True" in commands[0][-2]
            assert "--no-sandbox" not in str(commands[0])
    else:
        assert (selected, version) == ("prepared-chromium", "prepared")
        assert commands == []


def test_build_failure_category_reads_bounded_log_tail(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from ci import build_failure_category

    log = tmp_path / "build.log"
    log.write_text("compile progress\n" * 10000 + "Prerender: Request failed for /: timeout\n")
    assert build_failure_category(log) == "frontend_prerender_failed"


def test_build_metadata_exposes_only_allowlisted_node_error(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from ci import build_failure_metadata

    log = tmp_path / "build.log"
    log.write_text("Error [ERR_UNSUPPORTED_ESM_URL_SCHEME]: sensitive-fixture-value\n")
    metadata = build_failure_metadata(log)
    assert metadata["node_error"] == "ERR_UNSUPPORTED_ESM_URL_SCHEME"
    assert "sensitive-fixture-value" not in str(metadata)


@pytest.mark.parametrize(
    "diagnostic,phase",
    [
        ("", "launcher_or_package"),
        ("Generating single mermaid chart", "mermaid_started"),
    ],
)
@pytest.mark.parametrize("timeout_kind", ["subprocess", "psutil"])
def test_diagram_renderer_timeout_cleans_owned_process(
    tmp_path: Path, monkeypatch, diagnostic, phase, timeout_kind
) -> None:
    import importlib.util

    monkeypatch.syspath_prepend(str(SCRIPTS))
    specification = importlib.util.spec_from_file_location(
        "caller_docs", SCRIPTS / "verify_docs.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    class Process:
        def wait(self, timeout):
            assert timeout == 300
            if timeout_kind == "psutil":
                raise module.psutil.TimeoutExpired(seconds=timeout, pid=123)
            raise subprocess.TimeoutExpired("renderer", timeout)

    process = Process()
    stopped = []

    def spawn(*arguments, **keywords):
        assert keywords["stdin"] == subprocess.DEVNULL
        keywords["stdout"].write(diagnostic + "\nsensitive-fixture-must-not-leak")
        return process

    monkeypatch.setattr(module.psutil, "Popen", spawn)
    monkeypatch.setattr(module, "stop", lambda processes: stopped.extend(processes))
    with pytest.raises(RuntimeError, match="diagram_renderer_timeout:" + phase) as rejected:
        module.run_renderer(["renderer"], {}, tmp_path / "renderer.log")
    assert "sensitive-fixture-must-not-leak" not in str(rejected.value)
    assert stopped == [process]


@pytest.mark.parametrize(
    "platform,node_available,script_available,error",
    [
        ("win32", True, True, None),
        ("win32", False, True, "node_npx_required_for_mermaid_validation"),
        ("win32", True, False, "node_npx_script_unavailable"),
        ("linux", True, False, None),
    ],
)
def test_renderer_launcher_bypasses_windows_batch_wrapper(
    tmp_path: Path, monkeypatch, platform, node_available, script_available, error
) -> None:
    import importlib.util

    monkeypatch.syspath_prepend(str(SCRIPTS))
    specification = importlib.util.spec_from_file_location(
        "caller_docs", SCRIPTS / "verify_docs.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    installation = tmp_path / "Node installation with spaces"
    installation.mkdir()
    wrapper = installation / "npx.cmd"
    node = installation / "node.exe"
    script = installation / "node_modules/npm/bin/npx-cli.js"
    if script_available:
        script.parent.mkdir(parents=True)
        script.touch()
    monkeypatch.setattr(module.sys, "platform", platform)
    commands = {
        "npx.cmd": str(wrapper),
        "node.exe": str(node) if node_available else None,
        "npx": "/usr/bin/npx",
    }
    monkeypatch.setattr(module.shutil, "which", commands.get)
    if error:
        with pytest.raises(RuntimeError, match=error):
            module.renderer_launcher()
    else:
        selected = module.renderer_launcher()
        assert selected == ([str(node), str(script)] if platform == "win32" else ["/usr/bin/npx"])
        assert not any(argument.endswith(".cmd") for argument in selected)


@pytest.mark.parametrize("reported", ["12.0.0", "12.0.1", ""])
def test_diagram_rendering_prepares_once_then_forbids_package_network(
    tmp_path: Path, monkeypatch, reported
) -> None:
    import importlib.util
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(SCRIPTS))
    specification = importlib.util.spec_from_file_location(
        "caller_docs", SCRIPTS / "verify_docs.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    browser = tmp_path / "prepared-browser"
    browser.touch()
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(
        module, "environment", lambda: {"NPM_CONFIG_REGISTRY": "https://registry.example.invalid/"}
    )
    monkeypatch.setattr(module, "renderer_launcher", lambda: ["test-node", "test-npx.js"])
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *arguments, **keywords: SimpleNamespace(stdout=str(browser)),
    )
    monkeypatch.setattr(module, "renderer_browser", lambda prepared, values: (prepared, "prepared"))
    monkeypatch.setattr(
        module,
        "validate_documents",
        lambda paths, root: [
            (tmp_path / "first.md", "flowchart LR\n A --> B"),
            (tmp_path / "second.md", "flowchart LR\n B --> C"),
        ],
    )
    commands = []

    def execute(arguments, values, log):
        commands.append(arguments)
        assert arguments[:2] == ["test-node", "test-npx.js"]
        if arguments[-1] == "--version":
            assert "--offline" not in arguments
            assert values["NPM_CONFIG_FETCH_TIMEOUT"] == "30000"
            assert values["NPM_CONFIG_FETCH_RETRIES"] == "0"
            log.write_text(reported + "\n", encoding="utf-8")
            return
        assert "--offline" in arguments
        rendered = Path(arguments[arguments.index("-o") + 1])
        rendered.write_text(
            '<svg viewBox="0 0 10 10"><g><path/><path/><path/></g></svg>', encoding="utf-8"
        )

    monkeypatch.setattr(module, "run_renderer", execute)
    if reported != "12.0.0":
        with pytest.raises(RuntimeError, match="mermaid_cli_version_invalid"):
            module.main()
        assert len(commands) == 1
    else:
        module.main()
        assert len(commands) == 3
        assert commands[0][-1] == "--version"
        assert all("@mermaid-js/mermaid-cli@12.0.0" in command for command in commands)


@pytest.fixture
def terraform_checks(monkeypatch):
    import importlib.util

    monkeypatch.syspath_prepend(str(SCRIPTS))
    specification = importlib.util.spec_from_file_location(
        "caller_terraform", SCRIPTS / "verify_terraform.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_terraform_verification_removes_cloud_and_argument_injection(
    terraform_checks, monkeypatch, tmp_path
):
    for name in (
        "ARM_CLIENT_SECRET",
        "AZURE_CLIENT_ID",
        "TF_VAR_subscription_id",
        "TF_CLI_ARGS",
        "TF_CLI_ARGS_test",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    ):
        monkeypatch.setenv(name, "synthetic-untrusted-inherited-value")
    values = terraform_checks.verification_environment(tmp_path)
    assert not any(
        key.startswith(("ARM_", "AZURE_", "TF_VAR_", "TF_CLI_ARGS"))
        for key in values
        if key != "AZURE_CONFIG_DIR"
    )
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in values
    assert values["AZURE_CONFIG_DIR"] == str(tmp_path / "azure")
    assert values["TF_INPUT"] == "0"


def test_terraform_archive_mismatch_fails_before_extraction(terraform_checks):
    import hashlib

    content = b"synthetic-tool-archive"
    filename = "terraform_1.17.0_windows_amd64.zip"
    valid = hashlib.sha256(content).hexdigest() + "  " + filename
    terraform_checks.verify_archive(content, filename, valid)
    with pytest.raises(ValueError, match="terraform_checksum_mismatch"):
        terraform_checks.verify_archive(content + b"changed", filename, valid)


def test_terraform_check_propagates_child_failure_without_retry(terraform_checks, monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(terraform_checks, "require_toolchain", lambda: Path("synthetic-terraform"))

    def execute(binary, arguments, *, directory, values):
        calls.append(arguments)
        if arguments[0] == "validate":
            raise subprocess.CalledProcessError(1, "terraform")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(terraform_checks, "execute", execute)
    with pytest.raises(subprocess.CalledProcessError):
        terraform_checks.check()
    assert calls[0][0] == "fmt" and calls[-1][0] == "validate"
    assert not any(
        arguments[0] in {"apply", "plan", "destroy", "force-unlock"} for arguments in calls
    )


def test_terraform_check_uses_validate_and_mocked_tests_only(terraform_checks, monkeypatch):
    import json
    from types import SimpleNamespace

    calls = []
    summary = json.dumps(
        {
            "type": "test_summary",
            "test_summary": {
                "status": "pass",
                "passed": 1,
                "failed": 0,
                "errored": 0,
                "skipped": 0,
            },
        }
    )
    monkeypatch.setattr(terraform_checks, "require_toolchain", lambda: Path("synthetic-terraform"))
    monkeypatch.setattr(
        terraform_checks,
        "execute",
        lambda binary, arguments, **keywords: (
            calls.append(arguments) or SimpleNamespace(stdout=summary)
        ),
    )
    monkeypatch.setattr(terraform_checks, "environment_findings", lambda: [])
    monkeypatch.setattr(terraform_checks, "scan_configuration", lambda: 1)
    terraform_checks.check()
    names = [arguments[0] for arguments in calls]
    assert names[0] == "fmt"
    assert names[1:] == ["validate", "test"] * (len(terraform_checks.ROOTS) + 13)
    assert terraform_checks.ROOTS[-1] == terraform_checks.BASE / "app_environment"


def test_terraform_preparation_is_explicit_and_missing_tool_does_not_fall_back(
    terraform_checks, monkeypatch, tmp_path
):
    monkeypatch.setattr(terraform_checks, "tool_path", lambda: tmp_path / "absent-terraform")
    with pytest.raises(RuntimeError, match="terraform_preparation_required"):
        terraform_checks.require_toolchain()


def test_terraform_check_rejects_successful_but_empty_native_suite(terraform_checks, monkeypatch):
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(terraform_checks, "require_toolchain", lambda: Path("synthetic-terraform"))
    summary = json.dumps(
        {
            "type": "test_summary",
            "test_summary": {"status": "pass", "passed": 0, "failed": 0, "skipped": 0},
        }
    )
    monkeypatch.setattr(
        terraform_checks, "execute", lambda *arguments, **keywords: SimpleNamespace(stdout=summary)
    )
    with pytest.raises(RuntimeError, match="terraform_native_tests_incomplete"):
        terraform_checks.check()


RETIRED_PATHS = (
    "/v1/internal/test-sessions",
    "/v1/calls/",
    "/v1/provider/callbacks",
    "/v1/operations/drain",
    "/v1/dev/transport",
    "caller.test.v1",
)
# Tests that assert absence, this file, this feature's documents and historical records.
RETIRED_PATH_ALLOWED = (
    "specs/",
    "docs/acceptance/",
    "code/tools/tests/test_static_checks.py",
    "code/backend/tests/contract/test_route_inventory.py",
    "code/backend/tests/contract/test_voice_endpoint.py",
    "docs/conversation-endpoints.md",
    "code/backend/tests/unit/test_metrics.py",
)


def test_active_files_name_no_retired_endpoint_path():
    root = Path(__file__).resolve().parents[3]
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    offenders = []
    for name in listed:
        path = root / name
        if name.startswith(RETIRED_PATH_ALLOWED) or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(retired in content for retired in RETIRED_PATHS):
            offenders.append(name)

    assert offenders == []


def test_identity_hand_over_directory_is_ignored_by_git():
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["git", "check-ignore", "code/.local/identity/int/management-signin-secret"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
