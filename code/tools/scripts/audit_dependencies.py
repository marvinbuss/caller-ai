"""Fail-closed locked Python, frontend and image advisory gates; no secret reports."""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx
import json5
from ci import ROOT, check, environment, run
from test_images import docker

SCANNER = (
    "aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c"
)


def audit_frontend(lock: Path, client: httpx.Client) -> dict[str, int]:
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
        raise RuntimeError("frontend_vulnerabilities_detected")
    return {"packages": len(packages), "vulnerabilities": 0}


def audit_python(project: str, temporary: Path) -> dict[str, int | str]:
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
            "--no-deps",
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
    if (
        result.returncode != 0
        or not dependencies
        or any(item.get("vulns") or item.get("skip_reason") for item in dependencies)
    ):
        raise RuntimeError("python_audit_incomplete_or_vulnerable")
    return {"project": project, "packages": len(dependencies), "vulnerabilities": 0}


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
    arguments = parser.parse_args()
    check()
    with tempfile.TemporaryDirectory(prefix="caller-audit-") as temporary:
        python = [
            audit_python(project, Path(temporary)) for project in ("backend", "management", "tools")
        ]
    with httpx.Client(timeout=30) as client:
        frontend = audit_frontend(ROOT / "code/management/reflex.lock/bun.lock", client)
    result = {"python": python, "frontend": frontend}
    if arguments.images:
        result["images"] = audit_images()
    print(json.dumps({"result": "dependency_audits_passed", **result}))


if __name__ == "__main__":
    main()
