"""The commands CI and developers run: prepare, check, frontend, test, quick and diagnostics.

Each command runs its child processes in order and stops at the first failure with a non-zero exit.
`prepare` builds the three locked environments, `check` runs lint, types, module reachability,
Terraform, secret and publication checks and the tools tests, `test` runs every suite and `quick`
runs only the fast tier. Local mirror settings come from the ignored `code/.local/` files; CI uses
public indexes.
"""

import argparse
import os
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]


def build_failure_metadata(log: Path) -> dict[str, int | str | bool]:
    with log.open("rb") as source:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(max(0, size - 65536))
        data = source.read(65536)
    diagnostic = re.sub(
        r"\x1b\[[0-?]*[ -/]*[@-~]", "", data.decode("utf-8", errors="replace").lower()
    )
    phase = "before_initialization"
    for label, marker in (
        ("initialization", "initializing"),
        ("compilation", "compiling"),
        ("client_bundle", "building client environment"),
        ("server_bundle", "building ssr environment"),
        ("prerender", "prerender"),
    ):
        if marker in diagnostic:
            phase = label
    node_error = "none"
    for code in (
        "ERR_UNSUPPORTED_ESM_URL_SCHEME",
        "ERR_MODULE_NOT_FOUND",
        "ERR_REQUIRE_ESM",
        "ERR_UNKNOWN_FILE_EXTENSION",
        "ERR_UNSUPPORTED_DIR_IMPORT",
        "EACCES",
        "ENOENT",
        "EADDRINUSE",
        "ECONNREFUSED",
        "SYNTAXERROR",
        "REFERENCEERROR",
        "TYPEERROR",
        "RANGEERROR",
        "ROLLUPERROR",
    ):
        if code.lower() in diagnostic:
            node_error = code
            break
    return {
        "phase": phase,
        "log_bytes": min(size, 16777216),
        "utf16_marker": data.startswith((b"\xff\xfe", b"\xfe\xff")),
        "python_traceback": "traceback (most recent call last)" in diagnostic,
        "replacement_characters": "\ufffd" in diagnostic,
        "node_error": node_error,
    }


def build_failure_category(log: Path) -> str:
    try:
        with log.open("rb") as source:
            source.seek(0, os.SEEK_END)
            source.seek(max(0, source.tell() - 65536))
            diagnostic = source.read(65536).decode("utf-8", errors="replace").lower()
    except OSError:
        return "diagnostic_unavailable"
    diagnostic = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", diagnostic)
    categories = (
        (
            "browser_sandbox_unavailable",
            (
                "no usable sandbox",
                "running as root without --no-sandbox",
                "failed to move to new namespace",
            ),
        ),
        (
            "browser_dependency_unavailable",
            ("cannot open shared object file", "host system is missing dependencies"),
        ),
        (
            "package_fetch_failed",
            ("npm err!", "npm error", "unable to verify", "certificate", "econnreset"),
        ),
        ("diagram_syntax_invalid", ("parse error on line", "syntax error in text")),
        ("frontend_prerender_failed", ("prerender: request failed", "error during prerendering")),
        ("frontend_module_missing", ("cannot find module", "err_module_not_found")),
        ("text_encoding_failed", ("unicodedecodeerror", "unicodeencodeerror")),
        ("build_type_error", ("typeerror:",)),
        ("build_file_missing", ("filenotfounderror:",)),
    )
    for category, markers in categories:
        if any(marker in diagnostic for marker in markers):
            return category
    return "unclassified"


def local_feed_settings(values: dict[str, str]) -> dict[str, str]:
    if any(values.get(name, "").lower() in ("true", "1") for name in ("CI", "GITHUB_ACTIONS")):
        return {}
    path = ROOT / "code/.local/feeds.toml"
    if not path.exists():
        return {}
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            raise ValueError
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(data) != {"environment"} or not isinstance(data["environment"], dict):
            raise ValueError
        settings = data["environment"]
        allowed = {"UV_DEFAULT_INDEX", "NPM_CONFIG_REGISTRY", "BUN_CONFIG_DEFAULT_REGISTRY"}
        if not settings or not set(settings) <= allowed:
            raise ValueError
        for value in settings.values():
            if not isinstance(value, str) or any(character.isspace() for character in value):
                raise ValueError
            endpoint = urlsplit(value)
            if (
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username is not None
                or endpoint.password is not None
                or endpoint.query
                or endpoint.fragment
                or endpoint.port not in (None, 443)
            ):
                raise ValueError
        return settings
    except (OSError, ValueError, TypeError):
        raise ValueError("local_feed_settings_invalid") from None


def environment() -> dict[str, str]:
    values = dict(os.environ)
    values.pop("VIRTUAL_ENV", None)
    values.pop("UV_PROJECT_ENVIRONMENT", None)
    settings = local_feed_settings(values)
    for name, value in settings.items():
        values.setdefault(name, value)
    if settings:
        values.setdefault("UV_NO_SYNC", "1")
    values.setdefault("UV_DEFAULT_INDEX", "https://pypi.org/simple")
    values.setdefault("NPM_CONFIG_REGISTRY", "https://registry.npmjs.org/")
    values.setdefault("BUN_CONFIG_DEFAULT_REGISTRY", values["NPM_CONFIG_REGISTRY"])
    return values


def run(*arguments: str) -> None:
    values = environment()
    if arguments[:2] in (("uv", "export"), ("uv", "lock")):
        values["UV_DEFAULT_INDEX"] = "https://pypi.org/simple"
    try:
        subprocess.run(arguments, cwd=ROOT, env=values, check=True, timeout=900)
    finally:
        if "reflex" in arguments:
            restore_frontend_lock()


