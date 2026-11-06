"""Build-only stub that lets the frontend export register the editor and test console pages.

The export runs without any deployment, so it cannot use the live composition. This stub gives the
pages the services they are registered with, built from fixed placeholders: a policy for an
address that does not resolve, a key set that always refuses, and no credential of any kind. The
resulting app is used only to produce the static frontend and is never served (the container
starts `create_app_from_environment`, which composes the real services at start).
"""

from typing import Any

from management_app.health import create_health_api
from management_app.reflex_security import ReflexEventGuard
from management_app.security import EntraPolicy, IdentityService, SessionStore, TokenVerifier
from management_app.settings import Settings
from management_app.state.editor_draft import EditorServices

PLACEHOLDER_ID = "00000000-0000-4000-8000-00000000e000"


class RefusingKeys:
    async def signing_keys(self) -> dict[str, Any]:
        raise RuntimeError("export_stub_has_no_identity")


def export_editor(settings: Settings | None = None) -> EditorServices:
    policy = EntraPolicy(
        tenant_id=PLACEHOLDER_ID,
        client_id=PLACEHOLDER_ID.replace("e000", "e001"),
        api_audience=PLACEHOLDER_ID.replace("e000", "e001"),
        origin="https://export.invalid",
        manager_role="Export.Stub",
        manager_scope=".default",
    )
    identity = IdentityService(
        policy=policy,
        verifier=TokenVerifier(policy=policy, keys=RefusingKeys()),
        sessions=SessionStore(policy=policy),
    )
    api = create_health_api(settings or Settings())
    return EditorServices(api=api, guard=ReflexEventGuard(identity=identity))
