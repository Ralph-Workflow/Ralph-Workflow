"""Tests for ANSI/markup stripping in PlainLogRenderer - copy-paste safety."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.plain_renderer import PlainLogRenderer


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False)
    renderer = PlainLogRenderer(console)
    return renderer, buffer


def test_emit_log_line_strips_rich_markup() -> None:
    """emit_log_line should strip [bold]x[/] rich tags."""
    renderer, buffer = _make_renderer()
    renderer.emit_log_line("unit-1", "[bold]important[/bold] message")
    output = buffer.getvalue()
    assert "[bold]" not in output
    assert "[/bold]" not in output
    assert "important message" in output


def test_emit_log_line_strips_ansi_escape() -> None:
    """emit_log_line should strip ANSI color escape sequences."""
    renderer, buffer = _make_renderer()
    renderer.emit_log_line("unit-1", "\x1b[31mred text\x1b[0m")
    output = buffer.getvalue()
    assert "\x1b[" not in output
    assert "red text" in output


def test_emit_log_line_strips_mixed_markup_and_ansi() -> None:
    """emit_log_line should strip both rich markup and ANSI escapes."""
    renderer, buffer = _make_renderer()
    renderer.emit_log_line("unit-1", "\x1b[31m[bold]red bold[/bold]\x1b[0m")
    output = buffer.getvalue()
    assert "\x1b[" not in output
    assert "[bold]" not in output
    assert "[/bold]" not in output
    assert "red bold" in output


def test_emit_status_line_strips_rich_markup() -> None:
    """emit_status_line should strip rich markup from status."""
    renderer, buffer = _make_renderer()
    renderer.emit_status_line("unit-1", "[green]RUNNING[/green]")
    output = buffer.getvalue()
    assert "[green]" not in output
    assert "[/green]" not in output
    assert "status=RUNNING" in output


def test_emit_status_line_strips_ansi() -> None:
    """emit_status_line should strip ANSI escapes from status."""
    renderer, buffer = _make_renderer()
    renderer.emit_status_line("unit-1", "\x1b[32mSUCCESS\x1b[0m")
    output = buffer.getvalue()
    assert "\x1b[" not in output
    assert "status=SUCCESS" in output


def test_emit_artifact_strips_ansi() -> None:
    """emit_artifact should produce ANSI-free output."""
    renderer, buffer = _make_renderer()
    renderer.emit_artifact("plan", "Test plan summary")
    output = buffer.getvalue()
    assert "\x1b[" not in output
    assert "kind=plan" in output


def test_strip_markup_static_method() -> None:
    """PlainLogRenderer.strip_markup should remove rich tags."""
    assert PlainLogRenderer.strip_markup("[green]ok[/green]") == "ok"
    assert PlainLogRenderer.strip_markup("[bold]bold[/bold] text") == "bold text"
    assert PlainLogRenderer.strip_markup("plain text") == "plain text"


def test_strip_markup_handles_invalid_markup() -> None:
    """PlainLogRenderer.strip_markup should not raise on invalid markup."""
    result = PlainLogRenderer.strip_markup("[not-valid")
    assert result is not None
    assert isinstance(result, str)
