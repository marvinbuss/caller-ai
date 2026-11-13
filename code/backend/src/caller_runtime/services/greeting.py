"""Fixed greeting inputs and deadlines; the greeting text itself is written by the Welcome agent.

`GreetingState` follows one voice session's greeting: it leaves `pending` once, for `greeted`
(first greeting audio within the deadline), `skipped` (the caller spoke or delegated first) or
`missed` (no first audio by the deadline, or the request failed), and never goes back.
"""

import math
from typing import Final, Literal

VOICE_GREETING_INPUT: Final = (
    "The caller has just connected. Write the greeting you will say: in English, at most two "
    "short sentences, introduce the service and ask how you can help."
)
VOICE_GREETING_SECONDS: Final = 5.0
TEXT_GREETING_INPUT: Final = (
    "The user has just opened the conversation. Greet them in English, introduce the service "
    "in one sentence and ask how you can help."
)
TEXT_FALLBACK_GREETING: Final = "Hello and welcome! How can I help you today?"
TEXT_GREETING_SECONDS: Final = 4.0
# Both modalities target the first greeting output within this time of ready or the open request.
GREETING_TARGET_SECONDS: Final = 5.0

GreetingStatus = Literal["pending", "greeted", "skipped", "missed"]


class GreetingState:
    def __init__(self, ready_at: float, *, deadline: float = VOICE_GREETING_SECONDS) -> None:
        if (
            type(ready_at) not in (int, float)
            or not math.isfinite(ready_at)
            or type(deadline) not in (int, float)
            or not 0 < deadline <= 60
        ):
            raise ValueError("invalid_greeting_state")
        self.ready_at, self.deadline = float(ready_at), float(deadline)
        self.expires_at = self.ready_at + self.deadline
        self.status: GreetingStatus = "pending"
        self.greeted = False
        self.first_user_input_at: float | None = None
        self.text_sent = False

    @property
    def pending(self) -> bool:
        return self.status == "pending"

    def seconds(self, now: float) -> float:
        return max(0.0, now - self.ready_at)

    def sent(self) -> None:
        if self.pending:
            self.text_sent = True

    def output(self, now: float) -> float | None:
        """Mark the first greeting output; return `greeting_seconds` when it is on time."""
        if not self.pending or not self.text_sent or now > self.expires_at:
            return None
        self.status, self.greeted = "greeted", True
        return self.seconds(now)

    def user_input(self, now: float) -> bool:
        """Caller speech or a delegation; return whether this skipped a pending greeting."""
        if self.first_user_input_at is None:
            self.first_user_input_at = now
        if not self.pending:
            return False
        self.status, self.greeted = "skipped", True
        return True

    def expire(self, now: float) -> bool:
        """At the deadline without first audio; return whether the greeting was missed now."""
        if not self.pending or now < self.expires_at:
            return False
        self.status = "missed"
        return True

    def fail(self) -> bool:
        """The greeting request failed; there is no fallback."""
        if not self.pending:
            return False
        self.status = "missed"
        return True
