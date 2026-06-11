"""Tests for ANSI escape stripping in [run-start] and [run-end] blocks.

Consolidated onto ParallelDisplay."""

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


def test_ansi_escapes_in_run_start_are_stripped() -> None:
    pd, buf = _make_display()
    pd.emit_run_start(RunStartOrientation(legend_enabled=False))
    out = buf.getvalue()
    assert "\x1b[" not in out


def test_ansi_escapes_in_run_end_are_stripped() -> None:
    pd, buf = _make_display()
    pd.emit_run_end(phase="final")
    out = buf.getvalue()
    assert "\x1b[" not in out
