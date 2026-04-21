"""Tests for PlainLogRenderer.emit_activity_line kind-tagged output."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.plain_renderer import PlainLogRenderer


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None)
    return PlainLogRenderer(console), buf


def test_text_kind_emits_content_tag() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "hello")
    out = buf.getvalue()
    assert "[content][u]" in out
    assert "hello" in out
    assert "INFO" in out


def test_thinking_kind_emits_thinking_tag() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "thinking", "I think therefore I am")
    out = buf.getvalue()
    assert "[thinking][u]" in out
    assert "I think therefore I am" in out


def test_tool_use_kind_emits_tool_tag() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "tool_use", "bash")
    out = buf.getvalue()
    assert "[tool][u]" in out
    assert "bash" in out


def test_tool_result_kind_emits_tool_result_tag_and_success_level() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "tool_result", "output")
    out = buf.getvalue()
    assert "[tool-result][u]" in out
    assert "SUCCESS" in out


def test_error_kind_emits_error_tag_and_error_level() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "error", "something went wrong")
    out = buf.getvalue()
    assert "[error][u]" in out
    assert "ERROR" in out
    assert "something went wrong" in out


def test_ansi_escapes_in_content_are_stripped() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "\x1b[31mred text\x1b[0m")
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "red text" in out


def test_rich_markup_in_content_is_stripped() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "[bold]x[/bold]")
    out = buf.getvalue()
    assert "[bold]" not in out
    assert "[/bold]" not in out
    assert "x" in out


def test_condensed_ref_appended() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "hello", condensed_ref=".agent/raw/u.log")
    out = buf.getvalue()
    assert "[see .agent/raw/u.log]" in out


def test_raw_kind_maps_to_content_tag() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "raw", "some raw line")
    out = buf.getvalue()
    assert "[content][u]" in out


def test_unknown_kind_defaults_to_content_tag() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "totally_unknown_kind", "data")
    out = buf.getvalue()
    assert "[content][u]" in out


def test_emit_log_line_delegates_to_emit_activity_line() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_log_line("u", "legacy line")
    out = buf.getvalue()
    assert "[content][u]" in out
    assert "legacy line" in out
