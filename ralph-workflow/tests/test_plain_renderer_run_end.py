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
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0)
    out = buf.getvalue()
    milestone_line = next(
        (ln for ln in out.splitlines() if "[run-end]" in ln and "Ralph Workflow run end" in ln),
        None,
    )
    assert milestone_line is not None
    assert "Ralph Workflow run end" in milestone_line
    for forbidden in ("MILESTONE META", "INFO META"):
        assert forbidden not in milestone_line, (
            f"wt-028-display S-4: run-end header must not leak {forbidden!r} chrome; "
            f"got: {milestone_line!r}"
        )


def test_emit_run_end_emits_phase_and_elapsed() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=5)
    out = buf.getvalue()
    assert any("phase=" in ln for ln in out.splitlines()), f"Missing phase= in: {out}"
    assert any(re.search(r"elapsed=\d+(\.\d+)?s", ln) for ln in out.splitlines()), (
        f"Missing elapsed= in: {out}"
    )


def test_run_end_tag_is_registered_in_tags() -> None:
    assert "run-end" in TAGS


def test_run_end_tag_maps_to_meta_category() -> None:
    assert TAG_CATEGORY.get("run-end") == "META"


def test_emit_run_end_pr_url_none_omits_pr_line() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, pr_url=None)
    out = buf.getvalue()
    assert "pr=" not in out


def test_emit_run_end_pr_url_reduces_rich_markup() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(
        phase="complete",
        total_agent_calls=0,
        pr_url="[bold]https://github.com/test/repo/pull/123[/bold]",
    )
    out = buf.getvalue()
    assert "https://github.com/test/repo/pull/123" in out
    assert "[bold]" not in out


def test_emit_run_end_no_ansi_escapes() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(
        phase="complete",
        total_agent_calls=3,
        pr_url="https://github.com/test/repo/pull/1",
    )
    out = buf.getvalue()
    assert "\x1b[" not in out


def test_emit_run_end_flushes_open_streaming_block() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "partial content")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_run_end(phase="complete", total_agent_calls=0)
    out = buf.getvalue()
    assert "[output][u]" in out
    run_end_line = next(
        (ln for ln in out.splitlines() if "[run-end]" in ln and "Ralph Workflow run end" in ln),
        None,
    )
    assert run_end_line is not None
    assert out.index("[output][u]") < out.index(run_end_line)


def test_emit_run_end_includes_all_counter_lines() -> None:
    pd, buf = _make_display()
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


def test_emit_run_end_continuation_lines_carry_no_level_badge() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=1)
    out = buf.getvalue()
    body_lines = [
        ln
        for ln in out.splitlines()
        if ln and "Ralph Workflow run end" not in ln and "[run-end]" in ln
    ]
    assert body_lines, f"Expected at least one [run-end] continuation line; got: {out!r}"
    for ln in body_lines:
        for forbidden in ("INFO", "WARN", "ERROR", "MILESTONE", "META", "OUT"):
            assert f" {forbidden} " not in ln, (
                f"wt-028-display S-4: [run-end] continuation line must not carry "
                f"{forbidden!r} level chrome; got: {ln!r}"
            )


def test_emit_run_end_milestone_glyph_ascii_fallback() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    pd = ParallelDisplay(make_display_context(console=console, env={"RALPH_FORCE_ASCII": "1"}))
    pd.emit_run_end(phase="complete", total_agent_calls=0)
    out = buf.getvalue()
    milestone_line = next((ln for ln in out.splitlines() if "Ralph Workflow run end" in ln), None)
    assert milestone_line is not None
    assert "[run-end] * Ralph Workflow run end" in milestone_line
    assert "◆" not in milestone_line


def test_emit_run_end_exit_trigger_shown_in_default_mode_output() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, exit_trigger="completed")
    out = buf.getvalue()
    assert "exit=completed" in out


def test_emit_run_end_exit_trigger_none_omits_exit_field() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, exit_trigger=None)
    out = buf.getvalue()
    assert "exit=" not in out


def test_emit_run_end_outer_dev_iteration_shown_when_set() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, outer_dev_iteration=3)
    out = buf.getvalue()
    assert "dev_cycle=3" in out


def test_emit_run_end_outer_dev_iteration_none_omits_field() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(phase="complete", total_agent_calls=0, outer_dev_iteration=None)
    out = buf.getvalue()
    assert "dev_cycle=" not in out
