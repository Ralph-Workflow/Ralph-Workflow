"""Automated accessibility contract for selected syntax themes."""

from __future__ import annotations

import pytest
from pygments.token import Comment, Keyword, Name, Number, Operator, String
from rich.syntax import PygmentsSyntaxTheme, Syntax
from rich.text import Text

from ralph.display import theme
from ralph.display._tool_result_syntax import append_tool_result_syntax
from ralph.display.edit_preview import build_edit_preview
from ralph.display.theme import (
    IDENTITY_PALETTE,
    IDENTITY_PALETTE_ON_LIGHT_BG,
    SYNTAX_BACKGROUND_TRANSPARENT,
    SYNTAX_THEME_ON_DARK_BG,
    SYNTAX_THEME_ON_LIGHT_BG,
    SYNTAX_THEME_ON_UNKNOWN_BG,
    contrast_ratio,
    syntax_theme_for_background,
)

_TOKEN_ROLES = (Comment, Keyword, Name.Function, String, Number, Operator)
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
def test_file_syntax_uses_accessible_ansi_theme_without_painting_background(
    terminal_bg_is_light: bool | None,
    expected_theme: object,
) -> None:
    """S-3: syntax inherits the operator palette and leaves status colors untouched."""
    preview = build_edit_preview(
        "read_file",
        {"path": "example.py", "content": "def render() -> int:\n    return 1\n"},
        width=80,
        terminal_bg_is_light=terminal_bg_is_light,
    )
    assert isinstance(preview, Syntax)
    assert preview.background_color == SYNTAX_BACKGROUND_TRANSPARENT
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
        str(token): f"#{color.get_truecolor().red:02X}{color.get_truecolor().green:02X}{color.get_truecolor().blue:02X}"
        for token in _TOKEN_ROLES
        if (color := syntax._theme.get_style_for_token(tuple(str(token).split(".")[1:])).color)
        is not None
    }
    assert len(colors) == len(_TOKEN_ROLES)
    backgrounds = ("#FFFFFF",) if terminal_bg_is_light is True else (
        ("#000000",) if terminal_bg_is_light is False else ("#000000", "#FFFFFF")
    )
    assert all(contrast_ratio(color, background) >= 4.5 for color in colors.values() for background in backgrounds)

    identities = IDENTITY_PALETTE_ON_LIGHT_BG if terminal_bg_is_light is True else (
        theme.IDENTITY_PALETTE_ON_UNKNOWN_BG if terminal_bg_is_light is None else IDENTITY_PALETTE
    )
    semantic_colors = (*identities, *theme._status_role_hexes())
    for matrix in _CVD_MATRICES:
        simulated = {role: theme._simulate_cvd(color, matrix) for role, color in colors.items()}
        assert len(set(simulated.values())) == len(simulated)
        assert not set(simulated.values()) & {
            theme._simulate_cvd(color, matrix) for color in semantic_colors
        }


def test_tool_result_syntax_uses_the_same_transparent_palette_contract() -> None:
    """S-3: recognized result payloads cannot paint a conflicting background."""
    text = Text()
    assert append_tool_result_syntax(
        text,
        '{"result": 1}',
        "mcp__ralph__read_file",
        terminal_bg_is_light=False,
    )
    assert text.plain.rstrip("\n") == '{"result": 1}'
