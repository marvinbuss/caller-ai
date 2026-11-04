"""Advisory gates for locked Python, frontend and image dependencies.

Runs `pip-audit` against each project's hashed lock, audits the resolved frontend packages and, with
`--images`, scans the built images. Findings fail the run unless they are an approved mirror gap
recorded in `code/dependency-mirror-gaps.yaml` (never high or unknown severity, and it must be
re-verified within 7 days and before its expiry). Output is a JSON
summary of counts and package names; it never prints secrets or raw tool output.
"""

import argparse
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx
import json5
import yaml
from ci import ROOT, check, environment, run
from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from test_images import docker

SCANNER = (
    "aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c"
)
MIRROR_GAPS = ROOT / "code/dependency-mirror-gaps.yaml"


@dataclass(frozen=True)
class MirrorGap:
    """An exact, owner-approved seven-day mirror gap, never a clean advisory result."""

    ecosystem: str
    package: str
    version: str
    advisory: str
    fixed_version: str
    owner: str
    reason: str
    verified_at: datetime
    expires_at: datetime


class GapLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently replacing approvals."""

    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise ValueError("duplicate_or_invalid_key")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load_mirror_gaps(
    path: Path, *, now: datetime | None = None, allow_expired: bool = False
) -> tuple[MirrorGap, ...]:
    """Load bounded exact approvals; expiry is bypassed only for read-only revalidation."""
    current = now or datetime.now(UTC)
    try:
        content = path.read_bytes()
        if len(content) > 32768 or current.utcoffset() != timedelta(0):
            raise ValueError("invalid_bounds")
        if any(
            isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
            for token in yaml.scan(content)
        ):
            raise ValueError("yaml_alias_forbidden")
        document = yaml.load(content, Loader=GapLoader)
        if (
            not isinstance(document, dict)
            or set(document) != {"schema_version", "exceptions"}
            or type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or not isinstance(document["exceptions"], list)
            or len(document["exceptions"]) > 32
        ):
            raise ValueError("invalid_schema")
        gaps = []
        identities = set()
        for entry in document["exceptions"]:
            if not isinstance(entry, dict) or set(entry) != set(MirrorGap.__dataclass_fields__):
                raise ValueError("invalid_fields")
            if any(
                not isinstance(value, str) or not value or len(value) > 256
                for value in entry.values()
            ):
                raise ValueError("invalid_value")
            if entry["ecosystem"] not in {"npm", "PyPI"}:
                raise ValueError("invalid_ecosystem")
            if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-zA-Z0-9][a-zA-Z0-9._-]*", entry["package"]):
                raise ValueError("invalid_package")
            if any(
                not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.+_-]{0,127}", entry[key])
                for key in ("version", "fixed_version", "owner")
            ):
                raise ValueError("invalid_identifier")
            if not re.fullmatch(
                r"(?:CVE-\d{4}-\d+|GHSA-[a-z0-9-]+|PYSEC-\d{4}-\d+)", entry["advisory"]
            ):
                raise ValueError("invalid_advisory")
            verified = datetime.fromisoformat(entry["verified_at"].replace("Z", "+00:00"))
            expires = datetime.fromisoformat(entry["expires_at"].replace("Z", "+00:00"))
            if (
                verified.utcoffset() != timedelta(0)
                or expires.utcoffset() != timedelta(0)
                or not verified <= current
                or not timedelta(0) < expires - verified <= timedelta(days=7)
                or (not allow_expired and current >= expires)
            ):
                raise ValueError("invalid_approval_window")
            identity = tuple(entry[key] for key in ("ecosystem", "package", "version", "advisory"))
            if identity in identities:
                raise ValueError("duplicate_approval")
            identities.add(identity)
            gaps.append(MirrorGap(**{**entry, "verified_at": verified, "expires_at": expires}))
        return tuple(gaps)
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        raise RuntimeError("mirror_gap_policy_invalid") from None


class MirrorLinks(HTMLParser):
    """Collect filenames only from a bounded Python simple-index response."""

    def __init__(self):
        super().__init__()
        self.filenames = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.filenames.append(unquote(urlsplit(value).path.rsplit("/", 1)[-1]))


def revalidate_mirror_gaps(
    gaps: tuple[MirrorGap, ...], client: httpx.Client, values: dict[str, str]
) -> list[dict[str, str | bool]]:
    """Read exact approved mirrors; available fixes and lookup errors never confirm a gap."""
    reports = []
    for gap in gaps:
        registry = values.get(
            "NPM_CONFIG_REGISTRY" if gap.ecosystem == "npm" else "UV_DEFAULT_INDEX", ""
        )
        origin = urlsplit(registry)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.port not in (None, 443)
            or origin.hostname in {"pypi.org", "registry.npmjs.org"}
        ):
            raise RuntimeError("approved_mirror_required")
        package = gap.package if gap.ecosystem == "npm" else canonicalize_name(gap.package)
        url = registry.rstrip("/") + "/" + package + "/"
        try:
            with client.stream(
                "GET",
                url,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                follow_redirects=False,
            ) as response:
                if (
                    response.status_code not in (200, 404)
                    or response.headers.get("content-encoding", "identity") != "identity"
                ):
                    raise ValueError("mirror_lookup_failed")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 4 * 1024 * 1024:
                        raise ValueError("mirror_metadata_too_large")
                available = False
                if response.status_code == 200 and gap.ecosystem == "npm":
                    metadata = json.loads(content)
                    if not isinstance(metadata, dict) or not isinstance(
                        metadata.get("versions"), dict
                    ):
                        raise ValueError("mirror_metadata_invalid")
                    available = gap.fixed_version in metadata["versions"]
                elif response.status_code == 200:
                    if "json" in response.headers.get("content-type", ""):
                        metadata = json.loads(content)
                        filenames = [item["filename"] for item in metadata["files"]]
                    else:
                        links = MirrorLinks()
                        links.feed(content.decode("utf-8"))
                        filenames = links.filenames
                    for filename in filenames:
                        try:
                            parsed = (
                                parse_wheel_filename(filename)
                                if filename.endswith(".whl")
                                else parse_sdist_filename(filename)
                            )
                        except ValueError:
                            continue
                        if (
                            parsed[0] == canonicalize_name(gap.package)
                            and str(parsed[1]) == gap.fixed_version
                        ):
                            available = True
                reports.append(
                    {
                        "package": gap.package,
                        "fixed_version": gap.fixed_version,
                        "gap_confirmed": not available,
                    }
                )
        except (httpx.HTTPError, ValueError, TypeError, KeyError, UnicodeError):
            raise RuntimeError("mirror_gap_revalidation_failed") from None
    return reports


def approved_findings(
    findings: list[dict], ecosystem: str, gaps: tuple[MirrorGap, ...], client: httpx.Client
) -> int:
    """Match exact unexpired lower-severity approvals, retaining original advisory records."""
    approved = 0
    reviewed = set()
    for finding in findings:
        eligible = any(
            gap.ecosystem == ecosystem
            and gap.package == finding["package"]
            and gap.version == finding["version"]
            and gap.advisory == finding["id"]
            and datetime.now(UTC) < gap.expires_at
            and ("fix_versions" not in finding or gap.fixed_version in finding["fix_versions"])
            for gap in gaps
        )
        if not eligible:
            continue
        if finding["id"] not in reviewed:
            try:
                with client.stream(
                    "GET", "https://api.osv.dev/v1/vulns/" + finding["id"], follow_redirects=False
                ) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 256 * 1024:
                            raise ValueError("advisory_too_large")
                    advisory = json.loads(content)
                if advisory.get("id") != finding["id"] or advisory.get("database_specific", {}).get(
                    "severity"
                ) not in {"LOW", "MODERATE"}:
                    raise ValueError("ineligible_severity")
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                raise RuntimeError("mirror_gap_advisory_not_eligible") from None
            reviewed.add(finding["id"])
        approved += 1
    return approved


def audit_frontend(
    lock: Path, client: httpx.Client, *, gaps: tuple[MirrorGap, ...] = ()
) -> dict[str, int]:
    data = json5.loads(lock.read_text(encoding="utf-8"))
    coordinates = set()
    for package in data["packages"].values():
        name, separator, version = package[0].rpartition("@")
        if not name or not separator or not version:
            raise RuntimeError("frontend_lock_coordinate_invalid")
        coordinates.add((name, version.removeprefix("v")))
    packages = sorted(coordinates)
    if not packages:
        raise RuntimeError("empty_frontend_audit")
    findings = []
    for offset in range(0, len(packages), 100):
        batch = packages[offset : offset + 100]
        response = client.post(
            "https://api.osv.dev/v1/querybatch",
            json={
                "queries": [
                    {"package": {"name": name, "ecosystem": "npm"}, "version": version}
                    for name, version in batch
                ]
            },
        )
        response.raise_for_status()
        results = response.json().get("results")
        if not isinstance(results, list) or len(results) != len(batch):
            raise RuntimeError("advisory_response_invalid")
        for coordinate, result in zip(batch, results, strict=True):
            vulnerabilities = result.get("vulns", [])
            if not isinstance(vulnerabilities, list):
                raise RuntimeError("advisory_response_invalid")
            for vulnerability in vulnerabilities:
                identifier = vulnerability.get("id", "")
                if not re.fullmatch(r"(?:CVE-\d{4}-\d+|GHSA-[a-z0-9-]+)", identifier):
                    raise RuntimeError("advisory_identifier_invalid")
                findings.append(
                    {"package": coordinate[0], "version": coordinate[1], "id": identifier}
                )
    if findings:
        print(json.dumps({"frontend_findings": findings}))
    approved = approved_findings(findings, "npm", gaps, client)
    if approved != len(findings):
        raise RuntimeError("frontend_vulnerabilities_detected")
    return {
        "packages": len(packages),
        "vulnerabilities": len(findings),
        "approved_mirror_gaps": approved,
    }


def audit_python(
    project: str, temporary: Path, *, gaps: tuple[MirrorGap, ...] = ()
) -> dict[str, int | str]:
    requirements = temporary / f"{project}.txt"
    report = temporary / f"{project}.json"
    run(
        "uv",
        "export",
        "--locked",
        "--all-groups",
        "--quiet",
        "--no-emit-project",
        "--project",
        f"code/{project}",
        "--format",
        "requirements-txt",
        "--output-file",
        str(requirements),
    )
    values = environment()
    values["PIP_INDEX_URL"] = values["UV_DEFAULT_INDEX"]
    values.pop("PIP_EXTRA_INDEX_URL", None)
    result = subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "--project",
            "code/tools",
            "pip-audit",
            "--require-hashes",
            "--disable-pip",
            "--strict",
            "--requirement",
            str(requirements),
            "--format",
            "json",
            "--output",
            str(report),
        ],
        cwd=ROOT,
        env=values,
        check=False,
        timeout=600,
    )
    if not report.exists():
        raise RuntimeError("python_advisory_lookup_failed")
    dependencies = json.loads(report.read_text(encoding="utf-8"))["dependencies"]
    findings = [
        {
            "package": item["name"],
            "version": item["version"],
            "id": vulnerability["id"],
            "fix_versions": vulnerability.get("fix_versions", []),
        }
        for item in dependencies
        for vulnerability in item.get("vulns", [])
    ]
    if findings:
        print(json.dumps({"python_findings": findings}))
    with httpx.Client(timeout=30, verify=True, trust_env=False, follow_redirects=False) as client:
        approved = approved_findings(findings, "PyPI", gaps, client)
    if (
        result.returncode not in (0, 1)
        or (result.returncode != 0 and not findings)
        or not dependencies
        or any(item.get("skip_reason") for item in dependencies)
        or approved != len(findings)
    ):
        raise RuntimeError("python_audit_incomplete_or_vulnerable")
    return {
        "project": project,
        "packages": len(dependencies),
        "vulnerabilities": len(findings),
        "approved_mirror_gaps": approved,
    }


def audit_images() -> dict[str, int]:
    identifier = uuid4().hex
    cache = f"caller-audit-{identifier}"
    images = []
    try:
        for service in ("backend", "management"):
            image = f"caller-{service}:audit-{identifier}"
            docker("build", "--tag", image, f"code/{service}")
            images.append(image)
        builder = f"caller-management-build:audit-{identifier}"
        docker("build", "--target", "build", "--tag", builder, "code/management")
        images.append(builder)
        for image in images:
            result = docker(
                "run",
                "--rm",
                "--volume",
                "/var/run/docker.sock:/var/run/docker.sock",
                "--volume",
                f"{cache}:/root/.cache",
                SCANNER,
                "image",
                "--quiet",
                "--image-src",
                "docker",
                "--scanners",
                "vuln",
                "--severity",
                "HIGH,CRITICAL",
                "--exit-code",
                "1",
                "--timeout",
                "10m",
                "--format",
                "json",
                image,
            )
            report = json.loads(result)
            if not report.get("Results") or any(
                entry.get("Vulnerabilities") for entry in report["Results"]
            ):
                raise RuntimeError("image_audit_incomplete_or_vulnerable")
        return {"images": len(images), "high_or_critical": 0}
    finally:
        for image in reversed(images):
            docker("image", "rm", image)
        docker("volume", "rm", cache)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", action="store_true")
    parser.add_argument("--revalidate-mirror-gaps", action="store_true")
    arguments = parser.parse_args()
    gaps = load_mirror_gaps(MIRROR_GAPS, allow_expired=arguments.revalidate_mirror_gaps)
    if arguments.revalidate_mirror_gaps:
        with httpx.Client(
            timeout=30, verify=True, trust_env=False, follow_redirects=False
        ) as client:
            reports = revalidate_mirror_gaps(gaps, client, environment())
        print(json.dumps({"result": "mirror_gaps_revalidated", "exceptions": reports}))
        if any(not report["gap_confirmed"] for report in reports):
            raise RuntimeError("mirror_gap_fixed_release_available")
        return
    check()
    with tempfile.TemporaryDirectory(prefix="caller-audit-") as temporary:
        python = [
            audit_python(project, Path(temporary), gaps=gaps)
            for project in ("backend", "management", "tools")
        ]
    with httpx.Client(timeout=30) as client:
        frontend = audit_frontend(ROOT / "code/management/reflex.lock/bun.lock", client, gaps=gaps)
    result = {"python": python, "frontend": frontend}
    if arguments.images:
        result["images"] = audit_images()
    excepted = (
        sum(int(item["approved_mirror_gaps"]) for item in python) + frontend["approved_mirror_gaps"]
    )
    status = "dependency_audits_passed_with_exceptions" if excepted else "dependency_audits_passed"
    print(json.dumps({"result": status, **result}))


if __name__ == "__main__":
    main()
