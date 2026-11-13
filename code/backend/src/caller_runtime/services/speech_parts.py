"""Splits spoken replies into GPT-Live context appends of at most 500 UTF-8 bytes.

`split_reply` cuts at the last sentence end that fits, then at the last whitespace, then at a
code-point boundary, so joining the parts gives back the reply. The texts here are fixed
application lines; none of them come from configuration.
"""

import re
from typing import Final

PART_BYTES: Final = 500
QUIET_HANDOVER: Final = "Request received; the application is working on it."
DROPPED_PREFIX: Final = "The rest of the earlier answer was not spoken: "
DROPPED_NOTE_BYTES: Final = 1500
_SENTENCE_END = re.compile(r"[.!?…]['\")\]]*\s+|\n\s*")


def _prefix_length(text: str, limit: int) -> int:
    """Return the number of code points of the longest prefix within `limit` UTF-8 bytes."""
    size = 0
    for index, character in enumerate(text):
        size += len(character.encode("utf-8"))
        if size > limit:
            return index
    return len(text)


def split_reply(text: str, limit: int = PART_BYTES) -> list[str]:
    if not isinstance(text, str) or type(limit) is not int or limit < 4:
        raise ValueError("invalid_speech_reply")
    parts: list[str] = []
    rest = text
    while rest:
        if len(rest.encode("utf-8")) <= limit:
            parts.append(rest)
            break
        end = _prefix_length(rest, limit)
        window = rest[:end]
        cut = 0
        for match in _SENTENCE_END.finditer(window):
            cut = match.end()
        if cut == 0:
            cut = next(
                (index + 1 for index in range(end - 1, 0, -1) if window[index].isspace()), end
            )
        parts.append(rest[:cut])
        rest = rest[cut:]
    return parts


def dropped_note(remaining: str) -> list[str]:
    """Quiet note for the parts an interruption dropped: at most 1,500 bytes in 3 parts."""
    if not isinstance(remaining, str):
        raise ValueError("invalid_speech_reply")
    if not remaining.strip():
        return []
    note = DROPPED_PREFIX + remaining.strip()
    note = note[: _prefix_length(note, DROPPED_NOTE_BYTES)]
    return [part for part in split_reply(note) if part.strip()][:3]
