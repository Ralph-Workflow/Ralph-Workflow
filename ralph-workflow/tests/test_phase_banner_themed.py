"""ANSI-vs-plain regression tests for ParallelDisplay phase methods."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import (
    resolve_active_display,
)
from ralph.display.theme import RALPH_THEME


def _themed_context(buf: StringIO) -> object:
    """Create a DisplayContext for themed (color) output."""
    console = Console(
        file=buf,
        force_terminal=True,
        no_color=False,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )
    return make_display_context(console=console, env={})


def _plain_context(buf: StringIO) -> object:
    """Create a DisplayContext for plain (no color) output."""
    console = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=200,
    )
    return make_display_context(console=console, env={})


def test_show_phase_transition_emits_ansi_on_tty() -> None:
    buf = StringIO()
    ctx = _themed_context(buf)
    display = resolve_active_display(None, ctx)
    display.emit_phase_transition("planning", "development")
    assert "\x1b[" in buf.getvalue()


def test_show_phase_transition_no_ansi_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    display = resolve_active_display(None, ctx)
    display.emit_phase_transition("planning", "development")
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Planning" in out
    assert "Development" in out


def test_show_phase_start_emits_ansi_on_tty() -> None:
    buf = StringIO()
    ctx = _themed_context(buf)
    display = resolve_active_display(None, ctx)
    display.emit_phase_start("development")
    assert "\x1b[" in buf.getvalue()


def test_show_phase_start_no_ansi_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    display = resolve_active_display(None, ctx)
    display.emit_phase_start("development")
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Development" in out


def test_minor_transition_emits_ansi_on_tty() -> None:
    buf = StringIO()
    ctx = _themed_context(buf)
    display = resolve_active_display(None, ctx)
    display.emit_phase_transition("development", "development_analysis")
    assert "\x1b[" in buf.getvalue()


def test_minor_transition_no_ansi_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    display = resolve_active_display(None, ctx)
    display.emit_phase_transition("development", "development_analysis")
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Development" in out
    assert "Development Analysis" in out
