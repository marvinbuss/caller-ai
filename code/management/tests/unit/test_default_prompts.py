from management_app.services import default_prompts

KIB = 1024
LANGUAGE_RULE = (
    "Start in English. As soon as the user speaks or writes in another language, "
    "continue in that language"
)
LIMITS = {
    "WELCOME_PROMPT": 16 * KIB,
    "ROUTING_PROMPT": 16 * KIB,
    "GOODBYE_PROMPT": 16 * KIB,
    "VOICE_INSTRUCTIONS": 12 * KIB,
    "VOICE_PLATFORM_RULES": 8 * KIB,
    "VOICE_GLOBAL_PROMPT": 8 * KIB,
    "TEXT_PLATFORM_RULES": 8 * KIB,
    "TEXT_GLOBAL_PROMPT": 8 * KIB,
}


def test_every_shipped_default_is_non_empty_and_within_its_limit():
    for name, limit in LIMITS.items():
        value = getattr(default_prompts, name)
        assert isinstance(value, str) and value.strip(), name
        assert "\x00" not in value, name
        assert len(value.encode("utf-8")) <= limit, name


def test_voice_instructions_follow_the_published_live_structure():
    text = default_prompts.VOICE_INSTRUCTIONS
    positions = [
        text.index(heading)
        for heading in ("Backchannel policy:", "Interruption policy:", "Delegation policy:")
    ]
    assert positions == sorted(positions)
    assert "Generate <|SpawnThinking|>" in text


def test_language_rule_is_in_voice_instructions_and_both_platform_rules():
    for name in ("VOICE_INSTRUCTIONS", "VOICE_PLATFORM_RULES", "TEXT_PLATFORM_RULES"):
        assert LANGUAGE_RULE in getattr(default_prompts, name), name


def test_voice_rules_forbid_markup_and_text_rules_allow_formatting():
    voice = default_prompts.VOICE_PLATFORM_RULES
    text = default_prompts.TEXT_PLATFORM_RULES
    for word in ("tables", "lists", "Markdown", "links"):
        assert word in voice
    for word in ("lists", "tables", "headings", "emphasis", "code", "links", "https"):
        assert word in text


def test_both_platform_rules_carry_the_progress_rules():
    for name in ("VOICE_PLATFORM_RULES", "TEXT_PLATFORM_RULES"):
        rules = getattr(default_prompts, name)
        assert rules.endswith(default_prompts.PROGRESS_RULES), name
    progress = default_prompts.PROGRESS_RULES
    # FR-021: still_working before any call; FR-022: final states only once known; waiting.
    assert "report_progress` with still_working before any tool, MCP or external call" in progress
    assert "completed or failed only after the outcome is known" in progress
    assert "waiting_for_input when you ask the user" in progress
