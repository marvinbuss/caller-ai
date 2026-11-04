"""Prepares the pinned Terraform binary and runs the credential-free native tests.

It downloads Terraform 1.17.0 once, verifies its hash, then validates each root and runs its native
tests with mocked providers, plus the static checks (no stage-name logic, text history owned by the
runtime, example stage pairs). It never runs `plan` or `apply` and needs no cloud credential.
"""

import argparse
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

import httpx
from ci import ROOT

BASE = ROOT / "code/infra/terraform"
ROOTS = (BASE / "bootstrap", BASE / "modules/platform", BASE / "environments")


def version() -> str:
    value = (BASE / ".terraform-version").read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise ValueError("invalid_terraform_version_pin")
    return value


def executable_name() -> str:
    return "terraform.exe" if os.name == "nt" else "terraform"


def tool_path() -> Path:
    return ROOT / "code/tools/test-results/terraform" / version() / executable_name()


def verification_environment(directory: Path) -> dict[str, str]:
    values = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ARM_", "AZURE_", "TF_VAR_", "TF_CLI_ARGS", "TF_TOKEN_"))
    }
    for key in ("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "SYSTEM_ACCESSTOKEN", "TF_CLOUD_TOKEN"):
        values.pop(key, None)
    values.update(
        AZURE_CONFIG_DIR=str(directory / "azure"),
        TF_INPUT="0",
        TF_IN_AUTOMATION="1",
        CHECKPOINT_DISABLE="1",
    )
    return values


def execute(
    binary: Path, arguments: Sequence[str], *, directory: Path, values: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    if not arguments or arguments[0] not in {"version", "init", "fmt", "validate", "test"}:
        raise ValueError("terraform_deployment_command_forbidden")
    return subprocess.run(
        [str(binary), *arguments],
        cwd=directory,
        env=values,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def require_toolchain() -> Path:
    binary = tool_path()
    if not binary.is_file():
        raise RuntimeError("terraform_preparation_required")
    with tempfile.TemporaryDirectory(prefix="caller-tf-version-") as temporary:
        result = execute(
            binary,
            ["version", "-json"],
            directory=ROOT,
            values=verification_environment(Path(temporary)),
        )
    if json.loads(result.stdout)["terraform_version"] != version():
        raise RuntimeError("terraform_version_mismatch")
    return binary


def verify_archive(content: bytes, filename: str, manifest: str) -> None:
    hashes = [
        line.split()[0]
        for line in manifest.splitlines()
        if re.fullmatch(r"[0-9a-f]{64}\s+" + re.escape(filename), line.strip())
    ]
    if len(hashes) != 1 or hashlib.sha256(content).hexdigest() != hashes[0]:
        raise ValueError("terraform_checksum_mismatch")


def prepare() -> None:
    if not tool_path().is_file():
        if platform.machine().lower() not in {"amd64", "x86_64"} or sys.platform not in {
            "win32",
            "linux",
        }:
            raise RuntimeError("unsupported_terraform_tooling_platform")
        operating_system = "windows" if os.name == "nt" else "linux"
        filename = f"terraform_{version()}_{operating_system}_amd64.zip"
        base = f"https://releases.hashicorp.com/terraform/{version()}"
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            manifest = client.get(f"{base}/terraform_{version()}_SHA256SUMS")
            manifest.raise_for_status()
            content = bytearray()
            with client.stream("GET", f"{base}/{filename}") as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 100 * 1024 * 1024:
                        raise ValueError("terraform_archive_too_large")
        verify_archive(bytes(content), filename, manifest.text)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            binary = archive.read(executable_name())
        tool_path().parent.mkdir(parents=True, exist_ok=True)
        tool_path().write_bytes(binary)
        tool_path().chmod(0o755)
    binary_path = require_toolchain()
    with tempfile.TemporaryDirectory(prefix="caller-tf-prepare-") as temporary:
        values = verification_environment(Path(temporary))
        for directory in ROOTS:
            execute(
                binary_path,
                ["init", "-backend=false", "-input=false", "-lockfile=readonly", "-no-color"],
                directory=directory,
                values=values,
            )
    print(json.dumps({"result": "terraform_prepared", "version": version(), "roots": len(ROOTS)}))


def native_test_count(output: str) -> int:
    try:
        events = [json.loads(line) for line in output.splitlines() if line.strip()]
        if any(not isinstance(event, dict) for event in events):
            raise ValueError("invalid_test_events")
        summaries = [
            event["test_summary"] for event in events if event.get("type") == "test_summary"
        ]
        if len(summaries) != 1 or not isinstance(summaries[0], dict):
            raise ValueError("missing_test_summary")
        summary = summaries[0]
        passed: object = summary.get("passed")
        if (
            summary.get("status") != "pass"
            or not isinstance(passed, int)
            or isinstance(passed, bool)
            or passed <= 0
            or any(
                type(summary.get(key)) is not int or summary[key] != 0
                for key in ("failed", "errored", "skipped")
            )
        ):
            raise ValueError("incomplete_native_tests")
        return passed
    except (ValueError, TypeError, KeyError):
        raise RuntimeError("terraform_native_tests_incomplete") from None


STAGE_BRANCH = (
    re.compile(r"var\.environment\b"),
    re.compile(r"(==|!=)\s*\"(dev|test|prod)\""),
    re.compile(r"\"(dev|test|prod)\"\s*(==|!=)"),
    re.compile(r"contains\(\s*\[[^\]]*\"(dev|test|prod)\""),
    re.compile(r"\$\{[^}]*\}[^\"\n]*\.tfstate"),
)


def strip_comments(text: str) -> str:
    return re.sub(r"(?m)(#|//).*$", "", text)


def stage_name_findings(base: Path = BASE) -> list[str]:
    """Return `.tf` files that branch on a stage name or interpolate the state key."""
    found = []
    for path in sorted(base.rglob("*.tf")):
        if ".terraform" in path.parts:
            continue
        text = strip_comments(path.read_text(encoding="utf-8"))
        if any(pattern.search(text) for pattern in STAGE_BRANCH):
            found.append(str(path.relative_to(base)))
    return found


def text_history_owner_findings(base: Path = BASE) -> list[str]:
    """Only the runtime identity may own `text-conversations`; management must have no grant."""
    security = (base / "modules/platform/security.tf").read_text(encoding="utf-8")
    owners = re.search(r"container_owners\s*=\s*\{(.*?)\n  \}", security, re.S)
    entries = dict(re.findall(r"([a-z-]+)\s*=\s*\"([a-z]+)\"", owners.group(1))) if owners else {}
    return [] if entries.get("text-conversations") == "runtime" else ["text-conversations"]


def example_pairs(directory: Path) -> list[tuple[str, str]]:
    """Discover `(stage, stack)` pairs from file names; no stage name is known to the code."""
    pairs = set()
    for path in directory.glob("*.tfvars.json.example"):
        stage, stack, *_ = path.name.split(".")
        pairs.add((stage, stack))
    return sorted(pairs)


def validate_example_pair(directory: Path, stage: str, stack: str) -> None:
    """Check one backend/variable example pair against the stack's declared variables."""
    root = {"bootstrap": BASE / "bootstrap", "platform": BASE / "environments"}[stack]
    backend = json.loads(
        (directory / f"{stage}.{stack}.azurerm.tfbackend.json.example").read_text()
    )
    values = json.loads((directory / f"{stage}.{stack}.tfvars.json.example").read_text())
    declared = {}
    for path in root.glob("variables.tf"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'variable "([a-z_]+)" \{(.*?)\n\}', text, re.S):
            declared[match.group(1)] = "default" not in match.group(2)
    required = {name for name, needed in declared.items() if needed}
    if (
        not set(values) <= set(declared)
        or not required <= set(values)
        or "environment" in values
        or not re.fullmatch(
            r"[A-Za-z0-9._-]{1,64}(/[A-Za-z0-9._-]{1,64}){0,3}\.tfstate", str(backend.get("key"))
        )
        or backend.get("use_oidc") is not True
        or backend.get("use_cli") is not False
        or any("secret" in key.lower() or "password" in key.lower() for key in {*backend, *values})
    ):
        raise ValueError(f"invalid_example_pair:{stage}.{stack}")


def check_examples(directory: Path = BASE / "config") -> int:
    pairs = example_pairs(directory)
    for stage, stack in pairs:
        validate_example_pair(directory, stage, stack)
    return len(pairs)


def check() -> None:
    binary = require_toolchain()
    native_tests = 0
    with tempfile.TemporaryDirectory(prefix="caller-tf-check-") as temporary:
        values = verification_environment(Path(temporary))
        execute(binary, ["fmt", "-check", "-recursive", str(BASE)], directory=ROOT, values=values)
        if stage_name_findings() or text_history_owner_findings():
            raise RuntimeError("terraform_static_check_failed")
        check_examples()
        for directory in ROOTS:
            execute(binary, ["validate", "-no-color"], directory=directory, values=values)
            result = execute(binary, ["test", "-json"], directory=directory, values=values)
            native_tests += native_test_count(result.stdout)
    print(
        json.dumps(
            {
                "result": "terraform_verified",
                "version": version(),
                "roots": len(ROOTS),
                "native_tests": native_tests,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check"))
    arguments = parser.parse_args()
    try:
        prepare() if arguments.command == "prepare" else check()
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        httpx.HTTPError,
        zipfile.BadZipFile,
    ):
        raise SystemExit("terraform_verification_failed") from None


if __name__ == "__main__":
    main()
