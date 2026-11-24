"""Probe P3: are replayed tool and handoff items of another agent accepted with `store=False`?

Each run sends one Responses call whose input replays a `function_call` and its
`function_call_output` for a synthetic tool that is not in the request's tools, plus an SDK-style
handoff call and its output, then asks a synthetic question. It records whether the call was
accepted. The summary decides between native rendering and the text fallback of
`contracts/shared-history.md` (R3): native only if every run was accepted.
"""

import asyncio
import sys
from collections.abc import Sequence
from typing import Any

import common

PROBE = "P3"


def request(deployment: str) -> dict[str, Any]:
    return {
        "model": deployment,
        "instructions": common.TEXT_INSTRUCTIONS,
        "input": [
            {"role": "user", "content": common.TEXT_PROMPT},
            {
                "type": "function_call",
                "call_id": "call_probe_tool",
                "name": common.FOREIGN_TOOL_NAME,
                "arguments": common.FOREIGN_TOOL_ARGUMENTS,
            },
            {
                "type": "function_call_output",
                "call_id": "call_probe_tool",
                "output": common.FOREIGN_TOOL_OUTPUT,
            },
            {
                "type": "function_call",
                "call_id": "call_probe_handoff",
                "name": common.HANDOFF_NAME,
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_probe_handoff",
                "output": common.HANDOFF_OUTPUT,
            },
            {"role": "user", "content": common.TEXT_PROMPT},
        ],
        "tools": [
            {
                "type": "function",
                "name": common.TOOL_NAME,
                "description": common.TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {"order": {"type": "string"}},
                    "required": ["order"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ],
        "reasoning": {"effort": "low"},
        "store": False,
        "max_output_tokens": 256,
    }


async def run_all(runs: int) -> int:
    from openai import AsyncOpenAI

    settings = common.settings()
    credential = common.credential()
    accepted = errors = 0
    try:
        token = await credential.get_token(common.TOKEN_SCOPE)
        client = AsyncOpenAI(
            api_key=token.token, base_url=settings.text_endpoint + "/openai/v1/", max_retries=0
        )
        try:
            for run in range(runs):
                try:
                    await client.responses.create(**request(settings.text_deployment))
                    accepted += 1
                    common.result(PROBE, run, "passed", {"accepted": True})
                except Exception as error:
                    if not common.refused(error):
                        errors += 1
                        common.result(PROBE, run, "error", {"code": common.failure_code(error)})
                    else:
                        common.result(
                            PROBE,
                            run,
                            "failed",
                            {"accepted": False, "code": common.failure_code(error)},
                        )
        finally:
            await client.close()
    finally:
        await credential.close()
    decided = "native" if accepted == runs else "text_fallback"
    common.result(
        PROBE,
        "total",
        "summary",
        {
            "runs": runs,
            "accepted": accepted,
            "errors": errors,
            "rendering": decided if errors == 0 else "undecided",
        },
    )
    return 0 if errors == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    options = common.arguments(__doc__ or PROBE, argv, default_runs=3)
    if options.preview:
        return common.preview(PROBE, ["responses.create.replayed_items"])
    return asyncio.run(run_all(options.runs))


if __name__ == "__main__":
    sys.exit(main())
