"""Automated accessibility contract for selected syntax themes."""

from __future__ import annotations

import pytest
from pygments.token import (
    Comment,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    String,
)
from pygments.token import Text as PygmentsText
from rich.syntax import PygmentsSyntaxTheme, Syntax

from ralph.display import theme
from ralph.display.edit_preview import build_edit_preview
from ralph.display.theme import (
    IDENTITY_PALETTE,
    IDENTITY_PALETTE_ON_LIGHT_BG,
    SYNTAX_BACKGROUND_TRANSPARENT,
    SYNTAX_THEME_ON_DARK_BG,
    SYNTAX_THEME_ON_LIGHT_BG,
    SYNTAX_THEME_ON_UNKNOWN_BG,
    contrast_ratio,
    diff_fill_styles,
    diff_token_foregrounds,
    pick_status_styles,
    preview_background_for_background,
    syntax_theme_for_background,
)

_TOKEN_ROLES = (Comment, Keyword, Name.Function, String, Number, Operator)
_FALLBACK_TOKEN_ROLES = (PygmentsText, Name, PygmentsText.Whitespace)
_CVD_MATRICES = (
    theme._DEUTERANOPIA_MATRIX,
    theme._PROTANOPIA_MATRIX,
    theme._TRITANOPIA_MATRIX,
)


def test_syntax_theme_selects_the_background_safe_ansi_variant() -> None:
    """Syntax highlights select the terminal palette variant for the background."""
    assert syntax_theme_for_background(False) == SYNTAX_THEME_ON_DARK_BG
    assert syntax_theme_for_background(True) == SYNTAX_THEME_ON_LIGHT_BG
    assert syntax_theme_for_background(None) == SYNTAX_THEME_ON_UNKNOWN_BG


@pytest.mark.parametrize(
    ("terminal_bg_is_light", "expected_theme"),
    [
        (False, SYNTAX_THEME_ON_DARK_BG),
        (True, SYNTAX_THEME_ON_LIGHT_BG),
        (None, SYNTAX_THEME_ON_UNKNOWN_BG),
    ],
)
def test_file_syntax_uses_accessible_ansi_theme_with_a_complete_owned_background(
    terminal_bg_is_light: bool | None,
    expected_theme: object,
) -> None:
    """S-4: syntax inherits the palette and owns a complete known-background surface."""
    preview = build_edit_preview(
        "read_file",
        {"path": "example.py", "content": "def render() -> int:\n    return 1\n"},
        width=80,
        terminal_bg_is_light=terminal_bg_is_light,
    )
    assert isinstance(preview, Syntax)
    assert preview.background_color == preview_background_for_background(terminal_bg_is_light)
    assert preview._theme.__class__.__name__ == "PygmentsSyntaxTheme"
    assert syntax_theme_for_background(terminal_bg_is_light) == expected_theme


@pytest.mark.parametrize("terminal_bg_is_light", [False, True, None])
def test_syntax_palette_is_contrast_safe_and_cvd_distinct_from_semantic_roles(
    terminal_bg_is_light: bool,
) -> None:
    """DA-001 / S-3: token colors remain readable and non-semantic under CVD."""
    syntax = Syntax(
        "def render(value: str) -> int:\n    return 1\n",
        "python",
        theme=syntax_theme_for_background(terminal_bg_is_light),
        background_color=SYNTAX_BACKGROUND_TRANSPARENT,
    )
    assert isinstance(syntax._theme, PygmentsSyntaxTheme)
    colors = {
        str(
            token
        ): f"#{color.get_truecolor().red:02X}{color.get_truecolor().green:02X}{color.get_truecolor().blue:02X}"
        for token in _TOKEN_ROLES
        if (color := syntax._theme.get_style_for_token(tuple(str(token).split(".")[1:])).color)
        is not None
    }
    assert len(colors) == len(_TOKEN_ROLES)
    backgrounds = (
        ("#FFFFFF",)
        if terminal_bg_is_light is True
        else (("#000000",) if terminal_bg_is_light is False else ("#000000", "#FFFFFF"))
    )
    assert all(
        contrast_ratio(color, background) >= 4.5
        for color in colors.values()
        for background in backgrounds
    )

    identities = (
        IDENTITY_PALETTE_ON_LIGHT_BG
        if terminal_bg_is_light is True
        else (
            theme.IDENTITY_PALETTE_ON_UNKNOWN_BG
            if terminal_bg_is_light is None
            else IDENTITY_PALETTE
        )
    )
    semantic_colors = (*identities, *theme._status_role_hexes())
    for matrix in _CVD_MATRICES:
        simulated = {role: theme._simulate_cvd(color, matrix) for role, color in colors.items()}
        assert len(set(simulated.values())) == len(simulated)
        assert not set(simulated.values()) & {
            theme._simulate_cvd(color, matrix) for color in semantic_colors
        }


