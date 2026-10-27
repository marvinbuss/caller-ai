"""Check text whitespace without changing source files or printing their content."""

import argparse
import sys
from pathlib import Path


def check_file(path: Path) -> bool:
    content = path.read_bytes()
    if not content:
        return True
    valid = content.endswith(b"\n")
    for line in content.splitlines():
        if line.rstrip(b" \t") != line:
            valid = False
    return valid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("files", nargs="*")
    arguments = parser.parse_args()
    invalid = [name for name in arguments.files if not check_file(Path(name))]
    if arguments.fix:
        for name in invalid:
            path = Path(name)
            content = path.read_bytes()
            ending = b"\r\n" if b"\r\n" in content else b"\n"
            lines = [line.rstrip(b" \t") for line in content.splitlines()]
            path.write_bytes(ending.join(lines) + ending)
        return 0
    for name in invalid:
        print(f"{name}: trailing whitespace or missing final newline", file=sys.stderr)
    return int(bool(invalid))


if __name__ == "__main__":
    raise SystemExit(main())
