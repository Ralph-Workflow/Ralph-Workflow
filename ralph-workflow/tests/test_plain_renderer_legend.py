"""Tests for run-start legend line in [run-start] block (consolidated onto ParallelDisplay)."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, color_system=None, force_terminal=False, width=200, highlight=False)
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def test_run_start_emits_legend_line_by_default() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(RunStartOrientation())
    out = buf.getvalue()
    legend_lines = [
        ln for ln in out.splitlines() if "legend: levels: INFO|SUCCESS|WARN|ERROR|MILESTONE" in ln
    ]
    assert len(legend_lines) == 1
    # wt-028-display S-4: the legend line is now bare — no leading
    # LEVEL or CAT badge in the chrome prefix. The line still
    # carries the [run-start] tag and the level/tag vocabulary the
    # renderer exposes to operators.
    assert "[run-start]" in legend_lines[0]


def test_run_start_legend_can_be_disabled() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(RunStartOrientation(legend_enabled=False))
    out = buf.getvalue()
    assert "legend: levels:" not in out
    assert "Ralph Workflow run start" in out


def test_run_start_legend_contains_tag_format() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(RunStartOrientation())
    out = buf.getvalue()
    # wt-028-display S-4: the legend enumerates the public tag
    # vocabulary; the retired META|CONT category chrome is gone.
    assert "tags: content|think|call|result|error" in out
    assert "[tag][unit] message" in out


def test_run_start_legend_appears_after_milestone_header() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(RunStartOrientation())
    out = buf.getvalue()
    assert out.index("Ralph Workflow run start") < out.index("legend: levels:")
