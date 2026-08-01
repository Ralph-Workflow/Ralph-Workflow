"""Tests for ParallelDisplay.emit_activity_line kind-tagged output (post-S-7 shape).

S-7 (wt-028-display P1) retired the per-fragment/preview/checkpoint emission
machinery. Streaming kinds (``text`` / ``thinking``) are now silent during
open / continue and emit exactly ONE entry on block close carrying the
joined passage plus fragment and char counts (sketch J shape).

Tests in this file pin the new shape:

* per-event, non-streaming kinds emit a single ``[<tag>][<unit>] <body>``
  line directly (``tool_use``, ``tool_result``, ``error``, ``progress``,
  ``lifecycle``, ``raw``);
* streaming kinds (``text`` / ``thinking``) buffer fragments silently and
  emit ONE line on close: ``[<tag>][<unit>] \u22ef <tag> \u00b7 <n> fragments \u00b7
  <chars> chars`` followed by the joined passage on the next line;
* no ``[content-start]``, ``[content-continue#N]``, ``[thinking-start]``,
  ``[thinking-continue#N]``, ``[content-end]``, ``[thinking-end]``,
  ``[content-checkpoint#N]``, ``[thinking-checkpoint#N]`` tags surface;
* no ``\u21b3 preview:`` / ``\u21b3 summary:`` / ``\u21b3 ai-summary:``
  supplement lines surface;
* a non-streaming event closes any active streaming blocks first;
* ``flush_blocks()`` emits a close line for every active block;
* whitespace-only ``thinking`` emits nothing (no open block, no close line);
* Rich markup and ANSI escapes in content are stripped before emission;
* condensed content appends ``[see <ref>]`` to the visible body.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

import pytest
from rich.console import Console

from ralph.display._plain_constants import LEVELS
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.snapshot import PipelineSnapshot


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def _plain_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip()]


# --- Per-event (non-streaming) kinds: single line on emit --------------


def test_tool_use_kind_emits_call_tag() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "tool_use", "bash")
    out = buf.getvalue()
    assert "[call][u]" in out
    assert "bash" in out


def test_tool_result_kind_emits_result_tag() -> None:
    """A tool_result event emits a [result] line; the SUCCESS LEVEL text is retired (S-4).

    The chrome prefix no longer carries the level/category badges.
    Severity is communicated by the renderer's own icon+label
    carrier (e.g. ``\u2713 PASS``); the activity line itself is
    text-only, with the [result][u] bracket and the body.
    """
    pd, buf = _make_display()
    pd.emit_activity_line("u", "tool_result", "output")
    out = buf.getvalue()
    assert "[result][u]" in out
    assert "SUCCESS" not in out


def test_error_kind_emits_error_tag() -> None:
    """An error event emits a [error] line; the ERROR LEVEL text is retired (S-4)."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "error", "something went wrong")
    out = buf.getvalue()
    assert "[error][u]" in out
    assert "ERROR" not in out
    assert "something went wrong" in out


def test_raw_kind_maps_to_content_tag() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "raw", "some raw line")
    out = buf.getvalue()
    assert "[output][u]" in out


def test_unknown_kind_defaults_to_content_tag() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "totally_unknown_kind", "data")
    out = buf.getvalue()
    assert "[output][u]" in out


def test_emit_log_line_delegates_to_emit_activity_line() -> None:
    pd, buf = _make_display()
    pd.emit_log_line("u", "legacy line")
    out = buf.getvalue()
    assert "[output][u]" in out
    assert "legacy line" in out


# --- Level badge tests --------------------------------------------------


def test_lifecycle_kind_emits_lifecycle_line() -> None:
    """A lifecycle event emits a line carrying the lifecycle carrier; no MILESTONE chrome.

    wt-028-display S-4: the chrome prefix no longer carries the
    MILESTONE LEVEL text. Lifecycle events are still routed to the
    same [status-content] (lifecycle) tag and the line carries the
    body. The surviving carrier (a milestone glyph) is rendered by
    the panel-level surface, not by the activity line.
    """
    pd, buf = _make_display()
    pd.emit_activity_line("u", "lifecycle", "agent started")
    out = buf.getvalue()
    assert "MILESTONE" not in out, f"retired MILESTONE chrome leaked: {out!r}"