@pytest.mark.parametrize("terminal_bg_is_light", [False, True, None])
def test_syntax_palette_fallback_tokens_are_contrast_safe(
    terminal_bg_is_light: bool | None,
) -> None:
    """DA-003: unmapped tokens never inherit Rich's black fallback."""
    syntax = Syntax(
        "plain text",
        "text",
        theme=syntax_theme_for_background(terminal_bg_is_light),
        background_color=SYNTAX_BACKGROUND_TRANSPARENT,
    )
    backgrounds = (
        ("#FFFFFF",)
        if terminal_bg_is_light is True
        else (("#000000",) if terminal_bg_is_light is False else ("#000000", "#FFFFFF"))
    )
    for token in _FALLBACK_TOKEN_ROLES:
        color = syntax._theme.get_style_for_token(tuple(str(token).split(".")[1:])).color
        assert color is not None
        hex_color = f"#{color.get_truecolor().red:02X}{color.get_truecolor().green:02X}{color.get_truecolor().blue:02X}"
        assert all(contrast_ratio(hex_color, background) >= 4.5 for background in backgrounds), (
            token
        )


@pytest.mark.parametrize("terminal_bg_is_light", [False, True, None])
def test_diff_tokens_are_distinct_and_accessibly_colored(
    terminal_bg_is_light: bool | None,
) -> None:
    """DA-004: diff carriers remain distinct and legible on every background."""
    syntax = Syntax(
        "@@ -1 +1 @@\n-old\n+new\n",
        "diff",
        theme=syntax_theme_for_background(terminal_bg_is_light),
        background_color=SYNTAX_BACKGROUND_TRANSPARENT,
    )
    colors = {
        token: f"#{color.get_truecolor().red:02X}{color.get_truecolor().green:02X}{color.get_truecolor().blue:02X}"
        for token in (Generic.Deleted, Generic.Inserted, Generic.Subheading, PygmentsText)
        if (color := syntax._theme.get_style_for_token(tuple(str(token).split(".")[1:])).color)
        is not None
    }
    assert len(colors) == 4
    assert len(set(colors.values())) == 4
    backgrounds = (
        ("#FFFFFF",)
        if terminal_bg_is_light is True
        else (("#000000",) if terminal_bg_is_light is False else ("#000000", "#FFFFFF"))
    )
    assert all(
        contrast_ratio(color, background) >= 4.5
        for color in colors.values()
        for background in backgrounds
    )
    for matrix in _CVD_MATRICES:
        assert len({theme._simulate_cvd(color, matrix) for color in colors.values()}) == 4


@pytest.mark.parametrize("terminal_bg_is_light", [False, True, None])
def test_derived_diff_fills_meet_the_painted_line_accessibility_contract(
    terminal_bg_is_light: bool | None,
) -> None:
    """S-2: every painted diff foreground clears its resolved wash."""
    fills = diff_fill_styles(terminal_bg_is_light)
    if terminal_bg_is_light is None:
        assert fills is None
        return
    assert fills is not None
    marker_styles = pick_status_styles(terminal_bg_is_light)
    marker_hexes = tuple(
        theme._extract_hex(marker_styles[status][0]) for status in ("error", "success")
    )
    for foreground in (*diff_token_foregrounds(terminal_bg_is_light), *marker_hexes):
        assert all(contrast_ratio(foreground, fill) >= 4.5 for fill in fills)
    for matrix in _CVD_MATRICES:
        assert theme._simulate_cvd(fills[0], matrix) != theme._simulate_cvd(fills[1], matrix)


def test_result_preview_uses_the_same_owned_palette_contract() -> None:
    """S-4: result previews use the shared known-background surface."""
    preview = build_edit_preview(
        "read_file",
        {"path": "result.json", "content": '{"result": 1}'},
        width=80,
        terminal_bg_is_light=False,
    )
    assert isinstance(preview, Syntax)
    assert preview.background_color == preview_background_for_background(False)
