"""The reviewed GPT-Live voice catalog is unique, bounded and excludes the Realtime-only voices."""

import re

import pytest


def test_catalog_ids_are_unique_bounded_and_default_is_marin():
    from caller_runtime.domain.voices import CATALOG, DEFAULT_VOICE, voice_ids

    ids = [entry.id for entry in CATALOG]
    assert len(ids) == len(set(ids)) == 22
    assert all(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identifier) for identifier in ids)
    assert DEFAULT_VOICE == "marin" and DEFAULT_VOICE in voice_ids()
    assert isinstance(CATALOG, tuple)


def test_each_entry_has_display_fields():
    from caller_runtime.domain.voices import CATALOG

    for entry in CATALOG:
        row = entry.as_json()
        assert set(row) == {"id", "name", "language", "accent", "presentation"}
        assert row["name"] and row["language"]
        assert row["accent"] is None or isinstance(row["accent"], str)
        assert row["presentation"] in {"Feminine", "Masculine", None}


@pytest.mark.parametrize(
    "voice", ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "cedar"]
)
def test_realtime_only_voices_are_not_offered(voice):
    from caller_runtime.domain.voices import voice_ids

    assert voice not in voice_ids()


def test_entries_are_immutable():
    from dataclasses import FrozenInstanceError

    from caller_runtime.domain.voices import CATALOG

    with pytest.raises(FrozenInstanceError):
        CATALOG[0].id = "changed"  # type: ignore[misc]
