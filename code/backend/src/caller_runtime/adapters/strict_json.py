"""Strict JSON object parsing: duplicate keys are an error, never "last one wins"."""

from typing import Any


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate_json_key")
        document[key] = value
    return document
