"""Contracts for the module reachability and purpose-docstring check."""

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def check_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import check_modules

    return check_modules


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "src"
    write(root / "app" / "__init__.py", "")
    write(
        root / "app" / "main.py",
        '"""Entry."""\n\nfrom app.live import value\nfrom . import helper\n',
    )
    write(root / "app" / "live.py", '"""Live."""\n\nvalue = 1\n')
    write(root / "app" / "helper.py", '"""Helper."""\n\nfrom app.sub.deep import deep\n')
    write(root / "app" / "sub" / "__init__.py", "")
    write(root / "app" / "sub" / "deep.py", '"""Deep."""\n\ndeep = 1\n')
    write(root / "app" / "dead.py", '"""Dead code nothing imports."""\n')
    return root


def names(paths, root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in paths}


def test_reachable_modules_follow_absolute_relative_and_nested_imports(check_modules, package):
    unreachable = check_modules.find_unreachable(package, "app", ["app.main"])

    assert names(unreachable, package) == {"app/dead.py"}


def test_lazy_and_type_checking_imports_count_as_reachable(check_modules, package):
    write(
        package / "app" / "main.py",
        '"""Entry."""\n\nfrom typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n'
        "    from app.live import value\n\n\n"
        "def late():\n    import app.helper\n    from app.dead import x\n",
    )

    assert check_modules.find_unreachable(package, "app", ["app.main"]) == []


def test_module_only_a_test_would_import_is_reported(check_modules, package):
    write(package / "app" / "only_tests.py", '"""Imported by tests only."""\n')

    unreachable = check_modules.find_unreachable(package, "app", ["app.main"])

    assert "app/only_tests.py" in names(unreachable, package)


def test_missing_and_blank_docstrings_are_reported(check_modules, package):
    write(package / "app" / "plain.py", "value = 1\n")
    write(package / "app" / "blank_doc.py", '""" """\n\nvalue = 1\n')

    missing = check_modules.find_missing_docstrings(
        check_modules.module_files(package, "app").values()
    )

    assert names(missing, package) == {"app/plain.py", "app/blank_doc.py"}


def test_empty_package_markers_do_not_need_a_docstring(check_modules, package):
    missing = check_modules.find_missing_docstrings(
        check_modules.module_files(package, "app").values()
    )

    assert missing == []


def test_strict_mode_fails_on_findings_and_report_mode_does_not(
    check_modules, package, monkeypatch
):
    target = check_modules.Target(package, "app", ("app.main",))
    monkeypatch.setattr(check_modules, "TARGETS", {"backend": target})
    monkeypatch.setattr(check_modules, "ROOT", package.parent)

    assert check_modules.main(["--only", "backend"]) == 0
    assert check_modules.main(["--only", "backend", "--strict"]) == 1


def test_allowed_module_is_reported_but_does_not_fail_strict_mode(
    check_modules, package, monkeypatch, capsys
):
    target = check_modules.Target(package, "app", ("app.main",))
    monkeypatch.setattr(check_modules, "TARGETS", {"backend": target})
    monkeypatch.setattr(check_modules, "ROOT", package.parent)
    monkeypatch.setattr(
        check_modules, "ALLOWED_UNREACHABLE", {"src/app/dead.py": "wired by a later deployment"}
    )

    assert check_modules.main(["--only", "backend", "--strict"]) == 0
    assert "allowed: src/app/dead.py (wired by a later deployment)" in capsys.readouterr().out


def test_clean_package_passes_strict_mode(check_modules, package, monkeypatch):
    (package / "app" / "dead.py").unlink()
    target = check_modules.Target(package, "app", ("app.main",))
    monkeypatch.setattr(check_modules, "TARGETS", {"backend": target})
    monkeypatch.setattr(check_modules, "ROOT", package.parent)

    assert check_modules.main(["--only", "backend", "--strict"]) == 0
