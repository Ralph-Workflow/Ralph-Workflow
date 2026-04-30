"""Black-box mode-adaptiveness tests for make_display_context.

Covers terminal width thresholds, override env vars, color policy,
glyph detection, and the force_glyphs override.
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


def test_columns_40_gives_compact_mode() -> None:
    ctx = _ctx({"COLUMNS": "40"})
    assert ctx.mode == "compact"
    assert ctx.narrow is True


def test_columns_80_gives_medium_mode() -> None:
    ctx = _ctx({"COLUMNS": "80"})
    assert ctx.mode == "medium"
    assert ctx.narrow is False


def test_columns_120_gives_wide_mode() -> None:
    ctx = _ctx({"COLUMNS": "120"})
    assert ctx.mode == "wide"
    assert ctx.narrow is False


def test_ralph_force_narrow_overrides_wide_columns() -> None:
    ctx = _ctx({"COLUMNS": "200", "RALPH_FORCE_NARROW": "1"})
    assert ctx.mode == "compact"
    assert ctx.narrow is True


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


def test_wide_mode_uses_unicode_glyphs_by_default() -> None:
    # StringIO has None encoding which skips the encoding check in detect_glyph_capability,
    # and without TERM=dumb or RALPH_FORCE_ASCII=1, glyphs are enabled by default.
    ctx = _ctx({"COLUMNS": "120"})
    assert ctx.glyph_for("milestone") == UNICODE_GLYPHS["milestone"]
