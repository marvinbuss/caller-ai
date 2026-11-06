"""The runtime verifies the management workload token against its own registration (FR-052)."""

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from caller_runtime.adapters.workload_identity import EntraWorkloadIdentity

TENANT = "00000000-0000-4000-8000-000000000002"
AUDIENCE = "00000000-0000-4000-8000-000000000008"
PRINCIPAL = "00000000-0000-4000-8000-000000000031"
ROLE = "Management.Caller"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks(private_key: rsa.RSAPrivateKey, kid: str = "key-1") -> dict[str, Any]:
    document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    return {"keys": [{**document, "kid": kid, "use": "sig"}]}


def token(private_key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "iss": f"https://sts.windows.net/{TENANT}/",
        "aud": f"api://{AUDIENCE}",
        "tid": TENANT,
        "oid": PRINCIPAL,
        "roles": [ROLE],
        "exp": int(time.time()) + 600,
    }
    claims.update(overrides)
    headers = {"kid": "key-1"}
    return jwt.encode(
        {key: value for key, value in claims.items() if value is not None},
        private_key,
        algorithm="RS256",
        headers=headers,
    )


def request(value: str | None) -> Any:
    headers = [] if value is None else [("authorization", value)]

    class Headers:
        def getlist(self, name: str) -> list[str]:
            return [item for key, item in headers if key == name]

    return SimpleNamespace(headers=Headers())


def verifier(private_key: rsa.RSAPrivateKey) -> EntraWorkloadIdentity:
    async def keys() -> dict[str, Any]:
        return jwks(private_key)

    return EntraWorkloadIdentity(
        tenant_id=TENANT, audience=AUDIENCE, role=ROLE, principal_id=PRINCIPAL, signing_keys=keys
    )


def verify(private_key: rsa.RSAPrivateKey, header: str | None) -> str:
    return asyncio.run(verifier(private_key).require_runtime(request(header)))


def test_a_valid_token_from_the_management_identity_returns_its_principal(private_key) -> None:
    assert verify(private_key, "Bearer " + token(private_key)) == PRINCIPAL


def test_both_audience_forms_and_both_issuers_are_accepted(private_key) -> None:
    assert verify(private_key, "Bearer " + token(private_key, aud=AUDIENCE)) == PRINCIPAL
    issuer = f"https://login.microsoftonline.com/{TENANT}/v2.0"
    assert verify(private_key, "Bearer " + token(private_key, iss=issuer)) == PRINCIPAL


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "api://other"},
        {"iss": "https://sts.windows.net/other/"},
        {"tid": "00000000-0000-4000-8000-0000000000ff"},
        {"oid": "00000000-0000-4000-8000-0000000000ee"},
        {"roles": ["Other.Role"]},
        {"roles": None},
        {"exp": int(time.time()) - 10},
        {"exp": None},
    ],
)
def test_a_token_that_fails_any_check_is_unverified(private_key, overrides) -> None:
    with pytest.raises(PermissionError, match="unverified_workload"):
        verify(private_key, "Bearer " + token(private_key, **overrides))


def test_a_token_signed_with_another_key_is_unverified(private_key) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(PermissionError, match="unverified_workload"):
        verify(private_key, "Bearer " + token(other))


BAD_HEADERS = [None, "", "Basic abc", "Bearer ", "Bearer  x", "Bearer " + "a" * 9000]


@pytest.mark.parametrize("header", BAD_HEADERS)
def test_missing_or_malformed_authorization_is_unverified(private_key, header) -> None:
    with pytest.raises(PermissionError, match="unverified_workload"):
        verify(private_key, header)


def test_a_key_fetch_failure_never_leaks_detail(private_key) -> None:
    async def failing() -> dict[str, Any]:
        raise RuntimeError("synthetic-private-detail")

    identity = EntraWorkloadIdentity(
        tenant_id=TENANT, audience=AUDIENCE, role=ROLE, principal_id=PRINCIPAL, signing_keys=failing
    )
    with pytest.raises(PermissionError) as raised:
        asyncio.run(identity.require_runtime(request("Bearer " + token(private_key))))
    assert "synthetic-private-detail" not in str(raised.value) and raised.value.__cause__ is None


def test_the_configuration_must_be_complete(private_key) -> None:
    async def keys() -> dict[str, Any]:
        return {}

    with pytest.raises(ValueError, match="invalid_workload_identity_configuration"):
        EntraWorkloadIdentity(
            tenant_id="", audience=AUDIENCE, role=ROLE, principal_id=PRINCIPAL, signing_keys=keys
        )
