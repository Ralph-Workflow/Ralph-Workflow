"""Tests for ParallelDisplay.emit_run_end (consolidated from PlainLogRenderer)."""

from __future__ import annotations

import re
from io import StringIO

from rich.console import Console

from ralph.display._plain_constants import TAG_CATEGORY, TAGS
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def test_emit_run_end_emits_milestone_header() -> None:
    """emit_run_end emits a MILESTONE header line."""
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0)
    out = buf.getvalue()
    milestone_line = next(
        (ln for ln in out.splitlines() if "MILESTONE META [run-end]" in ln), None
    )
    assert milestone_line is not None
    assert "Ralph Workflow run end" in milestone_line


def test_emit_run_end_emits_phase_and_elapsed() -> None:
    """emit_run_end includes phase= and elapsed= lines."""
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=5)
    out = buf.getvalue()
    assert any("phase=" in ln for ln in out.splitlines()), f"Missing phase= in: {out}"
    assert any(re.search(r"elapsed=\d+(\.\d+)?s", ln) for ln in out.splitlines()), (
        f"Missing elapsed= in: {out}"
    )


def test_run_end_tag_is_registered_in_tags() -> None:
    """'run-end' is present in TAGS."""
    assert "run-end" in TAGS


def test_run_end_tag_maps_to_meta_category() -> None:
    """'run-end' maps to 'META' in TAG_CATEGORY."""
    assert TAG_CATEGORY.get("run-end") == "META"


def test_emit_run_end_pr_url_none_omits_pr_line() -> None:
    """When pr_url is None, no pr= line is emitted."""
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, pr_url=None)
    out = buf.getvalue()
    assert "pr=" not in out


def test_emit_run_end_pr_url_set_emits_sanitized_pr_line() -> None:
    """When pr_url is set, pr=<sanitized> line is emitted without Rich markup."""
    pd, buf = _make_display()
    pd.emit_run_end(
        phase="complete",
        total_agent_calls=0,
        pr_url="[bold]https://github.com/test/repo/pull/123[/bold]",
    )
    out = buf.getvalue()
    assert "[bold]" not in out
    assert "[/bold]" not in out
    assert "pr=https://github.com/test/repo/pull/123" in out


def test_emit_run_end_no_ansi_escapes() -> None:
    """emit_run_end output contains no ANSI escape sequences."""
    pd, buf = _make_display()
    pd.emit_run_end(
        phase="complete",
        total_agent_calls=3,
        pr_url="https://github.com/test/repo/pull/1",
    )
    out = buf.getvalue()
    assert "\x1b[" not in out


def test_emit_run_end_flushes_open_streaming_block() -> None:
    """Calling emit_run_end with an open streaming block closes it before emitting [run-end]."""
    pd, buf = _make_display()
    # Open a streaming block
    pd.emit_activity_line("u", "text", "partial content")
    buf.truncate(0)
    buf.seek(0)
    # emit_run_end should close the block first
    pd.emit_run_end(phase="complete", total_agent_calls=0)
    out = buf.getvalue()
    # [content-end] must appear before [run-end] MILESTONE header
    assert "[content-end]" in out
    assert "MILESTONE META [run-end]" in out
    assert out.index("[content-end]") < out.index("MILESTONE META [run-end]")


def test_emit_run_end_includes_all_counter_lines() -> None:
    """emit_run_end emits content_blocks, thinking_blocks, tool_calls, errors, agent_calls."""
    pd, buf = _make_display()
    # Add some activity first so counters are non-zero
    pd.emit_activity_line("u", "text", "content block 1")
    pd.emit_activity_line("u", "thinking", "thinking block 1")
    pd.emit_activity_line("u", "tool_use", "bash")
    pd.emit_activity_line("u", "error", "some error")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_run_end(phase="complete", total_agent_calls=7, pr_url=None)
    out = buf.getvalue()
    assert "content_blocks=1" in out
    assert "thinking_blocks=1" in out
    assert "tool_calls=1" in out
    assert "errors=1" in out
    assert "agent_calls=7" in out


def test_emit_run_end_continuation_lines_use_info_level() -> None:
    """All continuation lines after the MILESTONE header use INFO level."""
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=1)
    out = buf.getvalue()
    info_lines = [ln for ln in out.splitlines() if " INFO " in ln]
    # The continuation lines should all be INFO
    assert info_lines, "Expected at least one INFO continuation line"


def test_emit_run_end_milestone_glyph_ascii_fallback() -> None:
    """RALPH_FORCE_ASCII=1 uses ASCII milestone glyph (* not ◆) in run-end header."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"RALPH_FORCE_ASCII": "1"})
    )
    pd.emit_run_end(phase="complete", total_agent_calls=0)
    out = buf.getvalue()
    milestone_line = next(
        (ln for ln in out.splitlines() if "Ralph Workflow run end" in ln), None
    )
    assert milestone_line is not None
    assert "[run-end] * Ralph Workflow run end" in milestone_line
    assert "◆" not in milestone_line


def test_emit_run_end_exit_trigger_shown_in_wide_output() -> None:
    """exit_trigger='completed' is surfaced as exit=completed in wide mode output."""
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, exit_trigger="completed")
    out = buf.getvalue()
    assert "exit=completed" in out


def test_emit_run_end_exit_trigger_shown_in_compact_output() -> None:
    """exit_trigger='failed' is surfaced in compact mode output."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=50)
    pd = ParallelDisplay(make_display_context(console=console, env={"COLUMNS": "50"}))
    pd.emit_run_end(phase="failed", total_agent_calls=0, exit_trigger="failed")
    out = buf.getvalue()
    assert "failed" in out


def test_emit_run_end_exit_trigger_none_omits_exit_field() -> None:
    """When exit_trigger is None, no exit= field is emitted."""
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, exit_trigger=None)
    out = buf.getvalue()
    assert "exit=" not in out


def test_emit_run_end_outer_dev_iteration_shown_when_set() -> None:
    """outer_dev_iteration is surfaced as dev_cycle=N in wide mode output."""
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, outer_dev_iteration=3)
    out = buf.getvalue()
    assert "dev_cycle=3" in out


def test_emit_run_end_outer_dev_iteration_none_omits_field() -> None:
    """When outer_dev_iteration is None, no dev_cycle= field is emitted."""
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, outer_dev_iteration=None)
    out = buf.getvalue()
    assert "dev_cycle=" not in out
