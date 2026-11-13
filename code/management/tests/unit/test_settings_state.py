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


def test_text_boxes_show_stored_values_or_the_shipped_defaults() -> None:
    from management_app.services import default_prompts
    from management_app.state.settings import TEXT_FIELDS, shown_texts, text_overrides

    overrides = text_overrides({"voice": "marin", "textGlobalPrompt": "Synthetic global"})
    assert [field for field, _section, _label in TEXT_FIELDS] == list(overrides)
    assert overrides["textGlobalPrompt"] == "Synthetic global"
    assert overrides["voiceInstructions"] is None
    shown = shown_texts(overrides)
    assert shown["voiceInstructions"] == default_prompts.VOICE_INSTRUCTIONS
    assert shown["textGlobalPrompt"] == "Synthetic global"
    assert {section for _field, section, _label in TEXT_FIELDS} == {"Voice", "Text"}


def test_restore_default_removes_the_stored_value_and_other_fields_stay() -> None:
    from management_app.state.settings import publishable, text_overrides

    stored = {"textGlobalPrompt": "Synthetic global", "quietHandover": False}
    overrides = text_overrides(stored) | {"textGlobalPrompt": None, "voiceGlobalPrompt": ""}
    assert publishable(overrides, stored) == {"quietHandover": False, "voiceGlobalPrompt": ""}
    # A draft without any 2.2.0 text publishes none, so the stored schema version stays.
    assert publishable(text_overrides({}), {}) == {}


def test_changed_platform_rules_or_voice_instructions_need_the_confirmation() -> None:
    from management_app.state.settings import needs_confirmation, text_overrides

    stored = {"voicePlatformRules": "Synthetic rules"}
    overrides = text_overrides(stored)
    assert not needs_confirmation(overrides, stored)
    assert not needs_confirmation(overrides | {"textGlobalPrompt": "Changed"}, stored)
    assert needs_confirmation(overrides | {"voicePlatformRules": None}, stored)
    assert needs_confirmation(overrides | {"voiceInstructions": "Changed"}, stored)
    assert needs_confirmation(overrides | {"textPlatformRules": ""}, stored)


def test_text_limits_are_checked_before_publishing() -> None:
    from management_app.state.settings import text_errors, text_overrides

    overrides = text_overrides({})
    assert text_errors(overrides) == {}
    assert text_errors(overrides | {"voiceInstructions": ""}) == {
        "voiceInstructions": "1 to 12288 bytes."
    }
    assert text_errors(overrides | {"textPlatformRules": ""}) == {}
    assert text_errors(overrides | {"textGlobalPrompt": "x" * 8193}) == {
        "textGlobalPrompt": "At most 8192 bytes."
    }


def test_quiet_handover_switch_is_on_by_default_and_stores_only_a_choice() -> None:
    from management_app.state.settings import stored_quiet, with_quiet

    # Absent means on: the switch shows on and nothing is stored.
    assert stored_quiet({}) is None and (stored_quiet({}) is not False)
    assert stored_quiet({"quietHandover": False}) is False
    assert stored_quiet({"quietHandover": "false"}) is None
    # Switching off stores false; Restore default removes the field again.
    assert with_quiet({"voice": "marin"}, False) == {"voice": "marin", "quietHandover": False}
    assert with_quiet({"voice": "marin", "quietHandover": False}, None) == {"voice": "marin"}
    assert with_quiet({}, True) == {"quietHandover": True}
