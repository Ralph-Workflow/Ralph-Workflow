"""Tests for PlainLogRenderer.emit_activity_line kind-tagged output."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.plain_renderer import LEVELS, PlainLogRenderer


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return PlainLogRenderer(console), buf


def test_text_kind_emits_content_tag() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "hello")
    out = buf.getvalue()
    assert "[content" in out
    assert "[u]" in out
    assert "hello" in out
    assert "INFO" in out


def test_thinking_kind_emits_thinking_tag() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "thinking", "I think therefore I am")
    out = buf.getvalue()
    assert "[thinking" in out
    assert "[u]" in out
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


def test_condensed_ref_appended_only_when_condensed_flag() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line(
        "u", "text", "hello", condensed_ref=".agent/raw/u.log", condensed_flag=True
    )
    out = buf.getvalue()
    assert "[see .agent/raw/u.log]" in out


def test_condensed_ref_not_appended_when_not_condensed() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line(
        "u", "text", "short", condensed_ref=".agent/raw/u.log", condensed_flag=False
    )
    out = buf.getvalue()
    assert "[see .agent/raw/u.log]" not in out


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


# --- Level badge tests ---


def test_lifecycle_kind_emits_milestone_level() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "lifecycle", "agent started")
    out = buf.getvalue()
    assert "MILESTONE" in out


def test_tool_use_kind_emits_info_level() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "tool_use", "bash")
    out = buf.getvalue()
    assert "INFO" in out


# --- Category prefix tests ---


def test_content_tag_gets_cont_category() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "raw", "data")
    out = buf.getvalue()
    assert "CONT" in out


def test_tool_result_tag_gets_cont_category() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "tool_result", "ok")
    out = buf.getvalue()
    assert "CONT" in out


def test_progress_kind_gets_meta_category() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "progress", "50%")
    out = buf.getvalue()
    assert "META" in out


# --- Streaming block tests ---


def test_streaming_text_emits_content_start_on_first() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "first line")
    out = buf.getvalue()
    assert "[content-start]" in out


def test_streaming_text_emits_content_continue_on_second() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "first")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_activity_line("u", "text", "second")
    out = buf.getvalue()
    assert "[content-continue#" in out


def test_streaming_block_flushes_on_different_kind() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "text line")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_activity_line("u", "tool_use", "bash")
    out = buf.getvalue()
    assert "[content-end]" in out


def test_flush_blocks_emits_content_end() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "partial content")
    buf.truncate(0)
    buf.seek(0)
    renderer.flush_blocks()
    out = buf.getvalue()
    assert "[content-end]" in out


def test_thinking_kind_emits_thinking_start_on_first() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "thinking", "reasoning starts")
    out = buf.getvalue()
    assert "[thinking-start]" in out


def test_thinking_kind_emits_thinking_continue_on_subsequent() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "thinking", "first thought")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_activity_line("u", "thinking", "second thought")
    out = buf.getvalue()
    assert "[thinking-continue#" in out


def test_different_unit_id_closes_previous_block() -> None:
    """Global single-block invariant: switching units closes the previous block first."""
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("unit-a", "text", "a first")
    renderer.emit_activity_line("unit-b", "text", "b first")
    out = buf.getvalue()
    # unit-a starts a block
    assert "[content-start][unit-a]" in out
    # unit-b's emission closes unit-a's block before opening its own
    assert "[content-end][unit-a]" in out
    assert "[content-start][unit-b]" in out
    # unit-a's end must come before unit-b's start
    assert out.index("[content-end][unit-a]") < out.index("[content-start][unit-b]")


def test_switching_from_text_to_thinking_closes_text_block() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "text content")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_activity_line("u", "thinking", "thinking content")
    out = buf.getvalue()
    assert "[content-end]" in out
    assert "[thinking-start]" in out


def test_flush_blocks_no_op_when_no_active_block() -> None:
    renderer, buf = _make_renderer()
    renderer.flush_blocks()
    assert buf.getvalue() == ""


def test_non_streaming_kind_closes_other_unit_block() -> None:
    """Non-streaming events close all open blocks, even for different units."""
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("unit-a", "text", "streaming content")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_activity_line("unit-b", "tool_use", "bash")
    out = buf.getvalue()
    assert "[content-end][unit-a]" in out


# --- Phase level tests ---


def test_phase_lines_use_milestone_for_planning() -> None:
    assert LEVELS["planning"] == "MILESTONE"


def test_phase_lines_use_milestone_for_development() -> None:
    assert LEVELS["development"] == "MILESTONE"


def test_phase_lines_use_success_for_complete() -> None:
    assert LEVELS["complete"] == "SUCCESS"


def test_phase_lines_use_error_for_failed() -> None:
    assert LEVELS["failed"] == "ERROR"


# --- Streaming sequence number tests (Step 10) ---


def test_streaming_continue_second_emits_sequence_2() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "first")
    renderer.emit_activity_line("u", "text", "second")
    out = buf.getvalue()
    assert "[content-continue#2]" in out


def test_streaming_continue_third_emits_sequence_3() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "first")
    renderer.emit_activity_line("u", "text", "second")
    renderer.emit_activity_line("u", "text", "third")
    out = buf.getvalue()
    assert "[content-continue#3]" in out


def test_thinking_continue_has_sequence_number() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "thinking", "first thought")
    renderer.emit_activity_line("u", "thinking", "second thought")
    out = buf.getvalue()
    assert "[thinking-continue#2]" in out


def test_end_line_reports_fragment_and_char_counts() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "hello")  # 5 chars
    renderer.emit_activity_line("u", "text", "world")  # 5 chars
    buf.truncate(0)
    buf.seek(0)
    renderer.flush_blocks()
    out = buf.getvalue()
    assert "(2 fragments, 10 chars)" in out