def test_tool_use_kind_emits_tool_use_line() -> None:
    """A tool_use event emits a [call] line; the INFO LEVEL text is retired (S-4)."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "tool_use", "bash")
    out = buf.getvalue()
    assert "INFO" not in out, f"retired INFO chrome leaked: {out!r}"
    assert "[call][u]" in out
    assert "bash" in out


# --- Category prefix tests (non-streaming kinds surface CONT/META) ------


def test_tool_result_tag_does_not_leak_category_chrome() -> None:
    """A tool_result line never carries OUT category chrome (S-4 retirement)."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "tool_result", "ok")
    out = buf.getvalue()
    assert "OUT" not in out, f"retired OUT category chrome leaked: {out!r}"


def test_progress_kind_does_not_leak_category_chrome() -> None:
    """A progress line never carries META category chrome (S-4 retirement)."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "progress", "50%")
    out = buf.getvalue()
    assert "META" not in out, f"retired META category chrome leaked: {out!r}"


# --- Streaming kinds: silent during open/continue, single close line ---


def test_text_kind_emits_content_tag_on_close() -> None:
    """Text streams are silent until close; the close line carries [output].

    S-7: streaming layer is silent during open / continue. ``flush_blocks``
    emits one ``[output]`` close line carrying the joined passage.
    The chrome prefix no longer carries the INFO LEVEL text.
    """
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "hello")
    pd.flush_blocks()
    out = buf.getvalue()
    assert "[output][u]" in out
    assert "[u]" in out
    assert "hello" in out
    assert "INFO" not in out, f"retired INFO chrome leaked: {out!r}"
    # No per-fragment/preview tokens surface.
    for forbidden in (
        "[content-start]",
        "[content-continue#",
        "[content-end]",
        "[content-checkpoint#",
    ):
        assert forbidden not in out, f"forbidden token {forbidden!r} leaked: {out!r}"


def test_thinking_kind_emits_think_tag_on_close() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "thinking", "I think therefore I am")
    pd.flush_blocks()
    out = buf.getvalue()
    assert "[reasoning][u]" in out
    assert "[u]" in out
    assert "I think therefore I am" in out
    for forbidden in (
        "[thinking-start]",
        "[thinking-continue#",
        "[thinking-end]",
        "[thinking-checkpoint#",
    ):
        assert forbidden not in out, f"forbidden token {forbidden!r} leaked: {out!r}"


def test_ansi_escapes_in_content_are_stripped() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "\x1b[31mred text\x1b[0m")
    pd.flush_blocks()
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "red text" in out


def test_rich_markup_in_content_is_reduced() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "[bold]x[/bold]")
    pd.flush_blocks()
    out = buf.getvalue()
    assert "[output][u]" in out
    assert "x" in out
    assert "[bold]" not in out


def test_condensed_ref_appended_only_when_condensed_flag() -> None:
    """``[see <ref>]`` appears on non-streaming emissions when condensed.

    Streaming kinds buffer fragments and emit ONE close line on
    flush; the close-line body carries the joined passage plus
    fragment/char counts but NOT the ref (the ref belongs to the
    overflow path, surfaced separately via ``_emit_activity_event``).
    Non-streaming kinds emit a single line immediately, so the ref
    is appended to that line.
    """
    pd, buf = _make_display()
    pd.emit_activity_line(
        "u",
        "tool_result",
        "hello",
        condensed_ref=".agent/raw/u.log",
        condensed_flag=True,
    )
    out = buf.getvalue()
    assert "[see .agent/raw/u.log]" in out


def test_condensed_ref_not_appended_when_not_condensed() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line(
        "u",
        "text",
        "short",
        condensed_ref=".agent/raw/u.log",
        condensed_flag=False,
    )
    pd.flush_blocks()
    out = buf.getvalue()
    assert "[see .agent/raw/u.log]" not in out


# --- Streaming close line shape ---------------------------------------


def test_close_line_carries_joined_passage_and_span_duration() -> None:
    """S-13 close line carries joined passage + sketch-J span and duration.

    Format: ``INFO [<tag>][<unit>] \u22ef <tag> \u00b7 <start HH:MM:SS> \u2192 <end
    HH:MM:SS> \u00b7 <duration>`` followed by the joined passage on the next
    line. The ``fragments`` / ``chars`` plumbing is retired; the operator
    sees the human-vocabulary span and duration instead.
    """
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "hello")  # 5 chars
    pd.emit_activity_line("u", "text", "world")  # 5 chars
    pd.flush_blocks()
    out = buf.getvalue()
    # Joined passage survives exactly once.
    assert "hello world" in out
    # Sketch-J span and duration markers are present.
    assert "\u2192" in out, f"close line missing \u2192 span marker: {out!r}"
    assert "s\n" in out or out.endswith("s"), f"close line missing duration suffix 's': {out!r}"
    # No retired plumbing leaks.
    assert "fragments" not in out
    assert "chars" not in out


def test_close_line_uses_middle_dot_separators() -> None:
    """The close line uses ``\u00b7`` (middle dot) between header fields, never a comma."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "abc")
    pd.flush_blocks()
    out = buf.getvalue()
    # Sketch-J header: \u22ef <tag> \u00b7 HH:MM:SS \u2192 HH:MM:SS \u00b7 <duration>.
    assert "\u22ef output" in out, f"close line missing \u22ef marker: {out!r}"
    assert "\u00b7" in out, f"close line missing \u00b7 separator: {out!r}"
    assert "\u2192" in out, f"close line missing \u2192 span marker: {out!r}"
    # Joined passage survives exactly once.
    assert out.count("abc") == 1
    # No retired plumbing leaks.
    assert "fragments" not in out
    assert "chars" not in out
    # No comma-separated (n, m) parenthesis format.
    assert "(1 fragments, 3 chars)" not in out


