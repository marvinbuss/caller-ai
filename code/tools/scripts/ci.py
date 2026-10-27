"""Portable foundation CI commands; fail on every unsuccessful child command."""

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def environment() -> dict[str, str]:
    values = dict(os.environ)
    values.pop("VIRTUAL_ENV", None)
    values.pop("UV_PROJECT_ENVIRONMENT", None)
    values.setdefault("UV_DEFAULT_INDEX", "https://pypi.org/simple")
    values.setdefault("NPM_CONFIG_REGISTRY", "https://registry.npmjs.org/")
    values["BUN_CONFIG_DEFAULT_REGISTRY"] = values["NPM_CONFIG_REGISTRY"]
    return values


def run(*arguments: str) -> None:
    subprocess.run(arguments, cwd=ROOT, env=environment(), check=True, timeout=900)


def prepare() -> None:
    for project in ("backend", "management", "tools"):
        arguments = ["uv", "sync", "--locked", "--project", f"code/{project}"]
        if project != "tools":
            arguments.extend(("--group", "dev"))
        run(*arguments)
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


def check() -> None:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "code",
            ".github/workflows",
        ],
        cwd=ROOT,
        env=environment(),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    files = result.stdout.splitlines()
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
    run("uv", "run", "--locked", "--project", "code/tools", "pytest", "code/tools/tests", "-q")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check", "frontend", "test"))
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        prepare()
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
                "pytest",
                "-q",
                "--tb=short",
            )
    else:
        check()


if __name__ == "__main__":
    main()
