"""Reject private feed URLs and relocate unchanged locks against verified public metadata."""

import argparse
import base64
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import json5

ROOT = Path(__file__).resolve().parents[3]
PROJECTS = ("backend", "management", "tools")
URLS = re.compile(r"https?://[^\s\"'<>`]+", re.IGNORECASE)


def public_url(value: str, host: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("nonpublic_package_origin")
    return value


def check_paths(paths: list[Path], root: Path) -> int:
    failed = False
    for path in paths:
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            print("publication_input_invalid", file=sys.stderr)
            failed = True
            continue
        content = path.read_bytes().decode("utf-8", errors="replace")
        private = False
        for value in URLS.findall(content):
            try:
                host = (urlsplit(value).hostname or "").lower()
                private |= "feedproxy" in host or ".pkgs." in host
            except ValueError:
                authority = value.partition("://")[2].split("/", 1)[0].lower()
                private |= "feedproxy" in authority or ".pkgs." in authority
        if private:
            print(f"{path.relative_to(root)}: private_package_origin", file=sys.stderr)
            failed = True
    return int(failed)


def check_git(root: Path) -> int:
    pattern = r"""https?://[^[:space:]"'<>]*(feedproxy|[.]pkgs[.])"""
    commands = (
        ["git", "grep", "--cached", "-l", "-I", "-i", "-E", pattern, "--", "."],
        ["git", "log", "--all", "--format=%H", "--regexp-ignore-case", "-G" + pattern, "--", "."],
    )
    failed = False
    for command in commands:
        result = subprocess.run(command, cwd=root, capture_output=True, timeout=30, check=False)
        if result.returncode not in (0, 1) or (command[1] == "log" and result.returncode != 0):
            raise RuntimeError("publication_git_check_unavailable")
        if result.stdout:
            print(
                "publication_"
                + ("history" if command[1] == "log" else "index")
                + "_contains_private_origin",
                file=sys.stderr,
            )
            failed = True
    return int(failed)


def inventory(root: Path) -> dict:
    python = set()
    for project in PROJECTS:
        lock = tomllib.loads((root / f"code/{project}/uv.lock").read_text(encoding="utf-8"))
        python.update(
            (package["name"], package["version"])
            for package in lock["package"]
            if "registry" in package["source"]
        )
    frontend = json5.loads(
        (root / "code/management/reflex.lock/bun.lock").read_text(encoding="utf-8")
    )
    npm = {tuple(package[0].rpartition("@")[::2]) for package in frontend["packages"].values()}
    return {
        "python": [{"name": name, "version": version} for name, version in sorted(python)],
        "npm": [{"name": name, "version": version} for name, version in sorted(npm)],
    }


def relocate_python(content: str, metadata: dict) -> str:
    parsed = tomllib.loads(content)
    replacements = {}
    for package in parsed["package"]:
        if "registry" not in package["source"]:
            continue
        public = metadata[package["name"] + "==" + package["version"]]
        files = {item["filename"]: item for item in public["urls"]}
        replacements[package["source"]["registry"]] = "https://pypi.org/simple"
        for artifact in [
            *package.get("wheels", []),
            *([package["sdist"]] if "sdist" in package else []),
        ]:
            filename = unquote(urlsplit(artifact["url"]).path.rsplit("/", 1)[-1])
            candidate = files.get(filename)
            if (
                candidate is None
                or "sha256:" + candidate["digests"]["sha256"] != artifact["hash"]
                or candidate["size"] != artifact["size"]
            ):
                raise ValueError("public_artifact_mismatch")
            replacements[artifact["url"]] = public_url(candidate["url"], "files.pythonhosted.org")
    for previous, replacement in replacements.items():
        content = content.replace(json.dumps(previous), json.dumps(replacement))
    tomllib.loads(content)
    return content


def relocate_frontend(content: str) -> str:
    parsed = json5.loads(content)
    for package in parsed["packages"].values():
        name, separator, version = package[0].rpartition("@")
        if not name or not separator or not version:
            raise ValueError("package_coordinate_invalid")
        algorithm, separator, encoded = package[-1].partition("-")
        sizes = {"sha1": 20, "sha256": 32, "sha384": 48, "sha512": 64}
        if (
            not separator
            or algorithm not in sizes
            or len(base64.b64decode(encoded, validate=True)) != sizes[algorithm]
        ):
            raise ValueError("public_artifact_mismatch")
        if package[1]:
            filename = unquote(urlsplit(package[1]).path.rsplit("/", 1)[-1])
            if filename != name.rsplit("/", 1)[-1] + "-" + version.removeprefix("v") + ".tgz":
                raise ValueError("package_coordinate_invalid")
        package[1] = ""
    return json.dumps(parsed, indent=2) + "\n"


def relocate(root: Path, metadata: dict) -> None:
    changes = {}
    for project in PROJECTS:
        path = root / f"code/{project}/uv.lock"
        changes[path] = relocate_python(path.read_text(encoding="utf-8"), metadata["python"])
    path = root / "code/management/reflex.lock/bun.lock"
    changes[path] = relocate_frontend(path.read_text(encoding="utf-8"))
    for path, content in changes.items():
        path.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("check", "inventory", "relocate", "frontend"),
        nargs="?",
        default="check",
    )
    parser.add_argument("--metadata", type=Path)
    options = parser.parse_args()
    if options.command == "frontend":
        try:
            path = ROOT / "code/management/reflex.lock/bun.lock"
            path.write_text(
                relocate_frontend(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n"
            )
        except Exception:
            print("frontend_lock_cleanup_failed", file=sys.stderr)
            return 1
        return 0
    if options.command == "inventory":
        print(json.dumps(inventory(ROOT)))
        return 0
    if options.command == "relocate":
        try:
            if options.metadata is None:
                raise ValueError("metadata_required")
            relocate(ROOT, json.loads(options.metadata.read_text(encoding="utf-8")))
        except Exception:
            print("public_lock_verification_failed", file=sys.stderr)
            return 1
        print("public_lock_origins_verified")
        return 0
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=30,
    )
    paths = [ROOT / value for value in result.stdout.decode("utf-8").split("\0") if value]
    if not paths:
        return 1
    return max(check_paths(paths, ROOT), check_git(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
