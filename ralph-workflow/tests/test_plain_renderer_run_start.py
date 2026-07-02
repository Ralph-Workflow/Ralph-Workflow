"""Tests for ParallelDisplay.emit_run_start (consolidated from PlainLogRenderer)."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display._plain_constants import TAG_CATEGORY, TAGS
from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def _orientation(**kwargs: object) -> RunStartOrientation:
    defaults: dict[str, object] = {
        "plan_present": False,
    }
    defaults.update(kwargs)
    return RunStartOrientation(**defaults)


def test_emit_run_start_prints_milestone_header() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(_orientation(plan_present=True))
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "expected at least one non-empty line"
    # The section rule "─── [run-start]" is now emitted first by ParallelDisplay.
    # The milestone header is the next non-empty line.
    milestone_line = next((ln for ln in lines if "MILESTONE META [run-start]" in ln), None)
    assert milestone_line is not None, (
        f"expected a line with 'MILESTONE META [run-start]', got: {lines!r}"
    )
    assert "Ralph Workflow run start" in milestone_line


def test_emit_run_start_emits_prompt_line_when_present() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(_orientation(prompt_path="PROMPT.md"))
    out = buf.getvalue()
    assert "[run-start] prompt=PROMPT.md" in out


def test_emit_run_start_omits_absent_fields() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(_orientation(workspace_root="/ws", plan_present=False))
    out = buf.getvalue()
    assert "developer=" not in out
    assert "reviewer=" not in out
    assert "iterations=" not in out
    assert "parallel=" not in out
    assert "verbosity=" not in out


def test_emit_run_start_includes_plan_present_flag() -> None:
    pd1, buf1 = _make_display()
    pd1.emit_run_start(_orientation(plan_present=True))
    assert "plan=ready" in buf1.getvalue()

    pd2, buf2 = _make_display()
    pd2.emit_run_start(_orientation(plan_present=False))
    assert "plan=absent" in buf2.getvalue()


def test_emit_run_start_includes_parallel_when_present() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(_orientation(parallel_max_workers=4))
    assert "parallel=max_workers=4" in buf.getvalue()


def test_emit_run_start_sanitises_rich_markup_in_path() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(_orientation(workspace_root="[bold]x[/bold]"))
    out = buf.getvalue()
    assert "[bold]" not in out
    assert "[/bold]" not in out
    assert "x" in out


def test_emit_run_start_no_ansi() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(
        _orientation(
            prompt_path="PROMPT.md",
            developer_agent="claude",
            developer_model="claude-3-5-sonnet",
            parallel_max_workers=2,
            plan_present=True,
            verbosity="verbose",
            workspace_root="/workspace",
        )
    )
    out = buf.getvalue()
    assert "\x1b[" not in out


def test_run_start_tag_is_registered() -> None:
    assert "run-start" in TAGS
    assert TAG_CATEGORY.get("run-start") == "META"


def test_emit_run_start_includes_verbosity_when_present() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(_orientation(verbosity="verbose"))
    out = buf.getvalue()
    assert "verbosity=verbose" in out


def test_emit_run_start_verbosity_omitted_when_none() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(_orientation(verbosity=None))
    out = buf.getvalue()
    assert "verbosity=" not in out


def test_emit_run_start_milestone_glyph_ascii_fallback() -> None:
    """RALPH_FORCE_ASCII=1 uses ASCII milestone glyph (* not ◆) in run-start header."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    pd = ParallelDisplay(make_display_context(console=console, env={"RALPH_FORCE_ASCII": "1"}))
    pd.emit_run_start(_orientation())
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "expected at least one non-empty line"
    milestone_line = next((ln for ln in lines if "Ralph Workflow run start" in ln), None)
    assert milestone_line is not None
    assert "[run-start] * Ralph Workflow run start" in milestone_line
    assert "◆" not in milestone_line


def test_emit_run_start_legend_format() -> None:
    """emit_run_start legend uses new pipe-separated format."""
    pd, buf = _make_display()
    pd.emit_run_start(_orientation())
    out = buf.getvalue()
    assert "levels: INFO|SUCCESS|WARN|ERROR|MILESTONE" in out
    assert "cats: META|CONT" in out
    assert "format: [tag][unit] message" in out


_COMPACT_MAX_RUN_START_LINES = 4


def _filter_run_start_content(lines: list[str]) -> list[str]:
    return [
        ln
        for ln in lines
        if "[run-start]" in ln and "legend" not in ln and "Ralph Workflow" not in ln
    ]


def test_emit_run_start_plan_verbosity_grouped_on_one_line() -> None:
    """Default mode: plan and verbosity share one [run-start] line."""
    pd, buf = _make_display()
    pd.emit_run_start(_orientation(plan_present=True, verbosity="verbose"))
    out = buf.getvalue()
    run_start_lines = _filter_run_start_content(out.splitlines())
    plan_line = next((ln for ln in run_start_lines if "plan=" in ln), None)
    assert plan_line is not None, "expected a line with plan= in default mode"
    assert "verbosity=" in plan_line, "verbosity= must be on the same line as plan="


def test_emit_run_start_prompt_workspace_grouped_on_one_line() -> None:
    """Default mode: prompt= and workspace= share one [run-start] line."""
    pd, buf = _make_display()
    pd.emit_run_start(_orientation(prompt_path="PROMPT.md", workspace_root="/workspace"))
    out = buf.getvalue()
    run_start_lines = _filter_run_start_content(out.splitlines())
    prompt_line = next((ln for ln in run_start_lines if "prompt=" in ln), None)
    assert prompt_line is not None, "expected a line with prompt= in default mode"
    assert "workspace=" in prompt_line, "workspace= must be on the same line as prompt="
