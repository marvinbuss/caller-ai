"""Shared parts of the feature 007 int probes: settings, credential, synthetic texts and results.

The probes answer model-behavior questions P1-P3 of
`specs/007-shared-conversation-history/research.md` (R15) before implementation. They read the
same `CALLER_*` endpoint and deployment variables as the runtime and authenticate with
`DefaultAzureCredential`, never with keys. They run in the runtime's virtual environment, which
has the OpenAI and Azure Identity packages, from the repository root:

    code/backend/.venv/Scripts/python.exe code/tools/scripts/probes/<probe>.py [--preview]

A private model account (int) is reachable only from its network: there the probes run inside the
runtime container with `/app/.venv/bin/python` (see the feature quickstart).

`--preview` prints the planned calls and makes no network call, so it also runs in the tools
environment. Every printed line is one closed JSON result: `probe`, `run`, `outcome` and `value`,
where `value` holds only numbers, booleans, null or fixed codes. Provider text is never printed.
Every conversation text sent to a model is one of the synthetic constants below; P1 starts its
session with the shipped voice conversation instructions, which hold no conversation content.
"""

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

TOKEN_SCOPE = "https://ai.azure.com/.default"
OUTCOMES = frozenset({"planned", "passed", "failed", "error", "summary"})
CODE = re.compile(r"[a-z][a-z0-9_.]{0,63}")
APPROVED_SUFFIXES = (".openai.azure.com", ".services.ai.azure.com")
VOICE = r"[a-z][a-z0-9_-]{0,63}"
DEPLOYMENT = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"

# Synthetic texts only. No conversation content, configuration or user data reaches a model.
LONG_COMMENTARY = (
    "Here is the synthetic status summary for this test. The first item is ready. The second "
    "item is waiting for a review. The third item was moved to next week. The fourth item has "
    "two open questions about the delivery date. The fifth item is complete and needs no more "
    "work. The sixth item is a placeholder that exists only for this test. Every number and "
    "every item in this summary is synthetic."
)
DELEGATION_UPDATE = (
    "Delegation update. This replaces all earlier delegation updates.\n"
    "Backend tools:\n"
    "- Synthetic invoices and payments for this test.\n"
    "- Synthetic technical faults for this test.\n"
    "Generate <|SpawnThinking|> when the request needs one of these capabilities.\n"
    "If no listed capability fits, say briefly that this service cannot help with it and "
    "offer what it can do."
)
TEXT_INSTRUCTIONS = "You are an assistant in a synthetic test. Answer in one short sentence."
TEXT_PROMPT = "Synthetic test: what is the status of the synthetic order A-100?"
TOOL_NAME = "check_synthetic_status"
TOOL_DESCRIPTION = "Returns the status of a synthetic order in this test."
FOREIGN_TOOL_NAME = "lookup_synthetic_order"
FOREIGN_TOOL_ARGUMENTS = '{"order": "A-100"}'
FOREIGN_TOOL_OUTPUT = '{"status": "shipped"}'
HANDOFF_NAME = "transfer_to_synthetic_billing"
HANDOFF_OUTPUT = '{"assistant": "Synthetic billing"}'
SYNTHETIC_TEXTS = frozenset(
    {
        LONG_COMMENTARY,
        DELEGATION_UPDATE,
        TEXT_INSTRUCTIONS,
        TEXT_PROMPT,
        TOOL_DESCRIPTION,
        FOREIGN_TOOL_ARGUMENTS,
        FOREIGN_TOOL_OUTPUT,
        HANDOFF_OUTPUT,
        "{}",
    }
)


@dataclass(frozen=True)
class ProbeSettings:
    """The runtime's endpoint and deployment settings the probes need."""

    live_endpoint: str
    live_voice: str
    text_endpoint: str
    text_deployment: str


def _origin(name: str, environment: dict[str, str]) -> str:
    value = environment.get(name, "").strip().rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or not parts.hostname.endswith(APPROVED_SUFFIXES)
        or parts.path
        or parts.query
        or parts.username
    ):
        raise SystemExit(f"{name} must be the https origin of an Azure AI endpoint")
    return value


def _value(name: str, environment: dict[str, str], pattern: str) -> str:
    value = environment.get(name, "").strip()
    if re.fullmatch(pattern, value) is None:
        raise SystemExit(f"{name} is missing or invalid")
    return value


def settings(environment: dict[str, str] | None = None) -> ProbeSettings:
    """Read the runtime variables; values are never printed."""
    values = dict(os.environ) if environment is None else environment
    return ProbeSettings(
        live_endpoint=_origin("CALLER_LIVE_ENDPOINT", values),
        live_voice=_value("CALLER_LIVE_VOICE", values, VOICE),
        text_endpoint=_origin("CALLER_TEXT_ENDPOINT", values),
        text_deployment=_value("CALLER_TEXT_DEPLOYMENT", values, DEPLOYMENT),
    )


def credential() -> Any:
    """`DefaultAzureCredential` from the runtime environment; imported only for real runs."""
    from azure.identity.aio import DefaultAzureCredential

    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def _checked(value: Any) -> Any:
    if isinstance(value, dict):
        if not all(isinstance(key, str) and CODE.fullmatch(key) for key in value):
            raise ValueError("probe_value_key_invalid")
        return {key: _checked(item) for key, item in value.items()}
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str) and CODE.fullmatch(value):
        return value
    raise ValueError("probe_value_not_closed")


def result(
    probe: str, run: int | str, outcome: str, value: Any, write: Callable[[str], Any] | None = None
) -> dict[str, Any]:
    """Print one closed result line and return it."""
    if outcome not in OUTCOMES or not re.fullmatch(r"P[1-4]", probe):
        raise ValueError("probe_result_invalid")
    if not (isinstance(run, int) and run >= 0) and run != "total":
        raise ValueError("probe_run_invalid")
    line = {"probe": probe, "run": run, "outcome": outcome, "value": _checked(value)}
    (write or (lambda text: print(text, flush=True)))(json.dumps(line, sort_keys=True))
    return line


def arguments(
    description: str, argv: Sequence[str] | None, default_runs: int, control: bool = False
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--preview", action="store_true", help="print the planned calls, make no network call"
    )
    parser.add_argument("--runs", type=int, default=default_runs, choices=range(1, 21))
    if control:
        parser.add_argument(
            "--control", action="store_true", help="run without the step under test, as a baseline"
        )
    return parser.parse_args(argv)


def preview(probe: str, calls: Sequence[str]) -> int:
    for index, call in enumerate(calls):
        result(probe, index, "planned", {"call": call})
    return 0


def failure_code(error: BaseException) -> str:
    """A fixed code for a failed call; provider messages are never printed."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return f"http_{status}"
    if isinstance(error, RuntimeError) and CODE.fullmatch(str(error)):
        return str(error)
    return re.sub(r"[^a-z0-9_]", "_", type(error).__name__.lower())[:64] or "error"


def refused(error: BaseException) -> bool:
    """Only a 400 answers the probe question; 401, 403, 429 or 5xx say nothing about it."""
    return getattr(error, "status_code", None) == 400


if __name__ == "__main__":
    sys.exit("Run one of the probe scripts in this directory.")
