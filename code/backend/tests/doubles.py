"""Deterministic Cosmos container double and server-sent-event reader shared by the tests."""

import copy
import json
from dataclasses import dataclass, field

from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError


@dataclass
class ContainerDouble:
    records: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    version: int = 0
    failure: int | None = None
    conflict_on_replace: bool = False
    partition_field: str | None = None

    def fail_if_requested(self) -> None:
        if self.failure:
            raise CosmosHttpResponseError(
                message="sensitive-store-details", status_code=self.failure
            )

    async def create_item(self, body, **keywords):
        self.calls.append(("create", copy.deepcopy(body), keywords))
        self.fail_if_requested()
        if body["id"] in self.records:
            raise CosmosHttpResponseError(status_code=409)
        self.version += 1
        self.records[body["id"]] = {**copy.deepcopy(body), "_etag": f'"cosmos-{self.version}"'}
        return copy.deepcopy(self.records[body["id"]])

    async def read_item(self, item, partition_key, **keywords):
        self.calls.append(("read", item, {"partition_key": partition_key, **keywords}))
        self.fail_if_requested()
        if self.partition_field is None:
            assert item == partition_key
        if item not in self.records or (
            self.partition_field is not None
            and self.records[item].get(self.partition_field) != partition_key
        ):
            raise CosmosHttpResponseError(status_code=404)
        return copy.deepcopy(self.records[item])

    async def replace_item(self, item, body, *, etag, match_condition, **keywords):
        self.calls.append(
            (
                "replace",
                item,
                {
                    "etag": etag,
                    "match_condition": match_condition,
                    "body": copy.deepcopy(body),
                    **keywords,
                },
            )
        )
        self.fail_if_requested()
        assert match_condition == MatchConditions.IfNotModified
        assert "_etag" not in body
        if self.conflict_on_replace or self.records[item]["_etag"] != etag:
            raise CosmosHttpResponseError(status_code=412)
        self.version += 1
        self.records[item] = {**copy.deepcopy(body), "_etag": f'"cosmos-{self.version}"'}
        return copy.deepcopy(self.records[item])


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """Return the `(event, data)` pairs of a server-sent-event body."""
    events = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def model_reply(text: str | None = None, call: tuple[str, dict] | None = None) -> dict:
    """A completed Responses API object shaped like the live reasoning model: a reasoning item,
    then a text message, one function call, or both."""
    output: list[dict] = [
        {
            "type": "reasoning",
            "id": "rs-fixture",
            "summary": [],
            "content": [],
            "encrypted_content": "synthetic-encrypted-reasoning",
        }
    ]
    if text is not None or call is None:
        output.append(
            {
                "type": "message",
                "id": "msg-fixture",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text or "", "annotations": []}],
            }
        )
    if call is not None:
        name, arguments = call
        output.append(
            {
                "type": "function_call",
                "id": "fc-fixture",
                "call_id": "call-fixture-" + name,
                "name": name,
                "arguments": json.dumps(arguments),
                "status": "completed",
            }
        )
    return {
        "id": "resp-fixture",
        "object": "response",
        "created_at": 1793318400,
        "model": "reasoning-fixture",
        "status": "completed",
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 8,
            "output_tokens": 3,
            "total_tokens": 11,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def model_stream(text: str | None = None, call: tuple[str, dict] | None = None) -> list[bytes]:
    """The server-sent events of a streamed Responses API reply."""
    reply = model_reply(text, call)
    events: list[dict] = [{"type": "response.created", "response": {**reply, "output": []}}]
    for index, item in enumerate(reply["output"]):
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        if item["type"] == "message":
            # The live model streams a few characters per delta.
            content = text or ""
            for start in range(0, max(len(content), 1), 4):
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": item["id"],
                        "output_index": index,
                        "content_index": 0,
                        "delta": content[start : start + 4],
                        "logprobs": [],
                    }
                )
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    events.append({"type": "response.completed", "response": reply})
    return [
        ("data: " + json.dumps({**event, "sequence_number": number}) + "\n\n").encode()
        for number, event in enumerate(events)
    ]


def model_capped(reason: str = "max_output_tokens") -> dict:
    """What the live reasoning model returned for a 16-token readiness request: only reasoning."""
    reply = model_reply()
    reply["output"] = reply["output"][:1]
    reply["status"] = "incomplete"
    reply["incomplete_details"] = {"reason": reason}
    reply["usage"]["output_tokens"] = reply["usage"]["output_tokens_details"][
        "reasoning_tokens"
    ] = 16
    return reply


def tool_names(body: dict) -> set[str]:
    """Names of the function tools in a Responses API request body."""
    return {tool["name"] for tool in body.get("tools", []) if tool.get("type") == "function"}


def live_session(start: dict, session_id: str) -> dict:
    """The session object the int Foundry Live endpoint returned for `start` (2026-11-10)."""
    return {
        "id": session_id,
        "expires_at": 1794327781,
        **start["session"],
        "audio": {"format": {"type": "audio/pcm", "rate": 24000}, **start["session"]["audio"]},
        "status": "active",
        "input": [],
        "context_management": {"type": "compaction"},
    }


def live_started(start: dict, session_id: str) -> str:
    return json.dumps(
        {
            "event_id": "event_started",
            "type": "session.started",
            "session": live_session(start, session_id),
        }
    )


def live_closed(start: dict, session_id: str, seconds: float) -> str:
    return json.dumps(
        {
            "type": "session.closed",
            "usage": {"seconds": seconds},
            "reason": "close_requested",
            "session": live_session(start, session_id),
            "event_id": "event_closed",
        }
    )
