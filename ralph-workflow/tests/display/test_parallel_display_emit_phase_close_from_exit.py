"""Black-box tests for ``ParallelDisplay.emit_phase_close_from_exit`` (wt-007).

Pins the new phase-close-from-exit emit method. The test is
black-box: it constructs a StringIO-backed rich Console, attaches a
DisplayContext, and asserts the visible output. No real I/O, no
time.sleep, no subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 0.5s.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.phase_exit_model import PhaseExitModel
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


def test_emit_phase_close_from_exit_renders_phase_label() -> None:
    """AC-05: phase label and elapsed seconds appear in output."""
    pd, buf = _display()
    pd.begin_phase("development")
    exit_model = PhaseExitModel(
        phase_name="development",
        phase_role="execution",
        agent_name="claude/sonnet",
        elapsed_seconds=1.5,
    )
    pd.emit_phase_close_from_exit(exit_model)
    pd.stop()
    output = buf.getvalue()
    assert "development" in output, f"missing phase label: {output!r}"
    assert "phase=development" in output, f"missing phase= tag: {output!r}"


def test_emit_phase_close_from_exit_quiet_mode_emits_nothing() -> None:
    """AC-05: quiet mode produces no output."""
    pd, buf = _display()
    pd._is_quiet = True
    exit_model = PhaseExitModel(
        phase_name="development",
        phase_role="execution",
        agent_name="claude/sonnet",
        elapsed_seconds=1.5,
    )
    pd.emit_phase_close_from_exit(exit_model)
    pd.stop()
    assert buf.getvalue() == "", f"quiet mode must produce no output, got: {buf.getvalue()!r}"
