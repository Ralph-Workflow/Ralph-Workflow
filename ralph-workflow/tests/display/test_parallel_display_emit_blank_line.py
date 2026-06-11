"""Black-box tests for ``ParallelDisplay.emit_blank_line`` (wt-007).

Pins the new blank-line emit method added in step 7 of the
consolidation. The test is black-box: it constructs a StringIO-backed
rich Console, attaches a DisplayContext, and asserts the visible
output. No real I/O, no time.sleep, no subprocess.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def test_emit_blank_line_emits_a_single_newline() -> None:
    pd, buf = StringIO(), StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    pd.emit_blank_line()
    pd.stop()
    output = buf.getvalue()
    assert output == "\n", f"expected exactly one newline, got {output!r}"


def test_emit_blank_line_quiet_mode_emits_nothing() -> None:
    """Quiet mode suppresses blank-line output entirely."""
    pd_buf = StringIO()
    console = Console(file=pd_buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx, is_quiet=True)
    pd.emit_blank_line()
    pd.stop()
    assert pd_buf.getvalue() == "", (
        f"quiet mode should suppress blank line, got {pd_buf.getvalue()!r}"
    )
