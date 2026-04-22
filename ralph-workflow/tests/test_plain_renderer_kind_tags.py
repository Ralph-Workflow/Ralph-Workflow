"""Tests for PlainLogRenderer.emit_activity_line kind-tagged output."""

from __future__ import annotations

import os
from io import StringIO
from unittest.mock import patch

from rich.console import Console

from ralph.display.content_condenser import condense_content
from ralph.display.long_content_summary import set_ai_summary_hook
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


# --- AI summary hook tests ---


def test_content_start_emits_ai_summary_line_when_provided() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line(
        "u",
        "text",
        "some content",
        condensed_flag=True,
        summary_line="First sentence.",
        ai_summary_line="AI generated summary",
    )
    out = buf.getvalue()
    assert "↳ ai-summary: AI generated summary" in out
    assert "↳ summary: First sentence." in out
    assert out.index("↳ summary:") < out.index("↳ ai-summary:")


def test_ai_summary_line_not_emitted_when_none() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line(
        "u",
        "text",
        "some content",
        condensed_flag=True,
        summary_line="First sentence.",
        ai_summary_line=None,
    )
    out = buf.getvalue()
    assert "↳ ai-summary:" not in out
    assert "↳ summary: First sentence." in out


def test_ai_summary_line_not_emitted_when_empty_string() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line(
        "u",
        "text",
        "some content",
        condensed_flag=True,
        summary_line="First sentence.",
        ai_summary_line="",
    )
    out = buf.getvalue()
    assert "↳ ai-summary:" not in out


# --- Streaming checkpoint tests ---


def test_streaming_checkpoint_every_20_fragments() -> None:
    renderer, buf = _make_renderer()
    for i in range(25):
        renderer.emit_activity_line("u", "text", f"frag{i:02d}")
    out = buf.getvalue()
    assert "[content-checkpoint#20]" in out


def test_streaming_checkpoint_every_4000_chars() -> None:
    renderer, buf = _make_renderer()
    # 3 distinct fragments of ~1500 chars: total 4500 chars, crosses 4000
    for i in range(3):
        renderer.emit_activity_line("u", "text", "a" * 1499 + str(i))
    out = buf.getvalue()
    assert "[content-checkpoint#" in out


def test_streaming_checkpoint_disabled_by_env() -> None:
    renderer, buf = _make_renderer()
    with patch.dict(os.environ, {"RALPH_STREAMING_CHECKPOINTS": "0"}):
        for i in range(25):
            renderer.emit_activity_line("u", "text", f"frag{i:02d}")
    out = buf.getvalue()
    assert "[content-checkpoint#" not in out


def test_streaming_checkpoint_clears_on_block_close() -> None:
    """After a block closes and re-opens, checkpoints reset."""
    renderer, buf = _make_renderer()
    # Open a block, accumulate 20 fragments (triggers checkpoint), close it
    for i in range(21):
        renderer.emit_activity_line("u", "text", f"frag{i:02d}")
    renderer.flush_blocks()
    buf.truncate(0)
    buf.seek(0)
    # Re-open with a non-streaming event that doesn't trigger reset, then text
    renderer.emit_activity_line("u", "text", "new block start")
    out = buf.getvalue()
    assert "[content-start]" in out
    assert "[content-checkpoint#" not in out


# --- Empty headline placeholder tests ---


def test_empty_headline_emits_placeholder_when_condensed() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line(
        "u",
        "text",
        "some content",
        condensed_flag=True,
        summary_line="",
    )
    out = buf.getvalue()
    assert "↳ summary: (no headline available)" in out


def test_empty_headline_emits_placeholder_line_not_dropped() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line(
        "u",
        "text",
        "some long condensed content",
        condensed_flag=True,
        summary_line="",
    )
    out = buf.getvalue()
    placeholder_count = out.count("↳ summary: (no headline available)")
    assert placeholder_count == 1


def test_none_summary_with_condensed_flag_emits_nothing() -> None:
    """summary_line=None means 'not applicable' — no placeholder even if condensed."""
    renderer, buf = _make_renderer()
    renderer.emit_activity_line(
        "u",
        "text",
        "content",
        condensed_flag=True,
        summary_line=None,
    )
    out = buf.getvalue()
    assert "↳ summary:" not in out


def test_none_summary_without_condensed_emits_nothing() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_activity_line(
        "u",
        "text",
        "content",
        condensed_flag=False,
        summary_line=None,
    )
    out = buf.getvalue()
    assert "↳ summary:" not in out


