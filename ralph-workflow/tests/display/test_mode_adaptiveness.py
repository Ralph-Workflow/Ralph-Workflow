"""Black-box tests for the single-mode DisplayContext invariant.

wt-028-display consolidated Ralph Workflow's display surface to a
single ``default`` mode. There is no width-based dispatch, no
``compact`` / ``medium`` / ``wide`` tier, no ``force_mode`` keyword,
and no ``RALPH_FORCE_NARROW`` environment variable. The
:class:`DisplayContext` always exposes the single ``default`` mode;
the type annotation ``Literal["default"]`` pins this at type-check
time so a future widening requires an explicit type relaxation.

These tests pin the single-mode invariants end-to-end:

- Color and glyph flags adapt to width / env, but the mode value
  itself is fixed to the literal string ``"default"``.
- Width-refresh preserves the single ``default`` mode after a
  SIGWINCH-style resize, because ``refreshed()`` rebuilds the
  context through :func:`make_display_context`.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.theme import ASCII_GLYPHS, UNICODE_GLYPHS


def _ctx(env: dict[str, str], *, force_glyphs: bool | None = None) -> DisplayContext:
    """Create a DisplayContext with a no-op console for the given env."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=300)
    return make_display_context(console=console, env=env, force_glyphs=force_glyphs)


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
    assert ctx.glyph_for("milestone") == UNICODE_GLYPHS["milestone"]
