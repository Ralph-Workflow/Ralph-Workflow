"""Automated accessibility contract for selected syntax themes."""

from __future__ import annotations

import pytest
from rich.syntax import Syntax
from rich.text import Text

from ralph.display._tool_result_syntax import append_tool_result_syntax
from ralph.display.edit_preview import build_edit_preview
from ralph.display.theme import (
    SYNTAX_BACKGROUND_TRANSPARENT,
    SYNTAX_THEME_ON_DARK_BG,
    SYNTAX_THEME_ON_LIGHT_BG,
    syntax_theme_for_background,
)


def test_syntax_theme_selects_the_background_safe_ansi_variant() -> None:
    """Syntax highlights select the terminal palette variant for the background."""
    assert syntax_theme_for_background(False) == SYNTAX_THEME_ON_DARK_BG
    assert syntax_theme_for_background(True) == SYNTAX_THEME_ON_LIGHT_BG


@pytest.mark.parametrize(
    ("terminal_bg_is_light", "expected_theme"),
    [(False, SYNTAX_THEME_ON_DARK_BG), (True, SYNTAX_THEME_ON_LIGHT_BG)],
)
def test_file_syntax_uses_accessible_ansi_theme_without_painting_background(
    terminal_bg_is_light: bool,
    expected_theme: str,
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
    assert preview._theme.__class__.__name__ == "ANSISyntaxTheme"
    assert syntax_theme_for_background(terminal_bg_is_light) == expected_theme


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