def test_hook_cleanup_between_tests() -> None:
    """Ensure global hook state doesn't leak between tests."""
    set_ai_summary_hook(None)
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "x" * 5000, ai_summary_line=None)
    out = buf.getvalue()
    assert "↳ ai-summary:" not in out


# --- Summary suppression / gating tests ---


def test_summary_disabled_env_suppresses_summary_line() -> None:
    """RALPH_LONG_CONTENT_SUMMARY=0 must yield no ↳ summary: line."""
    renderer, buf = _make_renderer()
    long_text = "First sentence. " * 300  # well above 4000 chars
    with patch.dict(os.environ, {"RALPH_LONG_CONTENT_SUMMARY": "0"}):
        visible, condensed, summary_line, _ai = condense_content(
            long_text, summary=True
        )
    assert condensed is True
    assert summary_line is None
    renderer.emit_activity_line(
        "u", "text", visible, condensed_flag=condensed, summary_line=summary_line
    )
    out = buf.getvalue()
    assert "↳ summary:" not in out


def test_sub_threshold_condensed_content_yields_no_summary() -> None:
    """Content between 400 and 4000 cells: condensed but no summary applicable."""
    renderer, buf = _make_renderer()
    text = "a" * 500  # above soft_limit(400) but below summary_threshold(4000)
    visible, condensed, summary_line, _ai = condense_content(text, summary=True)
    assert condensed is True
    assert summary_line is None
    renderer.emit_activity_line(
        "u", "text", visible, condensed_flag=condensed, summary_line=summary_line
    )
    out = buf.getvalue()
    assert "↳ summary:" not in out


def test_above_threshold_empty_headline_yields_placeholder() -> None:
    """Content above 4000-cell threshold with no extractable headline emits placeholder."""
    renderer, buf = _make_renderer()
    text = " " * 4100  # all spaces: no extractable headline, but cell_len > 4000
    visible, condensed, summary_line, _ai = condense_content(text, summary=True)
    assert condensed is True
    assert summary_line == "(no headline available)"
    renderer.emit_activity_line(
        "u", "text", visible, condensed_flag=condensed, summary_line=summary_line
    )
    out = buf.getvalue()
    assert "↳ summary: (no headline available)" in out


# --- Streaming end-of-block AI summary tests ---


def test_content_end_emits_ai_summary_when_hook_set() -> None:
    """Block close emits ↳ ai-summary: line after the [content-end] line."""
    renderer, buf = _make_renderer()
    set_ai_summary_hook(lambda text: "Block AI summary")
    try:
        with patch.dict(os.environ, {"RALPH_LONG_CONTENT_AI_SUMMARY": "1"}):
            # Accumulate > 4000 chars so should_summarize returns True
            for i in range(3):
                renderer.emit_activity_line("u", "text", "x" * 1499 + str(i))
            buf.truncate(0)
            buf.seek(0)
            renderer.flush_blocks()
    finally:
        set_ai_summary_hook(None)
    out = buf.getvalue()
    assert "[content-end][u]" in out
    assert "↳ ai-summary: Block AI summary" in out
    assert out.index("[content-end]") < out.index("↳ ai-summary:")


def test_content_end_no_ai_summary_when_hook_not_set() -> None:
    """Block close emits no ↳ ai-summary: line when hook is not registered."""
    renderer, buf = _make_renderer()
    set_ai_summary_hook(None)
    for _ in range(3):
        renderer.emit_activity_line("u", "text", "x" * 1500)
    buf.truncate(0)
    buf.seek(0)
    renderer.flush_blocks()
    out = buf.getvalue()
    assert "[content-end][u]" in out
    assert "↳ ai-summary:" not in out


def test_content_end_no_ai_summary_when_env_not_set() -> None:
    """Block close emits no ↳ ai-summary: line when env var is not set."""
    renderer, buf = _make_renderer()
    set_ai_summary_hook(lambda text: "should not appear")
    try:
        os.environ.pop("RALPH_LONG_CONTENT_AI_SUMMARY", None)
        for _ in range(3):
            renderer.emit_activity_line("u", "text", "x" * 1500)
        buf.truncate(0)
        buf.seek(0)
        renderer.flush_blocks()
    finally:
        set_ai_summary_hook(None)
    out = buf.getvalue()
    assert "[content-end][u]" in out
    assert "↳ ai-summary:" not in out
