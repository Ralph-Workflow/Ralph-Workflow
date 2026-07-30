"""S-1 background-aware preview contracts."""

from __future__ import annotations

import io

from rich.console import Console
from rich.syntax import Syntax

from ralph.display.edit_preview import build_edit_preview, render_markdown_preview
from ralph.display.theme import (
    SYNTAX_THEME_ON_UNKNOWN_BG,
    contrast_ratio,
    pick_status_styles,
    syntax_theme_for_background,
)


def _render(renderable: object) -> str:
    output = io.StringIO()
    Console(
        file=output, force_terminal=True, color_system="truecolor", no_color=False, width=80
    ).print(renderable)
    return output.getvalue()


def test_s1_unknown_background_uses_a_palette_safe_on_black_and_white() -> None:
    """S-1: unknown terminals never inherit the dark-only fallback."""
    assert syntax_theme_for_background(None) is SYNTAX_THEME_ON_UNKNOWN_BG
    for style, _icon, _label in pick_status_styles(None).values():
        color = style.rsplit(" ", 1)[-1]
        assert contrast_ratio(color, "#000000") >= 4.5
        assert contrast_ratio(color, "#FFFFFF") >= 4.5


def test_s1_markdown_fences_use_the_fixed_transparent_syntax_palette() -> None:
    """S-1: Markdown fences use the shared selector rather than Rich ANSI themes."""
    rendered = _render(
        render_markdown_preview("```python\nx = 1\n```", width=80, terminal_bg_is_light=None)
    )
    assert "38;2;" in rendered
    assert "48;2;" not in rendered


def test_s1_preview_background_argument_is_required() -> None:
    """S-1: preview construction cannot silently choose a terminal palette."""
    try:
        build_edit_preview("write_file", {"path": "x.py", "content": "x = 1"}, width=80)
    except TypeError:
        pass
    else:
        raise AssertionError("terminal_bg_is_light must be required")


def test_s1_preview_unknown_background_keeps_syntax_highlighting() -> None:
    preview = build_edit_preview(
        "write_file", {"path": "x.py", "content": "x = 1"}, width=80, terminal_bg_is_light=None
    )
    assert isinstance(preview, Syntax)
    assert "38;2;" in _render(preview)
