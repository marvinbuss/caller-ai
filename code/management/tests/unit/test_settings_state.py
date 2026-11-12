"""Settings page shaping: voice labels and groups, range checks, and what gets published."""

from management_app.state.settings import check, chosen, voice_groups, voice_label

VOICES = [
    {"id": "marin", "name": "Marin", "language": "English", "accent": None, "presentation": None},
    {
        "id": "quartz",
        "name": "Quartz",
        "language": "English",
        "accent": "Australian",
        "presentation": "Feminine",
    },
    {
        "id": "nuri",
        "name": "Nuri",
        "language": "German",
        "accent": None,
        "presentation": "Feminine",
    },
]
DEFAULTS = {"voice": "marin", "maxOutputTokens": 1024, "idleTimeoutSeconds": 120}


def test_voice_labels_name_language_accent_presentation_and_default() -> None:
    assert voice_label(VOICES[0], "marin") == "Marin \u2014 English (default)"
    assert voice_label(VOICES[1], "marin") == "Quartz \u2014 English (Australian), feminine"
    assert voice_label(VOICES[2], "marin") == "Nuri \u2014 German, feminine"


def test_voices_are_grouped_by_language_in_catalog_order() -> None:
    groups = voice_groups(VOICES, "marin")
    assert [group["language"] for group in groups] == ["English", "German"]
    assert [voice["id"] for voice in groups[0]["voices"]] == ["marin", "quartz"]


def test_out_of_range_values_name_the_allowed_range() -> None:
    voices = {"marin", "quartz"}
    good = {"voice": "quartz", "maxOutputTokens": "800", "idleTimeoutSeconds": "60"}
    assert check(good, voices, 1024) == {}
    assert check(good | {"idleTimeoutSeconds": "20"}, voices, 1024) == {
        "idleTimeoutSeconds": "Integer, 30 to 120."
    }
    assert check(good | {"idleTimeoutSeconds": "121"}, voices, 1024)["idleTimeoutSeconds"]
    assert check(good | {"maxOutputTokens": "1025"}, voices, 1024) == {
        "maxOutputTokens": "Integer, 1 to 1024."
    }
    assert check(good | {"maxOutputTokens": "8.5"}, voices, 1024)["maxOutputTokens"]
    assert check(good | {"maxOutputTokens": "0"}, voices, 1024)["maxOutputTokens"]
    assert check(good | {"voice": "cove"}, voices, 1024) == {
        "voice": "Choose a voice from the list."
    }


def test_only_values_that_differ_from_the_defaults_are_published() -> None:
    same = {"voice": "marin", "maxOutputTokens": "1024", "idleTimeoutSeconds": "120"}
    assert chosen(same, DEFAULTS) == {}
    assert chosen(same | {"voice": "quartz", "idleTimeoutSeconds": "60"}, DEFAULTS) == {
        "voice": "quartz",
        "idleTimeoutSeconds": 60,
    }
