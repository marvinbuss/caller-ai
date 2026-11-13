import pytest

from caller_runtime.services.speech_parts import QUIET_HANDOVER, split_reply


def _check(text: str, parts: list[str]) -> None:
    assert "".join(parts) == text
    assert all(part and len(part.encode("utf-8")) <= 500 for part in parts)


def test_short_reply_is_one_part_and_empty_reply_has_none():
    assert split_reply("Hello there.") == ["Hello there."]
    assert split_reply("") == []


def test_long_reply_prefers_sentence_ends():
    sentence = "This is a complete spoken sentence about the request. "
    text = sentence * 30
    parts = split_reply(text)
    _check(text, parts)
    assert len(parts) > 1
    assert all(part.endswith(". ") for part in parts[:-1])


def test_without_sentence_ends_it_cuts_at_whitespace():
    text = " ".join(["word"] * 300)
    parts = split_reply(text)
    _check(text, parts)
    assert all(part.endswith(" ") for part in parts[:-1])


def test_without_whitespace_it_cuts_at_code_point_boundaries():
    text = "ä" * 400 + "€" * 300
    parts = split_reply(text)
    _check(text, parts)
    assert len(parts) >= 3
    for part in parts:
        part.encode("utf-8").decode("utf-8")


@pytest.mark.parametrize(
    "text",
    [
        "Short. " * 200,
        "Line one\nLine two\n" * 80,
        "a" * 2048,
        "Mixed: ä€ 😀. " * 120,
        "Question? Answer! " * 100,
    ],
)
def test_every_part_is_bounded_and_joins_back(text):
    _check(text, split_reply(text))


def test_quiet_handover_is_short_and_fixed():
    assert QUIET_HANDOVER == "Request received; the application is working on it."
    assert len(QUIET_HANDOVER.encode("utf-8")) <= 500


def test_dropped_note_is_prefixed_capped_and_split_into_at_most_three_parts():
    from caller_runtime.services.speech_parts import dropped_note

    short = dropped_note("Your order ships tomorrow.")
    assert short == ["The rest of the earlier answer was not spoken: Your order ships tomorrow."]
    long = dropped_note("Another detail about the order follows here. " * 80)
    assert long[0].startswith("The rest of the earlier answer was not spoken: ")
    assert 1 <= len(long) <= 3
    assert all(part.strip() and len(part.encode("utf-8")) <= 500 for part in long)
    assert sum(len(part.encode("utf-8")) for part in long) <= 1500
    assert dropped_note("   ") == []
    unbroken = dropped_note("ä" * 2000)
    assert len(unbroken) == 3 and sum(len(p.encode("utf-8")) for p in unbroken) <= 1500
