"""The backend verifies the committed permit vector with one to four keys (FR-051)."""

import asyncio
import runpy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from caller_runtime.adapters.identity import TestPermitVerifier

VECTOR = runpy.run_path(str(Path(__file__).parents[3] / "contracts" / "permit_vector.py"))["VECTOR"]
NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
OLD, NEW = "vector-key-old", "vector-key-new"


def public_pem() -> bytes:
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )


def verifier(keys: dict[str, bytes]) -> TestPermitVerifier:
    return TestPermitVerifier(
        issuer=VECTOR["issuer"],
        audience=VECTOR["audience"],
        keys=keys,
        utc_now=lambda: NOW,
        max_clock_skew_seconds=1,
    )


def pem(name: str) -> bytes:
    return VECTOR["keys"][name]["public_key_pem"].encode()


@pytest.mark.parametrize("name", [OLD, NEW])
def test_each_vector_token_verifies_with_its_own_single_key(name: str) -> None:
    permit = asyncio.run(verifier({name: pem(name)}).verify(VECTOR["keys"][name]["token"]))
    assert permit.mode == "text" and permit.request_id == "vector-request-1"


def test_an_old_and_a_new_key_are_both_accepted_during_the_overlap() -> None:
    both = verifier({OLD: pem(OLD), NEW: pem(NEW)})
    for name in (OLD, NEW):
        assert asyncio.run(both.verify(VECTOR["keys"][name]["token"])).tenant_id.endswith("0002")


def test_up_to_four_keys_are_accepted_and_a_fifth_is_not() -> None:
    four = {OLD: pem(OLD), NEW: pem(NEW), "extra-1": public_pem(), "extra-2": public_pem()}
    assert asyncio.run(verifier(four).verify(VECTOR["keys"][NEW]["token"])).mode == "text"
    with pytest.raises(ValueError, match="invalid_test_permit_configuration"):
        verifier({**four, "extra-3": public_pem()})


def test_a_retired_key_no_longer_verifies_its_tokens() -> None:
    with pytest.raises(PermissionError, match="invalid_test_permit"):
        asyncio.run(verifier({NEW: pem(NEW)}).verify(VECTOR["keys"][OLD]["token"]))


def test_a_key_id_bound_to_another_key_never_verifies() -> None:
    swapped = verifier({OLD: pem(NEW), NEW: pem(OLD)})
    for name in (OLD, NEW):
        with pytest.raises(PermissionError, match="invalid_test_permit"):
            asyncio.run(swapped.verify(VECTOR["keys"][name]["token"]))
