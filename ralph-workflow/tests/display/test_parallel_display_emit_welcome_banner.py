"""Black-box tests for ``ParallelDisplay.emit_welcome_banner`` (wt-007).

Pins the new welcome-banner emit method. The test is black-box: it
constructs a StringIO-backed rich Console, attaches a DisplayContext,
and asserts the visible output. No real I/O, no time.sleep, no
subprocess.

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


def test_emit_welcome_banner_renders_version_string() -> None:
    """AC-05: the version string and 'Ralph Workflow' both appear in output."""
    pd, buf = _display()
    pd.emit_welcome_banner(version="1.2.3")
    pd.stop()
    output = buf.getvalue()
    assert "1.2.3" in output, f"missing version '1.2.3': {output!r}"
    assert "Ralph Workflow" in output, f"missing 'Ralph Workflow' name: {output!r}"


def test_emit_welcome_banner_quiet_mode_emits_nothing() -> None:
    """AC-05: quiet mode produces no output."""
    pd, buf = _display()
    pd._is_quiet = True
    pd.emit_welcome_banner(version="1.2.3")
    pd.stop()
    assert buf.getvalue() == "", f"quiet mode must produce no output, got: {buf.getvalue()!r}"
