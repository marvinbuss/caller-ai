"""Preflight, confirm and run the push and pull request hook; skips without gh or its login."""

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)


def preflight(
    which: Callable[[str], str | None] = shutil.which, runner: Runner = run
) -> str | None:
    """Return the reason to skip, or None when push and pull request can proceed."""
    for tool in ("git", "gh"):
        if which(tool) is None:
            return f"{tool} is not installed"
    if runner(["gh", "auth", "status"]).returncode != 0:
        return "gh is not authenticated; run gh auth login"
    return None


def execute(title: str, *, confirmed: bool, runner: Runner = run) -> int:
    if not confirmed:
        print("REFUSED: explicit confirmation is required before pushing or opening a pull request")
        return 2
    branch = runner(["git", "branch", "--show-current"]).stdout.strip()
    if not branch or branch == "main":
        print("REFUSED: run this from a feature branch, never main")
        return 2
    # A plain push: this command never force-pushes.
    if runner(["git", "push", "--set-upstream", "origin", branch]).returncode != 0:
        print("FAILED: git push was rejected; nothing else was done")
        return 1
    created = runner(
        ["gh", "pr", "create", "--base", "main", "--head", branch, "--title", title, "--fill"]
    )
    print(created.stdout.strip() or "pull request not created")
    return created.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--title", default="")
    arguments = parser.parse_args(argv)
    reason = preflight()
    if reason is not None:
        print(f"SKIP: {reason}")
        return 0
    if arguments.check:
        print("READY: git and gh are available and authenticated")
        return 0
    return execute(arguments.title or "Spec Kit feature", confirmed=arguments.confirm)


if __name__ == "__main__":
    sys.exit(main())
