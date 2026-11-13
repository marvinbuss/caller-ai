import runpy
from pathlib import Path

from caller_runtime.services import modality_defaults

MANAGEMENT_DEFAULTS = (
    Path(__file__).resolve().parents[3]
    / "management"
    / "management_app"
    / "services"
    / "default_prompts.py"
)


def test_runtime_copies_are_byte_equal_to_the_management_defaults():
    management = runpy.run_path(str(MANAGEMENT_DEFAULTS))
    for name in (
        "VOICE_INSTRUCTIONS",
        "VOICE_PLATFORM_RULES",
        "TEXT_PLATFORM_RULES",
        "PROGRESS_RULES",
    ):
        assert getattr(modality_defaults, name).encode("utf-8") == management[name].encode(
            "utf-8"
        ), name


def test_both_platform_rules_end_with_the_progress_rules():
    for rules in (modality_defaults.VOICE_PLATFORM_RULES, modality_defaults.TEXT_PLATFORM_RULES):
        assert rules.endswith("\n\n" + modality_defaults.PROGRESS_RULES)
        assert "report_progress" in rules
