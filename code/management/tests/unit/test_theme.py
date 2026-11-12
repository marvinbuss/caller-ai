"""Design tokens meet WCAG 2.1 AA and default and custom nodes differ in more than color."""

import pytest

from management_app import theme


def relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    light, dark = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize(("foreground", "background"), theme.TEXT_PAIRS)
def test_text_pairs_meet_aa_for_normal_text(foreground, background):
    assert contrast(theme.COLORS[foreground], theme.COLORS[background]) >= 4.5


@pytest.mark.parametrize(("foreground", "background"), theme.GRAPHIC_PAIRS)
def test_essential_graphics_meet_aa_for_non_text(foreground, background):
    assert contrast(theme.COLORS[foreground], theme.COLORS[background]) >= 3.0


def test_required_tokens_exist():
    for name in ("brand", "surface", "surface-muted", "border", "text", "text-muted", "focus"):
        assert theme.COLORS[name].startswith("#") and len(theme.COLORS[name]) == 7
    assert {"default", "custom", "entry", "warning", "danger"} <= set(theme.NODE_STYLES)


def test_default_and_custom_nodes_differ_in_fill_icon_and_label():
    default, custom = theme.NODE_STYLES["default"], theme.NODE_STYLES["custom"]
    assert default["fill"] != custom["fill"]
    assert default["icon"] != custom["icon"]
    assert default["label"] == "Default" and custom["label"] == "Custom"
    assert default["border_style"] != custom["border_style"]
    assert theme.NODE_STYLES["entry"]["label"] == "Entry"
    assert theme.NODE_STYLES["warning"]["label"] == "Unreachable"
