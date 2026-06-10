"""Quiet-mode test: ParallelDisplay in is_quiet=True mode emits zero output.

The wt-007 consolidation enforces that all 8 short-circuit-capable
public emit methods on ParallelDisplay short-circuit when the
display is constructed with ``is_quiet=True``. The two methods that
route to subscribers (``emit_parsed_event``, ``emit_analysis_result``)
correctly stay un-guarded and are excluded from this assertion.
"""

from __future__ import annotations

import io

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.phase_lifecycle import PhaseExitModel
from ralph.display.plain_renderer import RunStartOrientation
from ralph.pipeline.worker_status import WorkerStatus


def _make_quiet_display() -> tuple[ParallelDisplay, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={"CI": "1"})
    return ParallelDisplay(ctx, is_quiet=True), buf


def test_quiet_mode_emits_nothing_for_lifecycle_methods() -> None:
    pd, buf = _make_quiet_display()
    orientation = RunStartOrientation()
    pd.emit_run_start(orientation)
    pd.begin_phase("planning")
    pd.emit_phase_close("planning", "artifacts")
    pd.emit_phase_close_from_exit(
        PhaseExitModel(
            phase_name="planning",
            phase_role="planning",
            agent_name="planner",
            elapsed_seconds=1.0,
        )
    )
    pd.emit_run_end(phase="final")
    pd.emit("unit-1", "test log line")
    pd.set_status("unit-1", WorkerStatus.RUNNING)
    pd.record_artifact_outcome("committed")
    assert buf.getvalue() == "", (
        f"Quiet mode must emit zero output, got:\n{buf.getvalue()!r}"
    )
