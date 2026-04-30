"""Tests for PlainLogRenderer.emit_phase_close."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.plain_renderer import _TAG_CATEGORY, _TAGS, PlainLogRenderer


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return PlainLogRenderer(make_display_context(console=console, env={})), buf


def test_phase_close_emits_single_line() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_phase_close("planning", "plan: 5 step(s), 2 risk(s)")
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "INFO META [phase-close] phase=planning plan: 5 step(s), 2 risk(s)" in lines[0]


def test_phase_close_flushes_open_streaming_block() -> None:
    renderer, buf = _make_renderer()
    # Open a streaming block
    renderer.emit_activity_line("u", "text", "streaming content")
    renderer.emit_phase_close("development", "development: result artifact present")
    out = buf.getvalue()
    # [content-end] must appear before [phase-close]
    assert "[content-end]" in out
    assert "[phase-close]" in out
    assert out.index("[content-end]") < out.index("[phase-close]")


def test_phase_close_sanitises_produced() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_phase_close("review", "[red]issues[/red]")
    out = buf.getvalue()
    assert "[red]" not in out
    assert "[/red]" not in out
    assert "issues" in out


def test_phase_close_no_ansi() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_phase_close("planning", "plan: 3 step(s), 1 risk(s)")
    assert "\x1b[" not in buf.getvalue()


def test_phase_close_tag_is_registered() -> None:
    assert "phase-close" in _TAGS
    assert _TAG_CATEGORY.get("phase-close") == "META"


def test_phase_close_trims_empty_produced() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_phase_close("planning", "")
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    line = lines[0]
    # Line should contain 'phase=planning' and end with the suffix
    assert "phase=planning" in line
    suffix = "(elapsed=0.0s, content_blocks=0, thinking_blocks=0, tool_calls=0, errors=0)"
    assert line.endswith(suffix), f"expected line to end with {suffix}, got: {line}"