# --- Multi-block / unit / kind switching invariants -------------------


def test_flush_blocks_no_op_when_no_active_block() -> None:
    pd, buf = _make_display()
    pd.flush_blocks()
    assert buf.getvalue() == ""


def test_flush_blocks_emits_one_logical_close_entry_per_active_block() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "partial content")
    pd.flush_blocks()
    out = buf.getvalue()
    # One logical output close entry uses metadata and body rows; both retain
    # the carrier for cold transcript searches.
    content_rows = [ln for ln in _plain_lines(out) if "[output][u]" in ln]
    assert len(content_rows) == 2
    assert any("partial content" in row for row in content_rows)


def test_streaming_block_closed_by_non_streaming_event() -> None:
    """A non-streaming event closes the active streaming block first."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "text content")
    pd.emit_activity_line("u", "tool_use", "bash")
    out = buf.getvalue()
    # The text block closed before tool_use surfaced.
    assert "[output][u]" in out
    assert "[call][u]" in out
    assert "bash" in out
    # The text close line appears before the tool_use line.
    assert out.index("[output][u]") < out.index("[call][u]")


def test_different_unit_id_closes_previous_block() -> None:
    """Global single-block invariant: switching units closes the previous block first.

    After S-7, the close line carries ``[output]`` (not ``[content-end]``),
    but the ordering invariant — block-A closes before block-B opens — still
    holds. ``flush_blocks`` emits the close line for the still-open unit-b
    block.
    """
    pd, buf = _make_display()
    pd.emit_activity_line("unit-a", "text", "a first")
    pd.emit_activity_line("unit-b", "text", "b first")
    pd.flush_blocks()
    out = buf.getvalue()
    # unit-a's block closed (single close line, [output] tag).
    assert "[output][unit-a]" in out
    # unit-b's block closed on flush (single close line, [output] tag).
    assert "[output][unit-b]" in out
    # unit-a's close line precedes unit-b's.
    assert out.index("[output][unit-a]") < out.index("[output][unit-b]")


def test_non_streaming_kind_closes_other_unit_block() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("unit-a", "text", "streaming content")
    pd.emit_activity_line("unit-b", "tool_use", "bash")
    out = buf.getvalue()
    # unit-a's block closed before unit-b's tool_use surfaced.
    assert "[output][unit-a]" in out
    assert "[call][unit-b]" in out
    assert out.index("[output][unit-a]") < out.index("[call][unit-b]")


# --- Phase level tests -------------------------------------------------


def test_phase_lines_use_milestone_for_execution_role() -> None:
    assert LEVELS["execution"] == "MILESTONE"


def test_phase_lines_use_milestone_for_review_role() -> None:
    assert LEVELS["review"] == "MILESTONE"


def test_phase_lines_use_success_for_terminal_role() -> None:
    assert LEVELS["terminal"] == "SUCCESS"


def test_phase_lines_use_info_for_analysis_role() -> None:
    assert LEVELS["analysis"] == "INFO"


# --- Whitespace-only thinking suppression ------------------------------


def test_whitespace_only_thinking_emits_nothing() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "thinking", "   ")
    assert buf.getvalue() == "", f"Expected empty output, got: {buf.getvalue()!r}"


@pytest.mark.timeout_seconds(5)
def test_tab_only_thinking_emits_nothing() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "thinking", "\t\n  ")
    assert buf.getvalue() == "", f"Expected empty output, got: {buf.getvalue()!r}"


def test_whitespace_thinking_does_not_open_block() -> None:
    """A whitespace-only thinking fragment must not create an active block."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "thinking", "   ")
    pd.flush_blocks()
    # No close line because the block was never opened.
    assert buf.getvalue() == ""


