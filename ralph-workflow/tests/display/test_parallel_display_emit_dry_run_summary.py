"""Black-box tests for ``ParallelDisplay.emit_dry_run_summary`` (wt-007).

Pins the new dry-run-summary emit method added in step 8 of the
consolidation. The test is black-box: it constructs a StringIO-backed
rich Console, attaches a DisplayContext, and asserts the visible
output. No real I/O, no time.sleep, no subprocess.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def _display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf


def test_emit_dry_run_summary_contains_header_and_phase() -> None:
    """The 'Dry run mode' header and the phase / iterations lines are present."""
    pd, buf = _display()
    pd.emit_dry_run_summary(phase="development", iterations=3)
    pd.stop()
    output = buf.getvalue()
    assert "Dry run mode" in output, f"missing header: {output!r}"
    assert "development" in output, f"missing phase: {output!r}"
    assert "3" in output, f"missing iteration count: {output!r}"


def test_emit_dry_run_summary_includes_details() -> None:
    """Extra details dict entries appear in output."""
    pd, buf = _display()
    pd.emit_dry_run_summary(
        phase="planning",
        iterations=2,
        details={"Workspace root": "/tmp/ralph-test"},
    )
    pd.stop()
    output = buf.getvalue()
    assert "Workspace root" in output, f"missing detail key: {output!r}"
    assert "/tmp/ralph-test" in output, f"missing detail value: {output!r}"