def restore_frontend_lock() -> None:
    if not (ROOT / "code/management/reflex.lock/bun.lock").is_file():
        return
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(ROOT / "code/tools"),
            "--no-sync",
            "python",
            str(ROOT / "code/tools/scripts/check_publication.py"),
            "frontend",
        ],
        cwd=ROOT,
        env=environment(),
        check=True,
        timeout=30,
    )


def prepare_local() -> None:
    directory = ROOT / "code/.local"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=directory) as temporary:
        for project in ("backend", "management", "tools"):
            path = ROOT / "code" / project
            python = path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if not python.is_file():
                version = (path / ".python-version").read_text(encoding="utf-8").strip()
                run("uv", "venv", "--python", version, str(path / ".venv"))
            requirements = Path(temporary) / f"{project}.txt"
            run(
                "uv",
                "export",
                "--locked",
                "--all-groups",
                "--no-emit-project",
                "--quiet",
                "--project",
                str(path),
                "--format",
                "requirements-txt",
                "--output-file",
                str(requirements),
            )
            run("uv", "pip", "sync", "--require-hashes", "--python", str(python), str(requirements))
            if project != "tools":
                run(
                    "uv",
                    "pip",
                    "install",
                    "--no-deps",
                    "--python",
                    str(python),
                    "--editable",
                    str(path),
                )


def prepare() -> None:
    if local_feed_settings(dict(os.environ)):
        prepare_local()
    else:
        for project in ("backend", "management", "tools"):
            arguments = ["uv", "sync", "--locked", "--project", f"code/{project}"]
            if project != "tools":
                arguments.extend(("--group", "dev"))
            run(*arguments)
        run("uv", "sync", "--locked", "--project", "code/e2e")
    run(
        "uv",
        "run",
        "--locked",
        "--project",
        "code/tools",
        "python",
        "code/tools/scripts/verify_terraform.py",
        "prepare",
    )


def collect_editor_diagnostic() -> None:
    from check_publication import check_paths

    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows":
        raise RuntimeError("compiler_diagnostic_profile_not_authorized")
    source = ROOT / "code/management/test-results/editor-host.log"
    target = ROOT / "code/tools/test-results/diagnostics/editor-build.log"
    target.unlink(missing_ok=True)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 262144:
        raise RuntimeError("compiler_diagnostic_source_invalid")
    content = source.read_bytes()
    if b"Creating Production Build failed" not in content:
        raise RuntimeError("compiler_diagnostic_not_failed_build")
    if check_paths([source], ROOT) != 0:
        raise RuntimeError("compiler_diagnostic_private_origin_rejected")
    run(
        "uv",
        "run",
        "--project",
        str(ROOT / "code/tools"),
        "--no-sync",
        "python",
        str(ROOT / "code/tools/scripts/check_secrets.py"),
        str(source),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    print("compiler_only_diagnostic_retained")


def check() -> None:
    for project in ("backend", "management", "tools", "e2e"):
        run("uv", "lock", "--check", "--offline", "--project", f"code/{project}")
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "code",
            "docs",
            "README.md",
            "specs",
            ".github/workflows",
        ],
        cwd=ROOT,
        env=environment(),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    files = [
        path
        for path in result.stdout.splitlines()
        if (ROOT / path).exists() or (ROOT / path).is_symlink()
    ]
    if not files:
        raise RuntimeError("No source files found; an empty check is not success.")
    run(
        "uv",
        "run",
        "--locked",
        "--project",
        "code/tools",
        "pre-commit",
        "run",
        "--config",
        "code/.pre-commit-config.yaml",
        "--files",
        *files,
    )
    for script in ("validate_environment.py", "check_terraform_structure.py"):
        run(
            "uv",
            "run",
            "--locked",
            "--project",
            "code/tools",
            "python",
            f"code/tools/scripts/{script}",
        )
    run("uv", "run", "--locked", "--project", "code/tools", "pytest", "code/tools/tests", "-q")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "check", "frontend", "test", "e2e", "quick", "diagnostics")
    )
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        prepare()
    elif arguments.command == "quick":
        # Fast tier only: unit and contract tests; the full tier (`test`) stays the required gate.
        for project in ("backend", "management"):
            run(
                "uv",
                "run",
                "--directory",
                f"code/{project}",
                "--locked",
                "python",
                "-m",
                "pytest",
                "-q",
                "-m",
                "quick",
            )
    elif arguments.command == "diagnostics":
        collect_editor_diagnostic()
    elif arguments.command == "e2e":
        run(
            "uv",
            "run",
            "--directory",
            "code/e2e",
            "--locked",
            "python",
            "-m",
            "pytest",
            "-q",
            "--tb=short",
        )
    elif arguments.command == "frontend":
        run(
            "uv",
            "run",
            "--directory",
            "code/management",
            "--locked",
            "reflex",
            "init",
            "--no-agents",
        )
        run(
            "uv",
            "run",
            "--directory",
            "code/management",
            "--locked",
            "reflex",
            "export",
            "--frontend-only",
            "--no-zip",
        )
        run(
            "uv",
            "run",
            "--directory",
            "code/management",
            "--locked",
            "playwright",
            "install",
            "chromium",
        )
    elif arguments.command == "test":
        for project in ("backend", "management"):
            run(
                "uv",
                "run",
                "--directory",
                f"code/{project}",
                "--locked",
                "python",
                "-m",
                "pytest",
                "-q",
                "--tb=short",
            )
    else:
        check()


if __name__ == "__main__":
    main()
