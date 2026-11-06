"""Prints the fingerprint of the non-secret settings a deployment would give (research R-11).

The owner reviews those settings, records the printed value as `reviewed_fingerprint` in the
variable file and applies; the app then starts only when its own computation matches. Input is a
JSON object of `CALLER_*` names to values (`--settings FILE`) or, without it, the environment.
Secrets, the review reference and the reviewed fingerprint are ignored, and nothing is printed but
the fingerprint. Exit codes: 0 printed, 2 invalid input (one fixed reason code on stderr).
"""

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from management_app.live import reviewed_fingerprint
from management_app.settings import Settings, StartupError

IGNORED = frozenset(
    {"CALLER_ENTRA_CLIENT_SECRET", "CALLER_SIGNING_KEY_PEM", "CALLER_REVIEWED_FINGERPRINT"}
)


def fingerprint_of(values: Mapping[str, str]) -> str:
    """Fingerprint for a mapping of setting names to values; raises `StartupError`."""
    fields: dict[str, str] = {}
    for name, value in values.items():
        if name in IGNORED or not isinstance(value, str):
            continue
        if name == "AZURE_CLIENT_ID":
            fields[name] = value
        elif name.startswith("CALLER_"):
            fields[name.removeprefix("CALLER_").lower()] = value
    fields.pop("mode", None)
    fields["review_reference"] = "pending"  # a reviewer holds no reference before the apply
    try:
        settings = Settings.model_validate({"mode": "live", **fields})
    except Exception:
        raise StartupError("settings_invalid") from None
    live = settings.live(reviewable=True)
    from management_app.live import management_request

    management_request(live)
    return reviewed_fingerprint(live)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments[:1] == ["--settings"] and len(arguments) == 2:
            loaded = json.loads(Path(arguments[1]).read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise StartupError("settings_invalid")
            values: Mapping[str, str] = loaded
        elif not arguments:
            values = dict(os.environ)
        else:
            raise StartupError("settings_invalid")
        print(fingerprint_of(values))
        return 0
    except StartupError as error:
        print(error.code, file=sys.stderr)
        return 2
    except Exception:
        print("settings_invalid", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
