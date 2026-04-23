"""Tests for PlainLogRenderer legend line in [run-start] block."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.plain_renderer import PlainLogRenderer, RunStartOrientation


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(file=buf, color_system=None, force_terminal=False, width=200, highlight=False)
    return PlainLogRenderer(console), buf


def test_run_start_emits_legend_line_by_default() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(RunStartOrientation())
    out = buf.getvalue()
    legend_lines = [
        ln for ln in out.splitlines() if "legend: LEVEL (INFO/SUCCESS/WARN/ERROR/MILESTONE)" in ln
    ]
    assert len(legend_lines) == 1
    assert "INFO META [run-start]" in legend_lines[0]


def test_run_start_legend_can_be_disabled() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(RunStartOrientation(legend_enabled=False))
    out = buf.getvalue()
    assert "legend: LEVEL" not in out
    assert "◆ Ralph run start" in out


def test_run_start_legend_contains_cat_format() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(RunStartOrientation())
    out = buf.getvalue()
    assert "CAT (META/CONT)" in out


def test_run_start_legend_contains_tag_format() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(RunStartOrientation())
    out = buf.getvalue()
    assert "[tag][unit] message" in out


def test_run_start_legend_appears_after_milestone_header() -> None:
    renderer, buf = _make_renderer()
    renderer.emit_run_start(RunStartOrientation())
    out = buf.getvalue()
    assert out.index("◆ Ralph run start") < out.index("legend: LEVEL")
