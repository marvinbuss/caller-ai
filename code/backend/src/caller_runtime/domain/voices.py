"""Reviewed GPT-Live voice catalog for `gpt-live-1`; the only voices settings may select.

Source: "Managing GPT-Live sessions, Voice options"
(https://developers.openai.com/api/docs/guides/live-conversations#voice-options), checked on
2026-11-11, plus the service default `marin`. The Realtime voices (`alloy`, `ash`, `ballad`,
`coral`, `echo`, `sage`, `shimmer`, `verse`, `cedar`) are not in the GPT-Live table and stay out.
"""

from dataclasses import dataclass
from typing import Final, Literal

Presentation = Literal["Feminine", "Masculine"]


@dataclass(frozen=True, slots=True)
class Voice:
    id: str
    name: str
    language: str
    accent: str | None
    presentation: Presentation | None

    def as_json(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "accent": self.accent,
            "presentation": self.presentation,
        }


DEFAULT_VOICE: Final = "marin"

CATALOG: Final[tuple[Voice, ...]] = (
    Voice("marin", "Marin", "English", None, None),
    Voice("quartz", "Quartz", "English", "Australian", "Feminine"),
    Voice("ripple", "Ripple", "English", "Australian", "Masculine"),
    Voice("vesper", "Vesper", "English", "British", "Masculine"),
    Voice("willow", "Willow", "English", "Irish", "Feminine"),
    Voice("stone", "Stone", "English", "Irish", "Masculine"),
    Voice("gleam", "Gleam", "English", "North American", "Feminine"),
    Voice("meridian", "Meridian", "English", "North American", "Masculine"),
    Voice("beacon", "Beacon", "English", "Filipino", "Masculine"),
    Voice("delta", "Delta", "English", "Southern U.S.", "Feminine"),
    Voice("cinder", "Cinder", "English", "Southern U.S.", "Masculine"),
    Voice("bossa", "Bossa", "Portuguese", "Brazilian", "Feminine"),
    Voice("tempo", "Tempo", "Portuguese", "Brazilian", "Masculine"),
    Voice("nuri", "Nuri", "German", None, "Feminine"),
    Voice("juni", "Juni", "German", None, "Feminine"),
    Voice("flitz", "Flitz", "German", None, "Masculine"),
    Voice("brise", "Brise", "French", None, "Feminine"),
    Voice("sillage", "Sillage", "French", None, "Masculine"),
    Voice("nira", "Nira", "Hindi", None, "Feminine"),
    Voice("harema", "Harema", "Japanese", None, "Feminine"),
    Voice("shida", "Shida", "Japanese", None, "Masculine"),
    Voice("noeul", "Noeul", "Korean", None, "Feminine"),
)


def voice_ids() -> frozenset[str]:
    return frozenset(entry.id for entry in CATALOG)
