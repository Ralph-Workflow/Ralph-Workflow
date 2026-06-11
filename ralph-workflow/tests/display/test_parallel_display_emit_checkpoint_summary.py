"""Black-box tests for ``ParallelDisplay.emit_checkpoint_summary_table`` (wt-007).

Pins the new checkpoint-summary emit method added in step 2 of the
consolidation. The test is black-box: it constructs a StringIO-backed
rich Console, attaches a DisplayContext, and asserts the visible
output. No real I/O, no time.sleep, no subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 0.5s.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass
class _CheckpointSummaryOptions:
    phase: str
    budget_progress: Mapping[str, tuple[int, int]]


def _display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        width=120,
        color_system=None,
        theme=RALPH_THEME,
    )
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf


def test_emit_checkpoint_summary_phase_only() -> None:
    """Phase-only summary still renders the Phase row."""
    pd, buf = _display()
    pd.emit_checkpoint_summary_table(
        _CheckpointSummaryOptions(phase="planning", budget_progress={})
    )
    pd.stop()
    output = buf.getvalue()
    assert "Phase" in output, f"missing 'Phase' row: {output!r}"
    assert "planning" in output, f"missing phase value: {output!r}"


def test_emit_checkpoint_summary_phase_plus_one_counter() -> None:
    """Single counter row formatted as '{completed}/{cap}'."""
    pd, buf = _display()
    pd.emit_checkpoint_summary_table(
        _CheckpointSummaryOptions(
            phase="development",
            budget_progress={"iterations": (2, 5)},
        )
    )
    pd.stop()
    output = buf.getvalue()
    assert "iterations" in output, f"missing counter name: {output!r}"
    assert "2/5" in output, f"missing formatted counter: {output!r}"


def test_emit_checkpoint_summary_phase_plus_three_counters() -> None:
    """Three counter rows all formatted as '{completed}/{cap}'."""
    pd, buf = _display()
    pd.emit_checkpoint_summary_table(
        _CheckpointSummaryOptions(
            phase="review",
            budget_progress={
                "iterations": (1, 3),
                "tokens": (200, 1000),
                "files": (4, 7),
            },
        )
    )
    pd.stop()
    output = buf.getvalue()
    for key in ("iterations", "tokens", "files"):
        assert key in output, f"missing counter name {key!r}: {output!r}"
    for value in ("1/3", "200/1000", "4/7"):
        assert value in output, f"missing formatted counter {value!r}: {output!r}"
