"""The permit vector: the management signer and the backend verifier agree on one wire format.

The committed vector holds public keys and tokens only (the throwaway private keys were
discarded), so this test checks that a token the management signer signs now has the same header
and claim names as the vector, and that the vector decodes under the management permit model.
"""

import json
import runpy
from datetime import UTC, datetime
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from management_app.models.test_session import StartPermit
from management_app.security import TestPermitSigner

VECTOR = runpy.run_path(str(Path(__file__).parents[3] / "contracts" / "permit_vector.py"))["VECTOR"]
NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)


def test_the_vector_decodes_under_the_management_permit_model() -> None:
    for key_id, entry in VECTOR["keys"].items():
        header = jwt.get_unverified_header(entry["token"])
        assert header == {"alg": "RS256", "typ": "JWT", "kid": key_id}
        claims = jwt.decode(entry["token"], options={"verify_signature": False})
        permit = StartPermit.model_validate_json(json.dumps(claims))
        assert permit.issuer == VECTOR["issuer"] and permit.audience == VECTOR["audience"]


def test_a_fresh_signature_has_the_vectors_header_and_claim_names() -> None:
    key_id = "vector-key-new"
    vector_token = VECTOR["keys"][key_id]["token"]
    vector_claims = jwt.decode(vector_token, options={"verify_signature": False})
    permit = StartPermit.model_validate_json(json.dumps(vector_claims))
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signer = TestPermitSigner(
        issuer=VECTOR["issuer"],
        audience=VECTOR["audience"],
        key_id=key_id,
        signing_key=private,
        verification_keys={key_id: private.public_key()},
        utc_now=lambda: NOW,
        max_clock_skew_seconds=1,
    )
    token = signer.sign(permit)
    assert jwt.get_unverified_header(token) == jwt.get_unverified_header(vector_token)
    assert jwt.decode(token, options={"verify_signature": False}) == vector_claims
