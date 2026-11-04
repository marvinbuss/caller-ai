"""Contract of the shared connection module: settings, public errors and limits."""

import re
from pathlib import Path

import pytest

from caller_runtime.api import connection

SOURCE = Path(__file__).resolve().parents[2] / "src" / "caller_runtime"
RAISED = re.compile(r"(?:AdmissionError|PermissionError)\(\s*\"([a-z_]+)\"")
LIVE_MODULES = (
    "api/text.py",
    "api/voice.py",
    "api/test_sessions.py",
    "services/conversation.py",
    "services/delegation.py",
    "services/admission.py",
    "services/admission.py",
    "adapters/identity.py",
    "adapters/identity.py",
)


@pytest.mark.parametrize("value", [None, {}])
def test_absent_or_empty_settings_are_accepted(value):
    assert connection.parse_settings(value) == connection.ConnectionSettings()


@pytest.mark.parametrize(
    "value",
    [
        {"language": "en"},
        {"voice": "alloy"},
        {"revision_id": "revision-1"},
        {"identity": "someone"},
        {"capability": "send_mail"},
        {"credential": "token"},
        {"nested": {}},
        [],
        ["x"],
        "settings",
        1,
        True,
    ],
)
def test_any_other_settings_value_is_rejected(value):
    with pytest.raises(PermissionError, match="settings_not_supported"):
        connection.parse_settings(value)


def test_each_public_code_has_one_status_and_one_distinct_close_code():
    codes = set(connection.HTTP_STATUS)

    assert codes == set(connection.CLOSE_CODE)
    assert len(set(connection.CLOSE_CODE.values())) == len(codes)
    assert all(4000 <= close < 5000 for close in connection.CLOSE_CODE.values())
    assert connection.HTTP_STATUS == {
        "unauthorized": 401,
        "forbidden": 403,
        "settings_not_supported": 422,
        "limit_exceeded": 413,
        "capacity_unavailable": 503,
        "invalid_request": 400,
        "upstream_unavailable": 502,
        "internal_error": 500,
    }


@pytest.mark.parametrize(
    ("internal", "code", "reason"),
    [
        ("invalid_test_permit", "unauthorized", "permit_invalid"),
        ("permit_expired", "unauthorized", "permit_expired"),
        ("permit_replayed", "unauthorized", "permit_replayed"),
        ("session_expired", "unauthorized", "session_expired"),
        ("admission_deadline_exceeded", "unauthorized", "permit_expired"),
        ("mode_mismatch", "forbidden", "mode_mismatch"),
        ("stale_owner", "forbidden", "session_stale"),
        ("conversation_context_limit", "limit_exceeded", "conversation_context_limit"),
        ("capacity_unavailable", "capacity_unavailable", "capacity_unavailable"),
        ("work_busy", "capacity_unavailable", "work_busy"),
        ("manager_busy", "capacity_unavailable", "manager_busy"),
        ("invalid_control", "invalid_request", "invalid_message"),
        ("conversation_not_found", "invalid_request", "conversation_not_found"),
        ("history_unavailable", "upstream_unavailable", "history_unavailable"),
        ("authority_unavailable", "upstream_unavailable", "authority_unavailable"),
        ("settlement_conflict", "internal_error", None),
    ],
)
def test_internal_codes_map_to_a_public_code_and_fixed_reason(internal, code, reason):
    error = connection.public_error(internal)

    assert (error.code, error.reason) == (code, reason)
    assert error.status == connection.HTTP_STATUS[code]
    assert error.close_code == connection.CLOSE_CODE[code]


def test_unlisted_internal_code_is_internal_error_without_reason_or_detail():
    error = connection.public_error("some_new_private_detail")

    assert (error.code, error.reason, error.status) == ("internal_error", None, 500)
    assert error.body() == {"error": {"code": "internal_error"}}


def test_body_carries_only_code_and_reason():
    assert connection.public_error("work_busy").body() == {
        "error": {"code": "capacity_unavailable", "reason": "work_busy"}
    }


def test_authentication_failures_expose_only_the_four_verification_reasons():
    reasons = connection.REASONS["unauthorized"]

    assert reasons == connection.AUTHENTICATION_REASONS


def test_every_reason_belongs_to_the_closed_list_of_its_public_code():
    for internal, error in connection.INTERNAL_ERRORS.items():
        assert error.code in connection.HTTP_STATUS, internal
        if error.reason is not None:
            assert error.reason in connection.REASONS[error.code], internal
    assert connection.REASONS["internal_error"] == frozenset()


def test_every_internal_code_raised_by_live_backend_modules_is_mapped():
    raised: dict[str, set[str]] = {}
    for name in LIVE_MODULES:
        path = SOURCE / name
        if not path.exists():
            continue
        for code in RAISED.findall(path.read_text(encoding="utf-8")):
            raised.setdefault(code, set()).add(path.name)

    # Admission picks this code through a conditional expression the pattern cannot see.
    raised.setdefault("manager_busy", {"test_admission.py"})
    missing = {
        code: sorted(files)
        for code, files in raised.items()
        if code not in connection.INTERNAL_ERRORS
    }

    assert missing == {}


def test_shared_limits_match_the_existing_wire_bounds():
    assert connection.MAX_CONTROL_BYTES == 8192
    assert connection.MAX_TEXT_BYTES == 4096
    assert connection.MAX_REPLY_BYTES == 64 * 1024
    assert connection.MAX_CONTEXT_BYTES == 256 * 1024
    assert connection.ADMISSION_SECONDS == 10