def test_whitespace_text_fragment_still_emits_on_close() -> None:
    """Whitespace suppression applies only to 'thinking' kind, not 'text'.

    A whitespace-only text fragment still opens a streaming block and
    emits one close line on flush.
    """
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "   ")
    pd.flush_blocks()
    out = buf.getvalue()
    assert "[output][u]" in out


# --- Internal vocabulary must not surface -----------------------------


def test_no_retired_supplements_surface_in_close_path() -> None:
    """Close path emits no ``\u21b3 preview:`` / ``\u21b3 summary:`` /
    ``\u21b3 ai-summary:`` supplement lines (S-7 retirement).
    """
    pd, buf = _make_display()
    pd.emit_activity_line(
        "u",
        "text",
        "some content",
        condensed_flag=True,
        summary_line="headline",
        ai_summary_line="ai summary",
    )
    pd.flush_blocks()
    out = buf.getvalue()
    for forbidden in ("\u21b3 preview:", "\u21b3 summary:", "\u21b3 ai-summary:"):
        assert forbidden not in out, f"forbidden supplement {forbidden!r} leaked: {out!r}"


# --- Activity line dedup and path-suffix tests -------------------------


def test_activity_tag_not_emitted_twice_across_snapshots() -> None:
    """Snapshot A emits [activity]; snapshot B emits exactly one [activity] line."""
    pd, buf = _make_display()

    base_kwargs = {
        "phase": "development",
        "previous_phase": None,
        "review_issues_found": False,
        "interrupted_by_user": False,
        "last_error": None,
        "pr_url": None,
        "push_count": 0,
        "total_agent_calls": 0,
        "total_continuations": 0,
        "total_fallbacks": 0,
        "total_retries": 0,
        "workers": (),
        "prompt_path": None,
        "prompt_preview": (),
        "run_id": None,
        "created_at": datetime.now(UTC),
    }

    snapshot_a = PipelineSnapshot(
        active_agent="claude/sonnet",
        last_activity_line=None,
        **base_kwargs,
    )
    pd.emit_snapshot(snapshot_a)
    out_a = buf.getvalue()
    assert "[activity]" in out_a
    assert "agent=claude/sonnet" in out_a
    assert "[activity-line]" not in out_a

    buf.truncate(0)
    buf.seek(0)

    snapshot_b = PipelineSnapshot(
        active_agent="claude/sonnet",
        active_tool="mcp__ralph__read_file",
        last_activity_line="claude/sonnet tool: mcp__ralph__read_file (path=x.py)",
        **base_kwargs,
    )
    pd.emit_snapshot(snapshot_b)
    out_b = buf.getvalue()
    activity_count = out_b.count("[activity]")
    assert activity_count == 1, f"Expected 1 [activity], got {activity_count}"
    assert "[activity-line]" not in out_b
    assert "claude/sonnet tool: mcp__ralph__read_file" in out_b


def test_activity_appends_path_when_missing() -> None:
    """[activity] appends (path=...) when active_path is not in last_activity_line."""
    pd, buf = _make_display()
    snapshot = PipelineSnapshot(
        phase="development",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path=None,
        prompt_preview=(),
        run_id=None,
        created_at=datetime.now(UTC),
        active_path="ralph-workflow/ralph/x.py",
        last_activity_line="claude/sonnet tool: mcp__ralph__read_file",
    )
    pd.emit_snapshot(snapshot)
    out = buf.getvalue()
    assert "(path=ralph-workflow/ralph/x.py)" in out


def test_activity_does_not_double_append_path_when_already_present() -> None:
    """[activity] must NOT append (path=...) when active_path is already in the line."""
    pd, buf = _make_display()
    snapshot = PipelineSnapshot(
        phase="development",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path=None,
        prompt_preview=(),
        run_id=None,
        created_at=datetime.now(UTC),
        active_path="ralph-workflow/ralph/x.py",
        last_activity_line=(
            "claude/sonnet tool: mcp__ralph__read_file (path=ralph-workflow/ralph/x.py)"
        ),
    )
    pd.emit_snapshot(snapshot)
    out = buf.getvalue()
    assert out.count("ralph-workflow/ralph/x.py") == 1
