"""Design tokens shared by every page: colors, node styles and type.

Every text/background pair in `TEXT_PAIRS` meets WCAG 2.1 AA for normal text (4.5:1) and every
pair in `GRAPHIC_PAIRS` meets the 3:1 non-text minimum; `tests/unit/test_theme.py` checks both.
Default and custom graph nodes differ in fill, border style, icon and label, so the difference
never depends on color alone.
"""

from typing import Final

COLORS: Final[dict[str, str]] = {
    "brand": "#197767",
    "brand-strong": "#125a4e",
    "surface": "#ffffff",
    "surface-muted": "#f4f7f6",
    "border": "#8a9690",
    "divider": "#dce1df",
    "text": "#242925",
    "text-muted": "#4f5852",
    "on-brand": "#ffffff",
    "default-fill": "#e3e8ec",
    "default-border": "#4a5866",
    "custom-fill": "#ffffff",
    "entry": "#7a4d00",
    "warning": "#8a4b00",
    "warning-fill": "#fff4e5",
    "danger": "#a12622",
    "danger-fill": "#fdecea",
    "focus": "#1f5fbf",
}

TEXT_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("text", "surface"),
    ("text", "surface-muted"),
    ("text-muted", "surface"),
    ("text-muted", "surface-muted"),
    ("brand", "surface"),
    ("brand", "surface-muted"),
    ("on-brand", "brand"),
    ("text", "default-fill"),
    ("text", "custom-fill"),
    ("entry", "surface"),
    ("warning", "warning-fill"),
    ("danger", "danger-fill"),
    ("danger", "surface"),
)

GRAPHIC_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("border", "surface"),
    ("default-border", "default-fill"),
    ("default-border", "surface"),
    ("brand", "custom-fill"),
    ("focus", "surface"),
    ("focus", "surface-muted"),
)

NODE_STYLES: Final[dict[str, dict[str, str]]] = {
    "default": {
        "fill": COLORS["default-fill"],
        "border": COLORS["default-border"],
        "border_style": "dashed",
        "icon": "lock",
        "label": "Default",
    },
    "custom": {
        "fill": COLORS["custom-fill"],
        "border": COLORS["brand"],
        "border_style": "solid",
        "icon": "pencil",
        "label": "Custom",
    },
    "entry": {"color": COLORS["entry"], "icon": "flag", "label": "Entry"},
    "warning": {
        "color": COLORS["warning"],
        "fill": COLORS["warning-fill"],
        "icon": "triangle-alert",
        "label": "Unreachable",
    },
    "danger": {
        "color": COLORS["danger"],
        "fill": COLORS["danger-fill"],
        "icon": "circle-x",
        "label": "Invalid",
    },
}

FONT_FAMILY: Final = "'Trebuchet MS', 'Segoe UI', sans-serif"
FOCUS_RING: Final = f"0 0 0 3px {COLORS['focus']}"
SIDEBAR_WIDTH: Final = "232px"
NARROW_BREAKPOINT_PX: Final = 768
