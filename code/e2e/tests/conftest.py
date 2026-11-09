"""Environment for the in-process journey: both applications importable, no frontend work."""

import sys
from pathlib import Path

import pytest

try:
    import caller_runtime  # noqa: F401
except ImportError:
    # A local run from the management environment borrows the runtime's source and packages.
    backend = Path(__file__).parents[2] / "backend"
    for candidate in (
        backend / "src",
        backend / ".venv" / "Lib" / "site-packages",
        *(backend / ".venv" / "lib").glob("python*/site-packages"),
    ):
        if candidate.is_dir():
            sys.path.append(str(candidate))


@pytest.fixture(autouse=True)
def reflex_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("__REFLEX_SKIP_COMPILE", "true")
    monkeypatch.setattr(
        "reflex.utils.js_runtimes.install_frontend_packages",
        lambda *arguments, **keywords: pytest.fail("no_frontend_install_in_the_journey"),
    )
