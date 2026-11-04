"""Report production modules that nothing reaches and modules without a purpose docstring.

Reachability follows every import statement (including lazy and type-checking imports) from the
application entry modules, so a module only tests import is reported as unreachable. The docstring
check requires each production module to open with a short statement of its purpose.
"""

import argparse
import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class Target:
    """One production package and the entry modules that make everything else reachable."""

    source_root: Path
    package: str
    entries: tuple[str, ...]


TARGETS = {
    "backend": Target(
        ROOT / "code/backend/src",
        "caller_runtime",
        ("caller_runtime.main", "caller_runtime.composition"),
    ),
    "management": Target(
        ROOT / "code/management",
        "management_app",
        ("management_app.management_app",),
    ),
}
SCRIPT_DIRECTORIES = (ROOT / "code/tools/scripts",)

# Modules kept although no entry module imports them; each needs a recorded reason.
ALLOWED_UNREACHABLE: dict[str, str] = {
    "code/management/management_app/adapters/entra_oidc.py": (
        "the live Entra adapter an operator passes to compose_management as an explicit, "
        "independently authorized resource; no default composition may import it"
    ),
}


def module_files(source_root: Path, package: str) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted((source_root / package).rglob("*.py")):
        parts = list(path.relative_to(source_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        files[".".join(parts)] = path
    return files


def imported_names(name: str, path: Path, tree: ast.AST) -> set[str]:
    """Return dotted names the module imports, resolving relative imports against its package."""
    package_parts = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base_parts = (
                package_parts[: len(package_parts) - (node.level - 1)] if node.level else []
            )
            base = ".".join(base_parts + (node.module.split(".") if node.module else []))
            if base:
                found.add(base)
            found.update(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
    return found


def find_unreachable(source_root: Path, package: str, entries: Sequence[str]) -> list[Path]:
    files = module_files(source_root, package)
    reached: set[str] = set()
    pending = [entry for entry in entries if entry in files]
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        path = files[name]
        for imported in imported_names(name, path, ast.parse(path.read_bytes(), str(path))):
            parts = imported.split(".")
            for length in range(1, len(parts) + 1):
                candidate = ".".join(parts[:length])
                if candidate in files and candidate not in reached:
                    pending.append(candidate)
    return [path for name, path in files.items() if name not in reached and path.stat().st_size]


def find_missing_docstrings(paths: Iterable[Path]) -> list[Path]:
    missing = []
    for path in paths:
        content = path.read_bytes()
        if content.strip() and not ast.get_docstring(ast.parse(content, str(path))):
            missing.append(path)
    return missing


def production_files(selected: Sequence[str]) -> list[Path]:
    paths: list[Path] = []
    for name in selected:
        if name in TARGETS:
            target = TARGETS[name]
            paths.extend(module_files(target.source_root, target.package).values())
        else:
            for directory in SCRIPT_DIRECTORIES:
                paths.extend(sorted(directory.glob("*.py")))
    return paths


def display(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit non-zero on any finding")
    parser.add_argument("--only", choices=("backend", "management", "tools"), action="append")
    arguments = parser.parse_args(argv)
    selected = arguments.only or ["backend", "management", "tools"]
    findings = 0
    for name in selected:
        if name in TARGETS:
            target = TARGETS[name]
            for path in find_unreachable(target.source_root, target.package, target.entries):
                reason = ALLOWED_UNREACHABLE.get(display(path))
                if reason:
                    print(f"allowed: {display(path)} ({reason})")
                    continue
                print(f"unreachable: {display(path)}")
                findings += 1
    for path in find_missing_docstrings(production_files(selected)):
        print(f"no_docstring: {display(path)}")
        findings += 1
    print(f"findings={findings}")
    return 1 if arguments.strict and findings else 0


if __name__ == "__main__":
    sys.exit(main())
