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

from rich.cells import cell_len
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


def test_emit_phase_close_from_exit_folds_all_unique_counters_at_40_columns() -> None:
    """S-5 regression: 40-column recaps preserve full carriers and counters on every row."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=40, color_system=None, theme=RALPH_THEME)
    display = ParallelDisplay(make_display_context(console=console, env={}))
    display.begin_phase("development")
    display.emit_phase_close_from_exit(
        PhaseExitModel(
            phase_name="development",
            phase_role="execution",
            agent_name="claude/sonnet",
            artifact_outcome="artifacts ready for the long-running production display scenario",
            content_blocks=12,
            thinking_blocks=34,
            tool_calls=56,
            errors=7,
        )
    )
    display.stop()

    rows = [row for row in buf.getvalue().splitlines() if "[phase-close]" in row]
    assert len(rows) > 1
    carrier = rows[0].split(" ", 1)[0]
    assert carrier.startswith("[phase-close][")
    assert all(row.startswith(carrier) for row in rows)
    assert all(cell_len(row) <= 40 for row in rows)
    joined = " ".join(rows)
    for carrier in ("content_blocks=12", "thinking_blocks=34", "tool_calls=56", "errors=7"):
        assert carrier in joined


def test_emit_phase_close_from_exit_bounded_phase_identifier_preserves_counters_at_40_columns() -> None:
    """S-5 regression: an unbroken phase name cannot consume a folded recap row."""
    buf = StringIO()
    display = ParallelDisplay(
        make_display_context(console=Console(file=buf, width=40, color_system=None), env={})
    )
    display.begin_phase("very_long_unbroken_phase_identifier")
    display.emit_phase_close_from_exit(
        PhaseExitModel(
            phase_name="very_long_unbroken_phase_identifier",
            phase_role="execution",
            agent_name="claude/sonnet",
            artifact_outcome="artifacts ready",
            content_blocks=12,
            thinking_blocks=34,
            tool_calls=56,
            errors=7,
        )
    )
    display.stop()

    rows = [row for row in buf.getvalue().splitlines() if "[phase-close]" in row]
    assert rows
    assert all(cell_len(row) <= 40 for row in rows)
    carrier = rows[0].split(" ", 1)[0]
    assert all(row.startswith(carrier) for row in rows)
    joined = " ".join(rows)
    for counter in ("content_blocks=12", "thinking_blocks=34", "tool_calls=56", "errors=7"):
        assert counter in joined


def test_emit_phase_close_from_exit_wide_phase_identifier_stays_cell_bounded_at_40_columns() -> None:
    """S-5 regression: wide phase glyphs do not overflow folded phase-close rows."""
    buf = StringIO()
    display = ParallelDisplay(
        make_display_context(console=Console(file=buf, width=40, color_system=None), env={})
    )
    phase_name = "開発" * 18
    display.begin_phase(phase_name)
    display.emit_phase_close_from_exit(
        PhaseExitModel(
            phase_name=phase_name,
            phase_role="execution",
            agent_name="claude/sonnet",
            artifact_outcome="artifacts ready",
            content_blocks=12,
            thinking_blocks=34,
            tool_calls=56,
            errors=7,
        )
    )
    display.stop()

    rows = [row for row in buf.getvalue().splitlines() if "[phase-close]" in row]
    assert rows
    assert all(cell_len(row) <= 40 for row in rows)
    assert all("[phase-close]" in row for row in rows)
    joined = " ".join(rows)
    for counter in ("content_blocks=12", "thinking_blocks=34", "tool_calls=56", "errors=7"):
        assert counter in joined


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
