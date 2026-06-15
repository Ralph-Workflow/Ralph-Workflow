"""Black-box tests for ``ParallelDisplay.emit_renderable`` (wt-007).

Pins the new generic renderable emit method. The test is black-box:
it constructs a StringIO-backed rich Console, attaches a
DisplayContext, and asserts the visible output. No real I/O, no
time.sleep, no subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 0.5s.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.text import Text

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


def test_emit_renderable_prints_arbitrary_renderable() -> None:
    """AC-05: a custom Text marker is passed through to the console."""
    pd, buf = _display()
    pd.emit_renderable(Text("custom-text-marker-xyz"))
    pd.stop()
    output = buf.getvalue()
    assert "custom-text-marker-xyz" in output, f"missing arbitrary marker in output: {output!r}"


def test_emit_renderable_quiet_mode_emits_nothing() -> None:
    """AC-05: quiet mode produces no output."""
    pd, buf = _display()
    pd._is_quiet = True
    pd.emit_renderable(Text("custom-text-marker-xyz"))
    pd.stop()
    assert buf.getvalue() == "", f"quiet mode must produce no output, got: {buf.getvalue()!r}"
