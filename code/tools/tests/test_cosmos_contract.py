"""Spike S-13: the containers the repositories use match the data-store module (partition keys, TTL)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "code/infra/terraform/modules/data-store/main.tf"
BACKEND = ROOT / "code/backend/src/caller_runtime"
MANAGEMENT = ROOT / "code/management/management_app"

# container -> (partition key path, the source that writes it, the field that holds the key)
REPOSITORY_KEYS = {
    "configuration": ("/organizationId", MANAGEMENT / "adapters/configuration_repository.py", "organizationId"),
    "configuration-audit": ("/dayBucket", MANAGEMENT / "adapters/audit_repository.py", "dayBucket"),
    "text-conversations": ("/ownerKey", BACKEND / "adapters/text_history_repository.py", None),
    "call-sessions": ("/id", BACKEND / "adapters/admission_repository.py", "id"),
}


def declared() -> dict[str, tuple[str, str]]:
    text = MODULE.read_text(encoding="utf-8")
    block = re.search(r"containers = \{(.*?)\n  \}", text, re.DOTALL)
    assert block is not None
    return {
        name: (key, ttl.strip())
        for name, key, ttl in re.findall(
            r'"([a-z-]+)"\s*=\s*\{ partition_key = "([^"]+)", ttl = ([^}]+)\}', block.group(1)
        )
    }


def test_the_module_declares_exactly_the_containers_the_apps_use() -> None:
    assert set(declared()) == {*REPOSITORY_KEYS, "call-audit"}


@pytest.mark.parametrize("container", sorted(REPOSITORY_KEYS))
def test_each_partition_key_is_the_field_the_repository_writes(container: str) -> None:
    path_value, source, field = REPOSITORY_KEYS[container]
    assert declared()[container][0] == path_value
    text = source.read_text(encoding="utf-8")
    if field is not None:
        assert re.search(rf'["\']{field}["\']', text) or f"{field}=" in text
    else:
        assert "partition_key=owner_key" in text


def test_call_audit_shares_the_audit_partition_key() -> None:
    assert declared()["call-audit"][0] == "/dayBucket"


@pytest.mark.parametrize("container", ["configuration", "text-conversations"])
def test_containers_with_item_level_ttl_enable_it_without_a_default_expiry(
    container: str,
) -> None:
    """Items carry `ttl: -1` or their own TTL, which only counts when the container turns TTL on."""
    assert declared()[container][1] == "-1"


@pytest.mark.parametrize(
    "container,variable",
    [
        ("configuration-audit", "var.configuration_audit_ttl"),
        ("call-sessions", "var.call_session_ttl"),
        ("call-audit", "var.call_audit_ttl"),
    ],
)
def test_expiring_containers_take_their_ttl_from_the_variable_file(
    container: str, variable: str
) -> None:
    assert declared()[container][1] == variable


def test_the_configuration_repository_writes_item_ttl_the_container_must_honour() -> None:
    text = (MANAGEMENT / "adapters/configuration_repository.py").read_text(encoding="utf-8")
    assert '"ttl": -1' in text and 'body["ttl"] = retention_seconds' in text
    audit = (MANAGEMENT / "adapters/audit_repository.py").read_text(encoding="utf-8")
    assert '"ttl": self._retention' in audit
    history = (BACKEND / "adapters/text_history_repository.py").read_text(encoding="utf-8")
    assert 'body["ttl"] = self._ttl' in history
