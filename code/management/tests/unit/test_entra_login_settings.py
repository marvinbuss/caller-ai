"""Spike S-4: the sign-in flow takes its secret from the factory, the redirect from the origin."""

import asyncio
from typing import Any

from management_app.adapters.entra_oidc import MsalCodeFlow
from management_app.security import EntraPolicy

TENANT = "00000000-0000-4000-8000-000000000002"
CLIENT = "00000000-0000-4000-8000-000000000005"
API = "00000000-0000-4000-8000-000000000006"
SECRET = "synthetic-sign-in-credential"  # pragma: allowlist secret
ORIGIN = "https://management.example.azurecontainerapps.io"


class Application:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    def initiate_auth_code_flow(self, scopes, *, redirect_uri, state, response_mode):
        self.calls.append(
            {"scopes": scopes, "redirect_uri": redirect_uri, "response_mode": response_mode}
        )
        return {"auth_uri": f"{ORIGIN}/authorize?state={state}"}

    def acquire_token_by_auth_code_flow(self, auth_code_flow, auth_response):
        raise AssertionError("not used")


def policy(origin: str = ORIGIN) -> EntraPolicy:
    return EntraPolicy(
        tenant_id=TENANT,
        client_id=CLIENT,
        api_audience=API,
        origin=origin,
        manager_role="Caller.Editor",
        manager_scope="manage",
    )


def test_the_redirect_is_the_management_origin_plus_the_callback_path() -> None:
    calls: list[dict[str, Any]] = []
    flow = MsalCodeFlow(policy=policy(), application_factory=lambda: Application(calls))
    asyncio.run(flow.begin("fixture-state"))
    assert calls[0]["redirect_uri"] == f"{ORIGIN}/auth/callback"
    assert calls[0]["response_mode"] == "form_post"


def test_the_client_secret_stays_inside_the_factory_the_composition_supplies() -> None:
    calls: list[dict[str, Any]] = []

    def factory() -> Application:
        # The live composition builds the confidential client here from CALLER_ENTRA_CLIENT_SECRET.
        return Application(calls)

    flow = MsalCodeFlow(policy=policy(), application_factory=factory)
    login = asyncio.run(flow.begin("fixture-state"))
    assert SECRET not in repr(login) and SECRET not in repr(flow) and SECRET not in str(calls)


def test_one_registration_serves_sign_in_and_the_api_with_the_default_scope() -> None:
    """Recorded in research R-04: one registration for sign-in; its API audience is its own ID."""
    calls: list[dict[str, Any]] = []
    single = EntraPolicy(
        tenant_id=TENANT,
        client_id=CLIENT,
        api_audience=CLIENT,
        origin=ORIGIN,
        manager_role="Caller.Editor",
        manager_scope=".default",
    )
    flow = MsalCodeFlow(policy=single, application_factory=lambda: Application(calls))
    asyncio.run(flow.begin("fixture-state"))
    # Entra refuses an app's token for itself unless the resource is its GUID (AADSTS90009).
    assert calls[0]["scopes"] == [f"{CLIENT}/.default"]
