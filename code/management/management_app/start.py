"""Container start: point the exported frontend at the address this deployment is reached on.

The frontend is exported at build time with a placeholder backend address, because the address of a
deployment (its default domain) exists only after the apply. At start this script rewrites that
placeholder in the exported files to `API_URL` (or, without it, `CALLER_PUBLIC_ORIGIN`),
then hands over to the server. Only a plain
`http(s)://host[:port]` origin is accepted; anything else stops the start, so no markup or script
can enter the files. Precompressed siblings are regenerated so a client never gets a stale copy.
"""

import gzip
import os
import re
import sys
from pathlib import Path

ORIGIN = re.compile(r"https?://[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:[0-9]{1,5})?")
SUFFIXES = {".js", ".html", ".json", ".txt", ".xml", ".map"}
APP = Path("/app")
BAKED = APP / "baked-api-url"
CLIENT = APP / ".web" / "build" / "client"
SERVER = (
    "/app/.venv/bin/granian",
    "--interface",
    "asgi",
    "--host",
    "0.0.0.0",
    "--port",
    "3000",
    "--loop",
    "asyncio",
    "--workers",
    "1",
    "--factory",
    "management_app.management_app:create_app_from_environment",
)


def socket_origin(origin: str) -> str:
    return origin.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


def rewrite(root: Path, baked: str, target: str) -> int:
    """Replace the baked origin (and its socket form) in the exported files; returns file count."""
    for value in (baked, target):
        if ORIGIN.fullmatch(value) is None:
            raise ValueError("invalid_frontend_origin")
    pairs = ((baked, target), (socket_origin(baked), socket_origin(target)))
    changed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        updated = text
        for old, new in pairs:
            updated = updated.replace(old, new)
        if updated == text:
            continue
        path.write_text(updated, encoding="utf-8", newline="")
        sibling = path.with_name(path.name + ".gz")
        if sibling.exists():
            sibling.write_bytes(gzip.compress(updated.encode("utf-8"), mtime=0))
        changed += 1
    return changed


def apply(root: Path = CLIENT, baked_file: Path = BAKED, target: str | None = None) -> int:
    if not baked_file.exists():
        return 0
    baked = baked_file.read_text(encoding="utf-8").strip()
    if target is None:
        # The image sets API_URL to the build placeholder; only a real address counts.
        configured = os.environ.get("API_URL", "")
        target = configured if configured and configured != baked else ""
        target = target or os.environ.get("CALLER_PUBLIC_ORIGIN", "")
    if not target or target == baked:
        return 0
    return rewrite(root, baked, target)


def main() -> None:
    try:
        apply()
    except Exception:
        print("frontend_origin_invalid", file=sys.stderr)
        raise SystemExit(2) from None
    os.execv(SERVER[0], SERVER)


if __name__ == "__main__":
    main()
