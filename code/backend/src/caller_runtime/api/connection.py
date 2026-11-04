"""Shared contract of the text and voice conversation endpoints: limits, settings and errors.

Both endpoints verify the workload credential in `Authorization` and the signed start permit the
console minted, accept the same optional `settings` object, and report failures through one
vocabulary. Internal admission and conversation codes stay in telemetry; callers only see a public
code plus a fixed reason, so verification and storage detail is never revealed.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import HTTPConnection

from caller_runtime.domain.conversation import Metadata, StartPermit
from caller_runtime.services.admission import AdmissionError

MAX_CONTROL_BYTES = 8192
MAX_TEXT_BYTES = 4096
MAX_REPLY_BYTES = 64 * 1024
MAX_CONTEXT_BYTES = 256 * 1024
FIRST_MESSAGE_SECONDS = 3
ADMISSION_SECONDS = 10

HTTP_STATUS = {
    "unauthorized": 401,
    "forbidden": 403,
    "settings_not_supported": 422,
    "limit_exceeded": 413,
    "capacity_unavailable": 503,
    "invalid_request": 400,
    "upstream_unavailable": 502,
    "internal_error": 500,
}
# WebSocket close codes in the application range, one per public code.
CLOSE_CODE = {
    "unauthorized": 4401,
    "forbidden": 4403,
    "settings_not_supported": 4422,
    "limit_exceeded": 4413,
    "capacity_unavailable": 4503,
    "invalid_request": 4400,
    "upstream_unavailable": 4502,
    "internal_error": 4500,
}
AUTHENTICATION_REASONS = frozenset(
    {"permit_invalid", "permit_expired", "permit_replayed", "session_expired"}
)


class WorkloadIdentity(Protocol):
    """Verifies the calling workload from the connection, for HTTP requests and WebSockets."""

    async def require_runtime(self, request: HTTPConnection) -> str: ...


class PermitVerifier(Protocol):
    async def verify(self, raw: str) -> StartPermit: ...


async def verify_permit(permits: PermitVerifier, raw: str) -> StartPermit:
    """Verify a permit; every verification failure looks the same so no detail can leak."""
    try:
        return await permits.verify(raw)
    except PermissionError:
        raise PermissionError("invalid_test_permit") from None


@dataclass(frozen=True, slots=True)
class PublicError:
    code: str
    reason: str | None = None

    @property
    def status(self) -> int:
        return HTTP_STATUS[self.code]

    @property
    def close_code(self) -> int:
        return CLOSE_CODE[self.code]

    def body(self) -> dict[str, dict[str, str]]:
        error = {"code": self.code}
        if self.reason is not None:
            error["reason"] = self.reason
        return {"error": error}


def _group(code: str, reasons: dict[str, str | None]) -> dict[str, PublicError]:
    return {internal: PublicError(code, reason) for internal, reason in reasons.items()}


# Every internal code the live backend raises maps to one public code; a test fails when a code
# raised in the live modules is missing, and unlisted codes fall back to `internal_error`.
INTERNAL_ERRORS: dict[str, PublicError] = {
    **_group(
        "unauthorized",
        {
            "invalid_test_permit": "permit_invalid",
            "unverified_workload": "permit_invalid",
            "workload_identity_changed": "permit_invalid",
            "permit_expired": "permit_expired",
            "admission_deadline_exceeded": "permit_expired",
            "permit_replayed": "permit_replayed",
            "session_expired": "session_expired",
            "conversation_expired": "session_expired",
        },
    ),
    **_group(
        "forbidden",
        {
            "mode_mismatch": "mode_mismatch",
            "handover_not_allowed": "handover_not_allowed",
            "insecure_transport": "insecure_transport",
            "terminal": "session_terminal",
            "stale_owner": "session_stale",
            "stale_generation": "session_stale",
            "stale_agent_epoch": "session_stale",
            "stale_attempt": "session_stale",
            "stale_work": "session_stale",
        },
    ),
    **_group(
        "settings_not_supported",
        {"settings_not_supported": None},
    ),
    **_group(
        "limit_exceeded",
        {
            "control_limit": "control_limit",
            "audio_frame_limit": "frame_limit",
            "conversation_context_limit": "conversation_context_limit",
            "text_reply_limit": "text_reply_limit",
            "transcript_limit": "transcript_limit",
            "capture_credit_exhausted": "audio_credit_exhausted",
            "playback_credit_exhausted": "audio_credit_exhausted",
            "audio_sequence_exhausted": "sequence_exhausted",
            "server_sequence_exhausted": "sequence_exhausted",
            "generation_exhausted": "sequence_exhausted",
            "work_deadline_exceeded": "work_deadline_exceeded",
            "work_step_limit": "work_limit",
            "work_history_limit": "work_limit",
        },
    ),
    **_group(
        "capacity_unavailable",
        {
            "capacity_unavailable": "capacity_unavailable",
            "work_busy": "work_busy",
            "work_queue_full": "work_busy",
            "manager_busy": "manager_busy",
            "control_queue_full": "work_busy",
            "pre_auth_full": "capacity_unavailable",
            "actor_pre_auth_full": "capacity_unavailable",
            "provider_session_conflict": "capacity_unavailable",
        },
    ),
    **_group(
        "invalid_request",
        {
            "invalid_control": "invalid_message",
            "unsupported_control": "invalid_message",
            "invalid_sequence": "invalid_message",
            "invalid_work_input": "invalid_message",
            "invalid_delegation": "invalid_message",
            "invalid_transcript": "invalid_message",
            "invalid_audio_frame": "invalid_audio",
            "invalid_audio_sequence": "invalid_audio",
            "invalid_played_ack": "invalid_audio",
            "invalid_output_generation": "invalid_audio",
            "audio_clock_invalid": "invalid_audio",
            "work_deadline_required": "invalid_message",
            "clarification_required": "invalid_message",
            "conversation_not_ready": "not_ready",
            "session_not_ready": "not_ready",
            "conversation_not_found": "conversation_not_found",
        },
    ),
    **_group(
        "upstream_unavailable",
        {
            "authority_unavailable": "authority_unavailable",
            "configuration_unavailable": "configuration_unavailable",
            "clock_unavailable": "clock_unavailable",
            "live_unavailable": "model_unavailable",
            "conversation_unavailable": "model_unavailable",
            "capability_unavailable": "capability_unavailable",
            "test_runtime_unavailable": "runtime_unavailable",
            "speech_transition_unavailable": "runtime_unavailable",
            "history_unavailable": "history_unavailable",
            "work_unavailable": "runtime_unavailable",
        },
    ),
    # Integrity and cleanup faults are the server's concern; callers see no detail.
    **_group(
        "internal_error",
        {
            name: None
            for name in (
                "attempt_already_dispatched",
                "authority_conflict",
                "cleanup_authority_required",
                "cleanup_observation_capacity",
                "cleanup_owner_already_registered",
                "closure_evidence_required",
                "foreign_cleanup_observation",
                "foreign_cleanup_owner",
                "invalid_cleanup_evidence",
                "invalid_cleanup_observation",
                "invalid_server_control",
                "live_segment_not_closed",
                "settlement_conflict",
                "stale_cleanup_observation",
                "work_queue_closed",
            )
        },
    ),
}
REASONS: dict[str, frozenset[str]] = {
    code: frozenset(
        error.reason for error in INTERNAL_ERRORS.values() if error.code == code and error.reason
    )
    for code in HTTP_STATUS
}


def public_error(internal: str) -> PublicError:
    return INTERNAL_ERRORS.get(internal, PublicError("internal_error"))


def public_error_for(error: BaseException) -> PublicError:
    """Classify whatever a request or session raised; unknown failures expose nothing."""
    if isinstance(error, AdmissionError):
        return public_error(error.code)
    if isinstance(error, HTTPException):
        return public_error("unverified_workload")
    if isinstance(error, ValidationError):
        return public_error("invalid_control")
    if isinstance(error, (PermissionError, TimeoutError, ValueError)):
        if error.args and isinstance(error.args[0], str):
            return public_error(error.args[0])
        if isinstance(error, TimeoutError):
            return public_error("test_runtime_unavailable")
    return PublicError("internal_error")


class ConnectionSettings(Metadata):
    """Reserved extension point; no setting exists yet, so every key is rejected."""


def parse_settings(value: Any) -> ConnectionSettings:
    """Accept an absent or empty `settings` object and reject anything else."""
    if value is None:
        return ConnectionSettings()
    try:
        return ConnectionSettings.model_validate(value)
    except ValidationError:
        raise PermissionError("settings_not_supported") from None
