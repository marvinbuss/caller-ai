"""Versioned credential scanning; returns field/rule metadata, never matched text."""

import re
from typing import TypedDict
from urllib.parse import parse_qs, urlsplit

from pydantic import Field

from management_app.models.capabilities import ConfigurationModel
from management_app.models.configuration import ConfigurationGraph

RULE_IDS = frozenset(
    {"private_key_pem", "uri_userinfo", "connection_string", "sas_signature", "bearer_token"}
)
URI = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>\"']+", re.IGNORECASE)
FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
PATH_SCHEMA = ConfigurationGraph.model_json_schema(by_alias=True)
KNOWN_FIELDS = frozenset(
    name
    for schema in [PATH_SCHEMA, *PATH_SCHEMA.get("$defs", {}).values()]
    for name in schema.get("properties", {})
)


class Issue(TypedDict):
    path: str
    code: str
    rule_id: str
    policy_version: int


class PolicyRule(ConfigurationModel):
    id: str
    kind: str
    pattern: str | None = None


class DetectionPolicy(ConfigurationModel):
    version: int
    alias_pattern: str = Field(alias="aliasPattern")
    rules: list[PolicyRule]


def field_path(parent: str, component: str | int) -> str:
    if isinstance(component, int):
        return f"{parent}[{component}]"
    name = component if component in KNOWN_FIELDS and FIELD.fullmatch(component) else "unknown"
    return f"{parent}.{name}" if parent else name


class SecretDetector:
    def __init__(self, policy: dict[str, object], approved_aliases: frozenset[str]) -> None:
        self.policy = DetectionPolicy.model_validate(policy)
        if self.policy.version != 1 or {rule.id for rule in self.policy.rules} != RULE_IDS:
            raise ValueError("unsupported_secret_detection_policy")
        if len(self.policy.rules) != len(RULE_IDS):
            raise ValueError("invalid_secret_detection_policy")
        self.alias = re.compile(self.policy.alias_pattern)
        self.patterns: dict[str, re.Pattern[str]] = {}
        for rule in self.policy.rules:
            if rule.kind == "pattern" and rule.pattern:
                self.patterns[rule.id] = re.compile(rule.pattern, re.IGNORECASE)
            elif (rule.id, rule.kind) not in {
                ("uri_userinfo", "uri_userinfo"),
                ("sas_signature", "uri_signature"),
            }:
                raise ValueError("invalid_secret_detection_policy")
        self.approved_aliases = approved_aliases

    def issue(self, path: str, code: str, rule: str) -> Issue:
        return {
            "path": path or "configuration",
            "code": code,
            "rule_id": rule,
            "policy_version": self.policy.version,
        }

    def reference(self, value: str, path: str) -> list[Issue]:
        alias = value.removeprefix("secret://")
        if not value.startswith("secret://") or not self.alias.fullmatch(alias):
            return [self.issue(path, "secret_reference_invalid", "secret_reference")]
        if alias not in self.approved_aliases:
            return [self.issue(path, "secret_reference_unknown", "secret_reference")]
        return []

    def text(self, value: str, path: str) -> list[Issue]:
        matched = {rule for rule, pattern in self.patterns.items() if pattern.search(value)}
        issues: list[Issue] = []
        for match in URI.finditer(value):
            try:
                parts = urlsplit(match.group())
                if parts.username is not None or parts.password is not None:
                    matched.add("uri_userinfo")
                if any(
                    key.lower() == "sig" and any(values)
                    for key, values in parse_qs(parts.query).items()
                ):
                    matched.add("sas_signature")
                if parts.scheme == "secret":
                    issues.extend(self.reference(match.group(), path))
            except ValueError:
                if "@" in match.group():
                    matched.add("uri_userinfo")
        issues.extend(
            self.issue(path, "credential_literal_prohibited", rule) for rule in sorted(matched)
        )
        return issues

    def scan(self, value: object, path: str = "", *, depth: int = 0) -> list[Issue]:
        if depth > 64:
            return [self.issue(path, "invalid_configuration", "document_depth")]
        if isinstance(value, str):
            return self.text(value, path)
        errors: list[Issue] = []
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = field_path(path, key if isinstance(key, str) else "unknown")
                if isinstance(key, str):
                    errors.extend(self.text(key, child_path))
                errors.extend(self.scan(child, child_path, depth=depth + 1))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                errors.extend(self.scan(child, field_path(path, index), depth=depth + 1))
        return errors
