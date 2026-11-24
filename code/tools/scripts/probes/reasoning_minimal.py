"""Probe P2: does the text deployment accept `reasoning.effort = "minimal"` with a function tool?

Each run sends one streamed Responses call with one synthetic function tool and `store=False`:
`--runs` calls with `minimal`, then the same number with `low`. It records whether each call was
accepted and the time to its first output event. The summary decides the default of
`CALLER_REASONING_EFFORT` (R12): `minimal` if every `minimal` call was accepted, otherwise `low`.
"""

import asyncio
import sys
import time
from collections.abc import Sequence
from typing import Any

import common

PROBE = "P2"
EFFORTS = ("minimal", "low")
FIRST_OUTPUT_EVENTS = frozenset(
    {
        "response.output_text.delta",
        "response.function_call_arguments.delta",
        "response.output_item.added",
    }
)


def request(deployment: str, effort: str) -> dict[str, Any]:
    return {
        "model": deployment,
        "instructions": common.TEXT_INSTRUCTIONS,
        "input": common.TEXT_PROMPT,
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
        "reasoning": {"effort": effort},
        "store": False,
        "max_output_tokens": 256,
        "stream": True,
    }


async def run_once(client: Any, deployment: str, effort: str) -> dict[str, Any]:
    started = time.monotonic()
    first: float | None = None
    stream = await client.responses.create(**request(deployment, effort))
    async for event in stream:
        if first is None and getattr(event, "type", None) in FIRST_OUTPUT_EVENTS:
            first = time.monotonic() - started
    return {
        "effort": effort,
        "accepted": True,
        "first_output_seconds": None if first is None else round(first, 3),
    }


async def run_all(runs: int) -> int:
    from openai import AsyncOpenAI

    settings = common.settings()
    credential = common.credential()
    accepted = {effort: 0 for effort in EFFORTS}
    errors = 0
    try:
        token = await credential.get_token(common.TOKEN_SCOPE)
        client = AsyncOpenAI(
            api_key=token.token, base_url=settings.text_endpoint + "/openai/v1/", max_retries=0
        )
        try:
            index = 0
            for effort in EFFORTS:
                for _ in range(runs):
                    try:
                        value = await run_once(client, settings.text_deployment, effort)
                        accepted[effort] += 1
                        common.result(PROBE, index, "passed", value)
                    except Exception as error:
                        if not common.refused(error):
                            errors += 1
                            common.result(
                                PROBE, index, "error", {"code": common.failure_code(error)}
                            )
                        else:
                            common.result(
                                PROBE,
                                index,
                                "failed",
                                {
                                    "effort": effort,
                                    "accepted": False,
                                    "code": common.failure_code(error),
                                },
                            )
                    index += 1
        finally:
            await client.close()
    finally:
        await credential.close()
    decided = "minimal" if accepted["minimal"] == runs else "low"
    common.result(
        PROBE,
        "total",
        "summary",
        {
            "runs_per_effort": runs,
            "minimal_accepted": accepted["minimal"],
            "low_accepted": accepted["low"],
            "errors": errors,
            "reasoning_effort_default": decided if errors == 0 else "undecided",
        },
    )
    return 0 if errors == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    options = common.arguments(__doc__ or PROBE, argv, default_runs=10)
    if options.preview:
        return common.preview(PROBE, [f"responses.create.{effort}" for effort in EFFORTS])
    return asyncio.run(run_all(options.runs))


if __name__ == "__main__":
    sys.exit(main())
