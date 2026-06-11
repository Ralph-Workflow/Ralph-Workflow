"""Black-box tests for ``ParallelDisplay.emit_metrics_table`` (wt-007).

Pins the new metrics-table emit method added in step 2 of the
consolidation. The test is black-box: it constructs a StringIO-backed
rich Console, attaches a DisplayContext, and asserts the visible
output. No real I/O, no time.sleep, no subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 0.5s.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import RALPH_THEME


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


def test_emit_metrics_table_empty_dict() -> None:
    """Empty metrics dict still renders a 'Pipeline Metrics' header."""
    pd, buf = _display()
    pd.emit_metrics_table({})
    pd.stop()
    output = buf.getvalue()
    assert "Pipeline Metrics" in output, (
        f"expected 'Pipeline Metrics' title in output, got: {output!r}"
    )
    assert "[metrics]" in output, f"expected section rule in output, got: {output!r}"


def test_emit_metrics_table_single_metric() -> None:
    """Single metric appears in output."""
    pd, buf = _display()
    pd.emit_metrics_table({"files_touched": 7})
    pd.stop()
    output = buf.getvalue()
    assert "files_touched" in output, f"missing key: {output!r}"
    assert "7" in output, f"missing value: {output!r}"


def test_emit_metrics_table_multiple_metrics() -> None:
    """Multiple metrics all appear in output."""
    pd, buf = _display()
    pd.emit_metrics_table({"files_touched": 7, "iterations": 3, "duration_s": 42})
    pd.stop()
    output = buf.getvalue()
    for key in ("files_touched", "iterations", "duration_s"):
        assert key in output, f"missing key {key!r} in {output!r}"
    for value in ("7", "3", "42"):
        assert value in output, f"missing value {value!r} in {output!r}"
