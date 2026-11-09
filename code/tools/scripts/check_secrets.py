"""Offline secret scan over the files named on the command line.

Exits 0 only when every file exists and has no finding. A missing file or an empty file list exits 1
and claims no clean result. Failure output is the file, line and rule type only, never the
matched text.
"""

import json
import subprocess
import sys
from pathlib import Path

# The reviewed settings fingerprint is a public SHA-256 digest that the app compares at start.
REVIEWED_FINGERPRINT_LINE = r'^\s*"reviewed_fingerprint": "[0-9a-f]{64}",?$'


def main() -> int:
    if not sys.argv[1:] or not all(Path(name).is_file() for name in sys.argv[1:]):
        print("Secret scan requires existing files; no clean result is claimed.", file=sys.stderr)
        return 1
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "detect_secrets.pre_commit_hook",
            "--no-verify",
            "--json",
            "--exclude-lines",
            REVIEWED_FINGERPRINT_LINE,
            *sys.argv[1:],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return 0
    if result.returncode == 1:
        try:
            findings = json.loads(result.stdout)["results"]
            for filename, matches in findings.items():
                for match in matches:
                    print(f"{filename}:{match['line_number']}: {match['type']}", file=sys.stderr)
        except (ValueError, KeyError, TypeError):
            print("Secret scanner returned an invalid result.", file=sys.stderr)
    else:
        print("Secret scanner could not complete; no clean result is claimed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
