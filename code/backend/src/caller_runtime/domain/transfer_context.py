"""Volatile pinned-edge context selection; no transcript replay or durable payloads."""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

from caller_runtime.domain.visibility import CommunityVisibility

RULE_IDS = frozenset(
    {"private_key_pem", "uri_userinfo", "connection_string", "sas_signature", "bearer_token"}
)
URI = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>\"']+", re.IGNORECASE)


class TransferContextPolicy:
    """Pure selection over explicit trusted versioned policy; not semantic summarization."""

    def __init__(self, *, policy: Mapping[str, Any]) -> None:
        try:
            rules = policy["rules"]
            if policy["version"] != 1 or not isinstance(rules, list) or len(rules) != len(RULE_IDS):
                raise ValueError("unsupported_policy")
            if {rule["id"] for rule in rules} != RULE_IDS:
                raise ValueError("invalid_policy")
            patterns = []
            for rule in rules:
                if rule["kind"] == "pattern" and isinstance(rule.get("pattern"), str):
                    patterns.append(re.compile(rule["pattern"], re.IGNORECASE))
                elif (rule["id"], rule["kind"]) not in {
                    ("uri_userinfo", "uri_userinfo"),
                    ("sas_signature", "uri_signature"),
                }:
                    raise ValueError("invalid_policy")
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid_transfer_policy") from None
        self._patterns = tuple(patterns)
        self.policy_version = 1

    def _safe(self, value: str) -> bool:
        if any(pattern.search(value) for pattern in self._patterns):
            return False
        for match in URI.finditer(value):
            try:
                parts = urlsplit(match.group())
                if (
                    parts.scheme == "secret"
                    or parts.username is not None
                    or parts.password is not None
                ):
                    return False
                query = parse_qs(parts.query, max_num_fields=32)
                if any(key.lower() == "sig" and any(values) for key, values in query.items()):
                    return False
            except ValueError:
                return False
        return True

    def filter(
        self,
        visibility: CommunityVisibility,
        source_id: str,
        destination_id: str,
        supplied: Mapping[str, Any],
    ) -> dict[str, str]:
        allowed = visibility.allowed_context_fields(source_id, destination_id)
        selected: dict[str, str] = {}
        if not isinstance(supplied, Mapping) or len(supplied) > 32:
            raise ValueError("invalid_transfer_context")
        for field in ("request_summary", "transfer_reason"):
            if field not in allowed or field not in supplied:
                continue
            value = supplied[field]
            try:
                if not isinstance(value, str) or len(value.encode("utf-8")) > 4096:
                    raise ValueError("invalid_transfer_context")
            except (UnicodeError, ValueError):
                raise ValueError("invalid_transfer_context") from None
            if not self._safe(value):
                raise PermissionError("transfer_content_prohibited")
            if value:
                selected[field] = value
        return selected
