from __future__ import annotations

import io

from rich.console import Console

from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def test_parallel_display_preserves_log_tags() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={"CI": "1"})
    pd = ParallelDisplay(ctx)

    pd.emit_run_start(RunStartOrientation())
    pd.emit_phase_close("planning", "artifacts")
    pd.emit_run_end(phase="final")
    pd.emit(unit_id="unit-1", line="test log line")

    text = buf.getvalue()
    assert "[run-start]" in text, (
        f"[run-start] marker missing from ParallelDisplay output:\n{text!r}"
    )
    assert "[phase-close]" in text, (
        f"[phase-close] marker missing from ParallelDisplay output:\n{text!r}"
    )
    assert "[run-end]" in text, f"[run-end] marker missing from ParallelDisplay output:\n{text!r}"
    assert "unit-1" in text, f"unit_id 'unit-1' missing from ParallelDisplay output:\n{text!r}"
