"""Automated accessibility contract for selected syntax themes."""

from __future__ import annotations

from ralph.display.theme import (
    SYNTAX_THEME_ON_DARK_BG,
    SYNTAX_THEME_ON_LIGHT_BG,
    syntax_theme_for_background,
)


def test_syntax_theme_selects_the_background_safe_ansi_variant() -> None:
    """Syntax highlights select the terminal palette variant for the background."""
    assert syntax_theme_for_background(False) == SYNTAX_THEME_ON_DARK_BG
    assert syntax_theme_for_background(True) == SYNTAX_THEME_ON_LIGHT_BG
