"""One-time operator command: replace the protected agents' prompts with the shipped defaults.

Run once in the slice 1 rollout: `python -m management_app.services.apply_default_prompts`.
It reads the active revision, replaces only the Welcome, Routing and Goodbye agents' `prompt`
(found by role) and publishes one new revision through the normal publication path, so the change
gets history and audit. Every other field is copied as stored. When all three prompts already
equal the defaults it publishes nothing, so a repeat run is safe. No release runs it on its own.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Final, Protocol

from management_app.adapters.configuration_repository import (
    Document,
    Publication,
    graph_document,
    verified_revision,
)
from management_app.services import default_prompts

# Actor ids allow only letters, digits, `_` and `-`, like `system-bootstrap`.
ACTOR: Final = "system-apply-default-prompts"
DEFAULTS: Final = {
    "welcome": default_prompts.WELCOME_PROMPT,
    "routing": default_prompts.ROUTING_PROMPT,
    "goodbye": default_prompts.GOODBYE_PROMPT,
}


class PromptRepository(Protocol):
    async def read_active(self) -> Publication | None: ...

    async def publish(
        self, payload: Document, *, if_match: str, actor_id: str, actor_name: str = ""
    ) -> Publication | None: ...


class PublicationAudit(Protocol):
    async def recover_publication(self, committed_revision: Document) -> bool: ...


async def apply_default_prompts(
    repository: PromptRepository, audit: PublicationAudit
) -> int | None:
    """Publish the defaults; return the new revision number, or `None` when already current."""
    current = await repository.read_active()
    if current is None:
        raise RuntimeError("configuration_not_initialized")
    snapshot, etag = current
    graph = graph_document(verified_revision(snapshot))
    changed = False
    for agent in graph["agents"]:
        default = DEFAULTS.get(agent["role"])
        if default is not None and agent["prompt"] != default:
            agent["prompt"] = default
            changed = True
    if not changed:
        return None
    published = await repository.publish(graph, if_match=etag, actor_id=ACTOR)
    if published is None:
        raise RuntimeError("configuration_conflict")
    await audit.recover_publication(published[0])
    number: int = verified_revision(published[0])["revisionNumber"]
    return number


async def _main(policy: Document) -> str:
    from management_app.adapters.audit_repository import CosmosAuditRepository
    from management_app.adapters.azure_clients import AzureClients
    from management_app.adapters.configuration_repository import CosmosConfigurationRepository
    from management_app.live import reviewed_fingerprint
    from management_app.models.capabilities import ApprovedCapabilities
    from management_app.services.audit import AuditService
    from management_app.services.configuration_validation import ConfigurationValidator
    from management_app.settings import Settings

    settings = Settings()
    live = settings.live()
    # The same review gate and validation as the live management app.
    settings.require_fingerprint(reviewed_fingerprint(live))
    clients = AzureClients(live)
    try:
        validator = ConfigurationValidator(
            policy=policy,
            approved_secret_aliases=frozenset(),
            approved_destination_refs=frozenset(),
            reviewed=ApprovedCapabilities(),
        )
        repository = CosmosConfigurationRepository(
            container=clients.container("configuration"), validator=validator
        )
        audit = AuditService(
            repository=CosmosAuditRepository(
                container=clients.container("configuration-audit"),
                retention_seconds=live.audit_retention_seconds,
            )
        )
        number = await apply_default_prompts(repository, audit)
    finally:
        await clients.close()
    return "already current" if number is None else f"published revision {number}"


def main() -> None:
    from management_app.live import POLICY_FILE

    try:
        policy = json.loads(Path(POLICY_FILE).read_text(encoding="utf-8"))
        print(asyncio.run(_main(policy)))
    except Exception as error:
        code = error.args[0] if error.args and isinstance(error.args[0], str) else "failed"
        print(code if code.isidentifier() else "failed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
