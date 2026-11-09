"""Runs the Pester 5 tests of the identity script; CI calls this so every step starts with uv."""

import shutil
import subprocess
import sys
from pathlib import Path

from ci import ROOT

TESTS = "code/infra/scripts/identity/tests"
COMMAND = (
    "Import-Module Pester -MinimumVersion 5.0.0; "
    "$c = New-PesterConfiguration; "
    f"$c.Run.Path = '{TESTS}'; $c.Run.Exit = $true; $c.Output.Verbosity = 'Normal'; "
    "Invoke-Pester -Configuration $c"
)


def main(root: Path = ROOT) -> int:
    powershell = shutil.which("pwsh")
    if powershell is None:
        print("pester_requires_powershell_7", file=sys.stderr)
        return 2
    return subprocess.run(
        [powershell, "-NoProfile", "-Command", COMMAND], cwd=root, check=False, timeout=900
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
