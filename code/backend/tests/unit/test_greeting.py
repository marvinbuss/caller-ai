import pytest

from caller_runtime.services.greeting import VOICE_GREETING_SECONDS, GreetingState


def test_first_output_after_the_text_was_sent_greets_once_with_its_seconds():
    state = GreetingState(100.0)
    assert state.pending and not state.greeted
    assert state.output(100.5) is None
    state.sent()
    assert state.output(101.25) == 1.25
    assert state.status == "greeted" and state.greeted
    assert state.output(101.5) is None
    assert not state.user_input(102.0) and state.status == "greeted"
    assert not state.expire(106.0) and not state.fail()
    assert state.first_user_input_at == 102.0


def test_caller_speech_or_a_delegation_first_skips_the_greeting():
    state = GreetingState(10.0)
    assert state.user_input(11.0)
    assert state.status == "skipped" and state.greeted
    state.sent()
    assert not state.text_sent and state.output(11.5) is None
    assert not state.user_input(12.0) and state.first_user_input_at == 11.0


def test_no_first_audio_by_the_deadline_is_missed_without_retry():
    state = GreetingState(0.0)
    assert VOICE_GREETING_SECONDS == 5.0
    assert not state.expire(4.99)
    state.sent()
    assert state.output(5.01) is None and state.pending
    assert state.expire(5.0)
    assert state.status == "missed" and not state.greeted
    assert state.output(5.2) is None and not state.expire(6.0) and not state.user_input(7.0)
    assert state.status == "missed"


def test_a_failed_request_is_missed_and_never_falls_back():
    state = GreetingState(0.0)
    assert state.fail() and state.status == "missed"
    assert not state.fail()
    state.sent()
    assert state.output(1.0) is None


@pytest.mark.parametrize("ready_at", [float("nan"), float("inf"), "now", True])
def test_invalid_ready_times_are_refused(ready_at):
    with pytest.raises(ValueError, match="invalid_greeting_state"):
        GreetingState(ready_at)
