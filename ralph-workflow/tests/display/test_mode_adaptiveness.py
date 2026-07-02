"""Black-box tests for the single-mode DisplayContext invariant.

wt-028-display consolidated Ralph Workflow's display surface to a
single ``default`` mode. There are no width-based dispatch, no
``compact`` / ``medium`` / ``wide`` tiers, and no
``force_mode`` / ``RALPH_FORCE_NARROW`` overrides.

These tests pin the single-mode invariant end-to-end:

- ``ctx.mode`` is always the literal string ``"default"`` for any
  width, any ``COLUMNS`` value, any glyph-detection state, and any
  color policy.
- Color and glyph flags adapt to width / env, but the mode value
  itself is fixed.
- Width-refresh preserves ``mode == "default"`` after a SIGWINCH-style
  resize, because ``refreshed()`` rebuilds the context through
  :func:`make_display_context`.
"""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.theme import ASCII_GLYPHS, UNICODE_GLYPHS


def _ctx(env: dict[str, str], *, force_glyphs: bool | None = None) -> DisplayContext:
    """Create a DisplayContext with a no-op console for the given env."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=300)
    return make_display_context(console=console, env=env, force_glyphs=force_glyphs)


@pytest.mark.parametrize("columns", ["40", "80", "120", "200"])
def test_columns_any_width_gives_default_mode(columns: str) -> None:
    """Any COLUMNS width produces mode='default' (no width-based dispatch)."""
    ctx = _ctx({"COLUMNS": columns})
    assert ctx.mode == "default", (
        f"ctx.mode must be 'default' for COLUMNS={columns}; got {ctx.mode!r}"
    )


def test_default_mode_is_the_only_mode() -> None:
    """A fresh DisplayContext with no env overrides still returns mode='default'."""
    ctx = _ctx({})
    assert ctx.mode == "default"


def test_default_mode_persists_through_refresh() -> None:
    """refreshed() preserves mode='default' across width changes (no tier switching)."""
    ctx = _ctx({"COLUMNS": "120"})
    assert ctx.mode == "default"
    refreshed = ctx.refreshed()
    assert refreshed.mode == "default"


def test_no_color_disables_color_even_with_force_color() -> None:
    ctx = _ctx({"NO_COLOR": "1", "FORCE_COLOR": "1"})
    assert ctx.color_enabled is False


def test_ralph_force_ascii_disables_glyphs() -> None:
    ctx = _ctx({"COLUMNS": "120", "RALPH_FORCE_ASCII": "1"})
    assert ctx.glyphs_enabled is False
    assert ctx.glyph_for("milestone") == ASCII_GLYPHS["milestone"]


def test_term_dumb_disables_glyphs() -> None:
    ctx = _ctx({"TERM": "dumb"})
    assert ctx.glyphs_enabled is False
    assert ctx.glyph_for("arrow") == ASCII_GLYPHS["arrow"]


def test_force_glyphs_true_overrides_all_env() -> None:
    ctx = _ctx({"RALPH_FORCE_ASCII": "1", "TERM": "dumb"}, force_glyphs=True)
    assert ctx.glyphs_enabled is True
    assert ctx.glyph_for("milestone") == UNICODE_GLYPHS["milestone"]


def test_default_mode_uses_unicode_glyphs_by_default() -> None:
    """Single default-mode layout uses Unicode glyphs by default."""
    ctx = _ctx({"COLUMNS": "120"})
    assert ctx.mode == "default"
    assert ctx.glyph_for("milestone") == UNICODE_GLYPHS["milestone"]
